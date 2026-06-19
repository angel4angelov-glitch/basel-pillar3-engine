"""Command-line entry point for isda-p3 (chunk 1.8).

The ``run`` subcommand wires the M1 vertical slice end-to-end:

    isda-p3 run --bank barclays --template KM1 --period 2025Q4 --pdf <path> [--no-store]

It loads the bank from config, builds a :class:`DoclingEngine`, runs
:func:`isda_p3.pipeline.extract_template`, prints one audit line per reconciled
field, and (unless ``--no-store``) appends the results to the Decimal128 parquet
store. The non-deterministic, environment-touching bits — ``uuid4`` run ids and the
``extracted_at`` timestamp — live *here* in the entrypoint, never in the pure
pipeline/store modules (so those stay reproducible and stub-testable).

``run_command`` takes the engine (and optional mapper/tolerances/weights) as
arguments so tests drive it with a stub engine, no network and no API key.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from .config import setup_logging
from .config_load import load_banks
from .extraction.docling_engine import DoclingEngine
from .extraction.engine import ExtractionEngine
from .extraction.secondary import select_secondary_engine
from .mapping.llm import StructuredMapper
from .models import (
    Bank,
    ReconciliationResult,
    ReportingPeriod,
    SourceKind,
    Template,
    Unit,
    ValidationStatus,
)
from .pipeline import NoTemplateTableError, extract_template
from .reconcile.identities import load_tolerances, load_weights
from .review.queue import enqueue_flagged, load_queue, render_item, resolve
from .store.dataset import append_rows

log = logging.getLogger(__name__)


# --- formatting ------------------------------------------------------------------


def _format_value(result: ReconciliationResult) -> str:
    """``13.6%`` for percents, ``47200 GBP_M`` for everything else."""
    fv = result.field_value
    if fv.unit is Unit.PERCENT:
        return f"{fv.value}%"
    return f"{fv.value} {fv.unit.value}"


def _format_checks(result: ReconciliationResult) -> str:
    """``RATIO_IDENTITY PASS`` per fired check; ``NO_CHECKS`` if none touched the field."""
    if not result.checks:
        return "NO_CHECKS"
    return ", ".join(f"{c.check_type.value} {c.outcome.value}" for c in result.checks)


def _format_bbox(result: ReconciliationResult) -> str:
    """``page 4 bbox(10,20,30,40)``; ``page NA bbox(NA)`` for a sourceless (XBRL) value."""
    bbox = result.field_value.provenance.bbox
    if bbox is None:
        return "page NA bbox(NA)"
    return f"page {bbox.page} bbox({bbox.x0:g},{bbox.y0:g},{bbox.x1:g},{bbox.y1:g})"


def format_result(result: ReconciliationResult) -> str:
    """One auditable line: field, value, basis, fired checks, source cell, map method, status.

    Example::

        KM1.5 Common Equity Tier 1 ratio (%) = 13.6% [ecl=NA floor=FINAL] | \
