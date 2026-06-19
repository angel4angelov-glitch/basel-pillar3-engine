"""GATE: live two-engine cross-check wired into the pipeline (chunk 4.3).

Stub PRIMARY (Docling) + stub SECONDARY (Camelot) engines, no PDF / LLM / network.
The pipeline runs both, maps each, ``merge_engine_values`` folds the secondary's
numbers into the primary FieldValues, and ``reconcile_template`` fires the
``TWO_ENGINE`` check live:

* both engines agree            -> TWO_ENGINE PASS on every field -> all AUTO_PASSED;
* secondary misreads one cell   -> that field's TWO_ENGINE FAILs -> only it FLAGGED;
* ``secondary_engine=None``     -> unchanged M1 behaviour (TWO_ENGINE SKIPs).

This is the chunk-4.3 GATE: it must stay green and key-free.
"""

from __future__ import annotations

from pathlib import Path

from isda_p3.models import (
    BBox,
    Bank,
    CheckOutcome,
    CheckType,
    Engine,
    Jurisdiction,
    RawCell,
    ReportingPeriod,
    SourceKind,
    Template,
    ValidationStatus,
)
from isda_p3.pipeline import extract_template
from isda_p3.reconcile.identities import load_tolerances, load_weights

# Same synthetic KM1 the M1 e2e gate uses: 48000/320000 = 15.0%, etc.
_SYNTHETIC_KM1 = [
    ("Common Equity Tier 1 (CET1) capital", "48,000", "45,000"),
    ("Tier 1 capital", "56,000", "52,000"),
    ("Total capital", "64,000", "60,000"),
    ("Total risk-weighted assets (RWA)", "320,000", "310,000"),
    ("Common Equity Tier 1 ratio (%)", "15.0", "14.5"),
    ("Tier 1 ratio (%)", "17.5", "17.0"),
    ("Total capital ratio (%)", "20.0", "19.5"),
]

_PERIOD = ReportingPeriod(2025, 4)
_BANK = Bank(
    id="synthetic",
    name="Synthetic Bank",
    jurisdiction=Jurisdiction.UK,
    ir_url="https://example.test/pillar3.pdf",
    p3dh_lei=None,
    number_locale="en_GB",
    reporting_currency="GBP",
)
_TOLS = load_tolerances()
_WEIGHTS = load_weights()
_PDF = Path("synthetic.pdf")  # never opened: the stub engines ignore the path


def _grid(rows: list[tuple[str, ...]], engine: Engine) -> list[RawCell]:
    """A left-to-right RawCell grid (label in col 0), tagged with ``engine``."""
    cells: list[RawCell] = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cells.append(
                RawCell(
                    row_idx=r,
                    col_idx=c,
                    text=text,
                    bbox=BBox(page=4, x0=float(c), y0=float(r), x1=float(c) + 1, y1=float(r) + 1),
                    engine=engine,
                    row_label=None,
                    col_label=None,
                )
            )
    return cells


class _StubEngine:
    """Injected ExtractionEngine returning a canned grid, tagged with its engine id."""

    def __init__(self, rows: list[tuple[str, ...]], engine: Engine) -> None:
        self.engine = engine
        self._groups = [_grid(rows, engine)]

    def extract_tables(self, pdf_path, pages=None) -> list[list[RawCell]]:
        return self._groups

    def extract(self, pdf_path, pages=None) -> list[RawCell]:
        return [c for g in self._groups for c in g]


def _run(primary_rows, secondary_rows):
    primary = _StubEngine(primary_rows, Engine.DOCLING)
    secondary = _StubEngine(secondary_rows, Engine.CAMELOT) if secondary_rows is not None else None
    return extract_template(
        _PDF,
        bank=_BANK,
        period=_PERIOD,
        template=Template.KM1,
        source_url=_BANK.ir_url,
        source_kind=SourceKind.PDF,
        engine=primary,
        secondary_engine=secondary,
        tolerances=_TOLS,
        weights=_WEIGHTS,
        mapper=None,
    )


def _by_code(results) -> dict:
    return {r.field_value.field_code: r for r in results}


# --- both engines agree: TWO_ENGINE fires PASS on every field --------------------


def test_two_agreeing_engines_all_auto_passed():
    by = _by_code(_run(_SYNTHETIC_KM1, _SYNTHETIC_KM1))
    assert set(by) == {f"KM1.{i}" for i in range(1, 8)}
    assert all(r.status is ValidationStatus.AUTO_PASSED for r in by.values())
    # the live two-engine check fired (non-SKIP) and passed on every field
    for code, res in by.items():
        assert CheckType.TWO_ENGINE in res.validation_basis, code
        te = [c for c in res.checks if c.check_type is CheckType.TWO_ENGINE]
        assert te and all(c.outcome is CheckOutcome.PASS for c in te), code


def test_two_agreeing_engines_merge_both_values():
    by = _by_code(_run(_SYNTHETIC_KM1, _SYNTHETIC_KM1))
    ev = by["KM1.4"].field_value.engine_values
    assert ev[Engine.DOCLING] == ev[Engine.CAMELOT]
    assert set(ev) == {Engine.DOCLING, Engine.CAMELOT}


# --- secondary misreads one cell: only that field FLAGGED ------------------------


def test_secondary_misread_flags_only_that_field():
    misread = [
        (label, "400,000", prior) if "risk-weighted" in label else (label, cur, prior)
        for label, cur, prior in _SYNTHETIC_KM1
    ]
    by = _by_code(_run(_SYNTHETIC_KM1, misread))

    # KM1.4: Docling 320000 vs Camelot 400000 -> TWO_ENGINE FAIL -> FLAGGED.
    assert by["KM1.4"].status is ValidationStatus.FLAGGED
    te = [c for c in by["KM1.4"].checks if c.check_type is CheckType.TWO_ENGINE]
    assert te and te[0].outcome is CheckOutcome.FAIL
    # The canonical (Docling) RWA is unchanged, so the ratio identities still PASS and
    # every other field auto-passes — the disagreement is per-field, not contagious.
    for code in ("KM1.1", "KM1.2", "KM1.3", "KM1.5", "KM1.6", "KM1.7"):
        assert by[code].status is ValidationStatus.AUTO_PASSED, code


# --- secondary=None: unchanged M1 behaviour (TWO_ENGINE SKIPs) -------------------


def test_no_secondary_unchanged_behaviour():
    by = _by_code(_run(_SYNTHETIC_KM1, None))
    assert all(r.status is ValidationStatus.AUTO_PASSED for r in by.values())
    for code, res in by.items():
        # The two-engine check still RUNS and is recorded in ``checks`` (for audit), but
        # with no second engine it SKIPs — and a SKIP never enters ``validation_basis``,
        # so it neither validates nor (here) blocks the identity-driven auto-pass.
        assert CheckType.TWO_ENGINE not in res.validation_basis, code
        te = [c for c in res.checks if c.check_type is CheckType.TWO_ENGINE]
        assert te and all(c.outcome is CheckOutcome.SKIP for c in te), code
        assert set(res.field_value.engine_values) == {Engine.DOCLING}, code
