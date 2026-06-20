"""Measure the deterministic stack vs the human golden on the REAL Barclays KM1 (chunk 5.1).

End-to-end, no rebuild — reuses the existing pipeline:
  registry PDF -> DoclingEngine (per page, real page_no) -> map -> reconcile,
then compares the extracted Decimals to data/golden/expected/barclays_2026Q1_km1.yaml
(built independently by human vision) and freezes the extracted cells to JSON so an
integration test reproduces the accuracy + identities with NO PDF and NO API key.

    python scripts/measure_barclays_km1.py

The KM1 table spans two pages (5 = capital/RWA/ratios, 6 = leverage/LCR/NSFR) with
different column structures, and select_template_table picks ONE group, so we pin the
engine to each page and merge — no multi-page stitching code, just two reuse calls.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path

from isda_p3.cli import load_bank
from isda_p3.config import Paths
from isda_p3.extraction.docling_engine import DoclingEngine
from isda_p3.golden import (
    AccuracyReport,
    CellOutcome,
    compare_to_golden,
    fixture_from_fieldvalues,
    load_golden,
    write_fixture,
)
from isda_p3.models import (
    Bank,
    CheckOutcome,
    CheckResult,
    CheckType,
    EclBasis,
    Engine,
    FieldValue,
    FloorBasis,
    RawCell,
    ReconciliationResult,
    ReportingPeriod,
    SourceKind,
    Template,
    Unit,
)
from isda_p3.pipeline import NoTemplateTableError, extract_template
from isda_p3.reconcile.checks import ratio_identity
from isda_p3.reconcile.engine import reconcile_template
from isda_p3.reconcile.identities import Operand, RatioIdentity, load_tolerances, load_weights

PDF = Paths.RAW / "Q126-BPLC-Pillar-3.pdf"
GOLDEN = Paths.GOLDEN_EXPECTED / "barclays_2026Q1_km1.yaml"
FIXTURE = Paths.GOLDEN_EXPECTED / "barclays_2026Q1_km1_cells.json"
PINNED_URL = (
    "https://home.barclays/content/dam/home-barclays/documents/investor-relations/"
    "ResultAnnouncements/Q12026Results/Q126-BPLC-Pillar-3.pdf"
)
SHA256 = "897e31da5cfc9ef27accf6f5c23ae826374e13db632400cdfb4dca4b7803a685"
KM1_PAGES = (5, 6)
TOL_BP = Decimal("10")
# The accuracy actually measured (14/15). A re-run that drops below this exits non-zero
# so a partial/regressed extraction can never be mistaken for a clean run (CLAUDE.md §A.2).
MEASURED_FLOOR = Decimal("14") / Decimal("15")


class PagePinnedEngine:
    """Adapter: make ``inner`` only ever see ``pages`` (real page_no preserved).

    Lets one extract_template call target one page of the two-page KM1 table without
    touching the pipeline. Not an extractor itself (CLAUDE.md §A.1) — it forwards to a
    real deterministic engine and never invents a cell.
    """

    def __init__(self, inner: DoclingEngine, pages: Sequence[int]) -> None:
        self.engine: Engine = inner.engine
        self._inner = inner
        self._pages = list(pages)

    # ``pages`` is part of the ExtractionEngine interface but is INTENTIONALLY ignored
    # here — the adapter's whole purpose is to pin the page set, so it always overrides
    # the caller's argument with ``self._pages``.
    def extract_tables(self, pdf_path: Path, pages: Sequence[int] | None = None) -> list[list[RawCell]]:
        return self._inner.extract_tables(pdf_path, pages=self._pages)

    def extract(self, pdf_path: Path, pages: Sequence[int] | None = None) -> list[RawCell]:
        return self._inner.extract(pdf_path, pages=self._pages)


def run_extraction(bank: Bank, period: ReportingPeriod) -> list[ReconciliationResult]:
    """Extract + reconcile KM1 page-by-page through the real pipeline, merged."""
    docling = DoclingEngine()
    tol, weights = load_tolerances(), load_weights()
    results: list[ReconciliationResult] = []
    for page in KM1_PAGES:
        print(f"  [docling] converting + extracting page {page} ...", flush=True)
        try:
            results += extract_template(
                PDF,
                bank=bank,
                period=period,
                template=Template.KM1,
                source_url=PINNED_URL,
                source_kind=SourceKind.PDF,
                engine=PagePinnedEngine(docling, [page]),
                tolerances=tol,
                weights=weights,
                mapper=None,
            )
        except NoTemplateTableError as exc:
            # Loud (stderr): a missing page is half the KM1 table gone; the accuracy
            # floor check in main() turns the resulting misses into a non-zero exit.
            print(f"  [docling] page {page}: NO KM1 table mapped — {exc}", file=sys.stderr)
    return results


# --- printing --------------------------------------------------------------------


def _fmt(v: Decimal, unit: Unit) -> str:
    return f"{v}%" if unit is Unit.PERCENT else f"{v} {unit.value}"


def print_fieldvalues(results: list[ReconciliationResult]) -> None:
    print("\n=== STEP 4 — extracted FieldValues with REAL provenance =================")
    print(f"{'CODE':<7}{'VALUE':>14} {'UNIT':<8}{'pg':>3} {'bbox (x0,y0,x1,y1)':<28}{'alias matched':<42}")
    print("-" * 110)
    for r in sorted(results, key=lambda r: (int(r.field_value.field_code.split('.')[1]))):
        fv = r.field_value
        b = fv.provenance.bbox
        bbox = f"({b.x0:.0f},{b.y0:.0f},{b.x1:.0f},{b.y1:.0f})" if b else "NA"
        page = b.page if b else "NA"
        alias = fv.mapping.matched_alias or "(llm)"
        print(f"{fv.field_code:<7}{str(fv.value):>14} {fv.unit.value:<8}{page:>3} {bbox:<28}{alias[:40]:<42}")
    print("-" * 110)
    print(f"{len(results)} FieldValues extracted | source: {PINNED_URL.split('/')[-1]} | sha {SHA256[:12]}…")


def print_accuracy(
    golden_values: Mapping[str, Decimal], extracted: Mapping[str, Decimal]
) -> AccuracyReport:
    report = compare_to_golden(golden_values, extracted)
    print("\n=== STEP 5 — MEASURED accuracy (Decimal-exact vs human golden) ==========")
    print(f"{'CODE':<7}{'GOLDEN':>14}{'EXTRACTED':>16}  OUTCOME")
    print("-" * 60)
    for c in sorted(report.comparisons, key=lambda c: int(c.field_code.split('.')[1])):
        g = "—" if c.golden is None else str(c.golden)
        e = "—" if c.extracted is None else str(c.extracted)
        mark = "ok " if c.outcome is CellOutcome.CORRECT else "!! "
        print(f"{c.field_code:<7}{g:>14}{e:>16}  {mark}{c.outcome.value}")
    print("-" * 60)
    acc_pct = (report.accuracy * 100).quantize(Decimal("0.1"))
    print(f"ACCURACY = {report.n_correct}/{report.n_golden}  ({acc_pct}%)   "
          f"[correct {report.n_correct} | mismatch {len(report.mismatches)} | "
          f"missing {len(report.missing)} | extra {len(report.extras)}]")
    if report.mismatches:
        print("  value-mismatches (golden vs extracted):")
        for c in report.mismatches:
            print(f"    {c.field_code}: golden {c.golden}  !=  extracted {c.extracted}")
    if report.missing:
        print(f"  missing (golden not extracted): {', '.join(c.field_code for c in report.missing)}")
    if report.extras:
        print(f"  extras (extracted not in golden): {', '.join(c.field_code for c in report.extras)}")
    return report


def _print_check(label: str, cr: CheckResult) -> None:
    print(f"  [{cr.outcome.value:<4}] {label:<26} {cr.detail}")


def print_reconcile(values: dict[str, FieldValue]) -> None:
    print("\n=== STEP 6 — reconciliation on the REAL extracted digits ================")
    tol, weights = load_tolerances(), load_weights()
    results = reconcile_template(values, Template.KM1, tolerances=tol, weights=weights)

    print("Committed KM1 identities (config/identities.yaml) on real digits:")
    fired = False
    for r in sorted(results, key=lambda r: int(r.field_value.field_code.split('.')[1])):
        for c in r.checks:
            if c.check_type is CheckType.RATIO_IDENTITY and c.field_codes[0] == r.field_value.field_code:
                _print_check(f"{c.check_type.value} {r.field_value.field_code}", c)
                fired = fired or c.outcome is not CheckOutcome.SKIP
    if not fired:
        print("  (no committed identity fired — operands absent)")

    # Diagnostic-only identities (NOT committed — leverage is requested by the chunk;
    # LCR/NSFR demonstrate WHY their averaged ratios are deliberately not reconciled).
    print("\nDiagnostic identities (run via the SAME engine fn; not in committed config):")
    diagnostics = [
        ("leverage  KM1.14=KM1.2/KM1.13",
         RatioIdentity(Operand("KM1.14", EclBasis.NA, FloorBasis.NA),
                       Operand("KM1.2", EclBasis.TRANSITIONAL, FloorBasis.NA),
                       Operand("KM1.13", EclBasis.NA, FloorBasis.NA), Decimal("100"))),
        ("LCR  KM1.17=KM1.15/KM1.16",
         RatioIdentity(Operand("KM1.17", EclBasis.NA, FloorBasis.NA),
                       Operand("KM1.15", EclBasis.NA, FloorBasis.NA),
                       Operand("KM1.16", EclBasis.NA, FloorBasis.NA), Decimal("100"))),
        ("NSFR  KM1.20=KM1.18/KM1.19",
         RatioIdentity(Operand("KM1.20", EclBasis.NA, FloorBasis.NA),
                       Operand("KM1.18", EclBasis.NA, FloorBasis.NA),
                       Operand("KM1.19", EclBasis.NA, FloorBasis.NA), Decimal("100"))),
    ]
    for label, ident in diagnostics:
        cr = ratio_identity(values, ident, TOL_BP)
        _print_check(label, cr)
    print("  NB: LCR (row 17) is a 12-month average and NSFR (row 20) a 4-quarter average")
    print("      of spot ratios; their spot components are NOT a clean numerator/denominator,")
    print("      so they are intentionally excluded from the committed reconciliation.")
    print("  Unit sanity is enforced upstream in mapping.normalise (e.g. a '%' on a monetary")
    print("      field is rejected); a standalone PERIOD_SANITY check is not yet implemented.")


def main() -> int:
    if not PDF.exists():
        print(
            f"PDF not found at {PDF}. Fetch it first:\n"
            f"  python scripts/fetch_barclays_pillar3.py\n",
            file=sys.stderr,
        )
        return 1

    bank = load_bank("barclays")
    period = ReportingPeriod(2026, 1)
    print(f"Bank {bank.name} ({bank.jurisdiction}, {bank.reporting_currency}) | KM1 | period {period.label}")

    results = run_extraction(bank, period)
    if not results:
        print("\nEXTRACTION PRODUCED ZERO FIELDVALUES — refusing to report '100% of 0'.", file=sys.stderr)
        # still run the comparator so the 0/N is printed loudly, then fail.
        golden = load_golden(GOLDEN)
        print_accuracy(golden.values, {})
        return 1

    print_fieldvalues(results)

    values = {r.field_value.field_code: r.field_value for r in results}
    extracted = {code: fv.value for code, fv in values.items()}
    golden = load_golden(GOLDEN)
    report = print_accuracy(golden.values, extracted)
    print_reconcile(values)

    # Freeze the extracted cells so CI reproduces this WITHOUT the PDF.
    fixture = fixture_from_fieldvalues(
        (r.field_value for r in results),
        bank=golden.bank, period=golden.period, template=golden.template,
        source_url=PINNED_URL, sha256=SHA256,
    )
    write_fixture(fixture, FIXTURE)
    print(f"\nfroze {len(fixture.cells)} extracted cells -> {FIXTURE.relative_to(Paths.ROOT)}")

    # Exit non-zero if accuracy regressed below the measured floor (a partial-page or
    # mapping regression) so a re-run can never silently report a degraded extraction.
    if report.accuracy < MEASURED_FLOOR:
        print(
            f"\nMEASURED ACCURACY {report.n_correct}/{report.n_golden} "
            f"BELOW FLOOR ({MEASURED_FLOOR * 100:.1f}%) — exiting non-zero.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