RATIO_IDENTITY PASS | page 4 bbox(1,0,2,1) | map=RULE | AUTO_PASSED
    """
    fv = result.field_value
    alias = fv.mapping.matched_alias or ""
    label = f"{fv.field_code} {alias}".rstrip()
    return (
        f"{label} = {_format_value(result)} "
        f"[ecl={fv.ecl_basis.value} floor={fv.floor_basis.value}] | "
        f"{_format_checks(result)} | "
        f"{_format_bbox(result)} | "
        f"map={fv.mapping.method.value} | "
        f"{result.status.value}"
    )


# --- run command -----------------------------------------------------------------


def find_bank(bank_id: str, banks: Iterable[Bank] | None = None) -> Bank | None:
    """Resolve a bank id against ``banks.yaml``, or ``None`` if absent (non-fatal lookup).

    Takes an optional pre-loaded ``banks`` iterable so a batch sweep resolves the roster
    once instead of re-reading the YAML per file.
    """
    banks = banks if banks is not None else load_banks()
    return next((b for b in banks if b.id == bank_id), None)


def load_bank(bank_id: str) -> Bank:
    """Resolve a bank id against ``banks.yaml`` or raise ``SystemExit`` naming the options."""
    banks = load_banks()
    bank = find_bank(bank_id, banks)
    if bank is None:
        raise SystemExit(
            f"unknown bank id {bank_id!r} (known: {', '.join(b.id for b in banks)})"
        )
    return bank


def _parse_template(value: str) -> Template:
    """Coerce a ``--template`` arg or exit cleanly (a bad code is user error, not a traceback)."""
    try:
        return Template(value)
    except ValueError:
        raise SystemExit(
            f"unknown template {value!r} (known: {', '.join(t.value for t in Template)})"
        ) from None


def _parse_period(value: str) -> ReportingPeriod:
    """Coerce a ``--period`` arg or exit cleanly with the expected format."""
    try:
        return ReportingPeriod.parse(value)
    except ValueError as exc:
        raise SystemExit(f"invalid period {value!r}: {exc}") from None


def run_command(
    *,
    bank: Bank,
    template: Template,
    period: ReportingPeriod,
    pdf_path: Path,
    engine: ExtractionEngine,
    store: bool,
    secondary_engine: ExtractionEngine | None = None,
    mapper: StructuredMapper | None = None,
    tolerances: Mapping[str, Mapping[str, Decimal]] | None = None,
    weights: Mapping[str, Decimal] | None = None,
) -> list[ReconciliationResult]:
    """Run + print the slice for one (bank, template, period, pdf); optionally store.

    Engine/secondary_engine/mapper/tolerances/weights are injectable so tests stay
    key-free and network-free. When ``secondary_engine`` is given the live two-engine
    cross-check runs (its agreement shows in each field's fired checks). ``uuid4``/
    ``utcnow`` are read here (the entrypoint), keeping the pipeline and store pure.
    Returns the reconciled results for the caller/tests.
    """
    tolerances = tolerances if tolerances is not None else load_tolerances()
    weights = weights if weights is not None else load_weights()

    results = extract_template(
        pdf_path,
        bank=bank,
        period=period,
        template=template,
        source_url=bank.ir_url,
        source_kind=SourceKind.PDF,
        engine=engine,
        secondary_engine=secondary_engine,
        tolerances=tolerances,
        weights=weights,
        mapper=mapper,
    )

    for result in results:
        print(format_result(result))

    if store:
        run_id = uuid.uuid4().hex
        append_rows(
            results,
            bank=bank,
            run_id=run_id,
            extracted_at=datetime.now(timezone.utc).isoformat(),
        )
        # Same run_id: flagged rows land in this run's review queue (box 6).
        enqueue_flagged(results, bank=bank, run_id=run_id)
    return results


# --- run-all command (config-driven batch) ---------------------------------------


@dataclasses.dataclass(frozen=True)
class BankRunResult:
    """One bank's outcome in a ``run-all`` sweep — counts, or an ``error`` if skipped."""

    bank_id: str
    n_auto_passed: int
    n_flagged: int
    error: str | None = None


def _parse_pdf_name(path: Path) -> tuple[str, str, str] | None:
    """Split ``<bank_id>_<period>_<template>.pdf`` -> ``(bank_id, period, template)``.

    Returns ``None`` for a name that is not at least three underscore-separated parts.
    Split from the RIGHT (``rsplit("_", 2)``): period (``2025Q4``) and template (``KM1``)
    are the final two tokens and carry no underscore, so the bank id is everything before
    them — a bank id that itself contains an underscore is preserved, not silently dropped.
    """
    parts = path.stem.rsplit("_", 2)
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


def run_all_command(
    *,
    template: Template,
    period: ReportingPeriod,
    pdf_dir: Path,
    engine: ExtractionEngine,
    store: bool,
    secondary_for: Callable[[Path], ExtractionEngine] | None = None,
    mapper: StructuredMapper | None = None,
    tolerances: Mapping[str, Mapping[str, Decimal]] | None = None,
    weights: Mapping[str, Decimal] | None = None,
) -> list[BankRunResult]:
    """Sweep every ``<bank_id>_<period>_<template>.pdf`` in ``pdf_dir`` for one sweep.

    Files whose encoded period/template differ from the requested sweep, or whose name
    does not match the convention, are skipped (logged, not errored). A bank id absent
    from ``banks.yaml`` is logged and recorded as a :class:`BankRunResult` with an
    ``error`` — it never crashes the batch (CLAUDE.md §A.5: a new bank is a config
    entry, and a missing one is a clear, contained failure). ``secondary_for`` builds
    the per-file secondary engine (``select_secondary_engine`` in the real CLI), so each
    bank gets a live two-engine cross-check. Returns one result per processed/known file.
    """
    tolerances = tolerances if tolerances is not None else load_tolerances()
    weights = weights if weights is not None else load_weights()
    banks = load_banks()

    summary: list[BankRunResult] = []
    for path in sorted(pdf_dir.glob("*.pdf")):
        parsed = _parse_pdf_name(path)
        if parsed is None:
            log.info("run-all: skipping %s — name is not <bank>_<period>_<template>.pdf", path.name)
            continue
        bank_id, file_period, file_template = parsed
        if file_period != period.label or file_template != template.value:
            log.info(
                "run-all: skipping %s — not this sweep (%s %s)",
                path.name,
                period.label,
                template.value,
            )
            continue

        bank = find_bank(bank_id, banks)
        if bank is None:
            log.warning(
                "run-all: unknown bank id %r from %s — skipping (add it to banks.yaml)",
                bank_id,
                path.name,
            )
            summary.append(BankRunResult(bank_id, 0, 0, error=f"unknown bank id {bank_id!r}"))
            continue

        try:
            # The secondary build (Camelot/Ghostscript probe) is inside the guard too:
            # a bad PDF must isolate to this bank, never abort the batch for the rest.
            secondary = secondary_for(path) if secondary_for is not None else None
            results = run_command(
                bank=bank,
                template=template,
                period=period,
                pdf_path=path,
                engine=engine,
                secondary_engine=secondary,
                store=store,
                mapper=mapper,
                tolerances=tolerances,
                weights=weights,
            )
        except NoTemplateTableError as exc:
            log.warning("run-all: %s yielded no usable %s table — skipping: %s",
                        path.name, template.value, exc)
            summary.append(BankRunResult(bank_id, 0, 0, error=str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 — batch isolation: one bad PDF must not
            # kill the sweep. The failure is loud (ERROR) AND summarised (BankRunResult
            # with error) — contained, never silent (CLAUDE.md §A.5).
            log.error("run-all: %s failed (%s) — skipping", path.name, exc, exc_info=True)
            summary.append(BankRunResult(bank_id, 0, 0, error=f"{type(exc).__name__}: {exc}"))
            continue

        n_pass = sum(1 for r in results if r.status is ValidationStatus.AUTO_PASSED)
        n_flag = sum(1 for r in results if r.status is ValidationStatus.FLAGGED)
        summary.append(BankRunResult(bank_id, n_pass, n_flag))

    _print_run_all_summary(summary, template=template, period=period)
    return summary


def _print_run_all_summary(
    summary: list[BankRunResult], *, template: Template, period: ReportingPeriod
) -> None:
    """Print the per-bank tally and a closing line listing any skipped (errored) banks."""
    print(f"\n=== run-all {template.value} {period.label}: {len(summary)} bank(s) ===")
    for res in summary:
        if res.error is not None:
            print(f"{res.bank_id}: SKIPPED ({res.error})")
        else:
            print(f"{res.bank_id}: {res.n_auto_passed} AUTO_PASSED / {res.n_flagged} FLAGGED")
    errored = sorted({r.bank_id for r in summary if r.error is not None})
    if errored:
        print(f"skipped {len(errored)} bank(s): {', '.join(errored)}")


# --- review command --------------------------------------------------------------


def review_list_command() -> None:
    """Print the value-beside-source-cell view for every queued item (newest first)."""
    items = load_queue()
    if not items:
        print("review queue is empty")
        return
    for item in items:
        print(render_item(item))
        print()


def review_resolve_command(
    *,
    run_id: str,
    field_code: str,
    confirm: bool,
    corrected_value: Decimal | None,
    note: str,
) -> None:
    """Confirm or correct one queued item and print the resulting status."""
    action: Literal["confirm", "correct"] = "confirm" if confirm else "correct"
    try:
        item = resolve(
            run_id, field_code, action=action, corrected_value=corrected_value, note=note
        )
    except (FileNotFoundError, KeyError) as exc:
        raise SystemExit(str(exc)) from None
    suffix = f" = {item.value} {item.unit.value}" if action == "correct" else ""
    print(f"{field_code}: {item.status.value}{suffix}")


# --- argument parsing ------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="isda-p3",
        description="Auditable Basel Pillar 3 disclosure extraction & benchmarking.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="extract one template from one PDF and reconcile it")
    run_p.add_argument("--bank", required=True, help="bank id from config/banks.yaml")
    run_p.add_argument("--template", required=True, help="template code, e.g. KM1")
    run_p.add_argument("--period", required=True, help="reporting period, e.g. 2025Q4 / 2025FY")
    run_p.add_argument("--pdf", required=True, type=Path, help="path to the disclosure PDF")
    run_p.add_argument(
        "--no-store", action="store_true", help="print results without writing the parquet store"
    )

    runall_p = sub.add_parser(
        "run-all",
        help="batch every <bank>_<period>_<template>.pdf in a dir (config-driven sweep)",
    )
    runall_p.add_argument("--template", required=True, help="template code, e.g. KM1")
    runall_p.add_argument("--period", required=True, help="reporting period, e.g. 2025Q4")
    runall_p.add_argument(
        "--pdf-dir", required=True, type=Path, dest="pdf_dir",
        help="dir of <bank_id>_<period>_<template>.pdf files",
    )
    runall_p.add_argument(
        "--no-store", action="store_true", help="print results without writing the parquet store"
    )

    review_p = sub.add_parser("review", help="triage the low-confidence review queue (box 6)")
    review_sub = review_p.add_subparsers(dest="review_command", required=True)
    review_sub.add_parser("list", help="print every queued flagged item, newest run first")

    resolve_p = review_sub.add_parser("resolve", help="confirm or correct one queued item")
    resolve_p.add_argument(
        "--run", required=True, dest="run_id", help="run id (the jsonl filename)"
    )
    resolve_p.add_argument(
        "--field", required=True, dest="field_code", help="field code, e.g. KM1.5"
    )
    action = resolve_p.add_mutually_exclusive_group(required=True)
    action.add_argument("--confirm", action="store_true", help="accept the extracted value as-is")
    action.add_argument(
        "--correct", type=Decimal, dest="corrected_value", metavar="VALUE",
        help="replace the value with this human-verified figure",
    )
    resolve_p.add_argument("--note", default="", help="free-text adjudication note (audit)")
    return parser


def main(argv: list[str] | None = None) -> None:
    """Entry point: parse args and dispatch the ``run`` or ``review`` subcommand."""
    setup_logging()
    args = _build_parser().parse_args(argv)

    if args.command == "run":
        run_command(
            bank=load_bank(args.bank),
            template=_parse_template(args.template),
            period=_parse_period(args.period),
            pdf_path=args.pdf,
            engine=DoclingEngine(),
            secondary_engine=select_secondary_engine(args.pdf),
            store=not args.no_store,
        )
    elif args.command == "run-all":
        run_all_command(
            template=_parse_template(args.template),
            period=_parse_period(args.period),
            pdf_dir=args.pdf_dir,
            engine=DoclingEngine(),
            secondary_for=select_secondary_engine,
            store=not args.no_store,
        )
    elif args.command == "review":
        if args.review_command == "list":
            review_list_command()
        elif args.review_command == "resolve":
            review_resolve_command(
                run_id=args.run_id,
                field_code=args.field_code,
                confirm=args.confirm,
                corrected_value=args.corrected_value,
                note=args.note,
            )


if __name__ == "__main__":
    main()
