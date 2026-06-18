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
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .config import setup_logging
from .config_load import load_banks
from .extraction.docling_engine import DoclingEngine
from .extraction.engine import ExtractionEngine
from .mapping.llm import StructuredMapper
from .models import Bank, ReconciliationResult, ReportingPeriod, SourceKind, Template, Unit
from .pipeline import extract_template
from .reconcile.identities import load_tolerances, load_weights
from .store.dataset import append_rows


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


def load_bank(bank_id: str) -> Bank:
    """Resolve a bank id against ``banks.yaml`` or raise ``SystemExit`` naming the options."""
    banks = load_banks()
    for bank in banks:
        if bank.id == bank_id:
            return bank
    raise SystemExit(
        f"unknown bank id {bank_id!r} (known: {', '.join(b.id for b in banks)})"
    )


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
    mapper: StructuredMapper | None = None,
    tolerances: Mapping[str, Mapping[str, Decimal]] | None = None,
    weights: Mapping[str, Decimal] | None = None,
) -> list[ReconciliationResult]:
    """Run + print the slice for one (bank, template, period, pdf); optionally store.

    Engine/mapper/tolerances/weights are injectable so tests stay key-free and
    network-free. ``uuid4``/``utcnow`` are read here (the entrypoint), keeping the
    pipeline and store pure. Returns the reconciled results for the caller/tests.
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
        tolerances=tolerances,
        weights=weights,
        mapper=mapper,
    )

    for result in results:
        print(format_result(result))

    if store:
        append_rows(
            results,
            bank=bank,
            run_id=uuid.uuid4().hex,
            extracted_at=datetime.now(timezone.utc).isoformat(),
        )
    return results


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
    return parser


def main(argv: list[str] | None = None) -> None:
    """Entry point: parse args and dispatch the ``run`` subcommand."""
    setup_logging()
    args = _build_parser().parse_args(argv)

    if args.command == "run":
        run_command(
            bank=load_bank(args.bank),
            template=_parse_template(args.template),
            period=_parse_period(args.period),
            pdf_path=args.pdf,
            engine=DoclingEngine(),
            store=not args.no_store,
        )


if __name__ == "__main__":
    main()
