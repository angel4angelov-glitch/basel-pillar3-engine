"""End-to-end gate for the M1 vertical slice (chunk 1.8) — no PDF, no LLM, no network.

A ``_StubEngine`` returns a synthetic KM1 grid (the committed golden under
``data/golden/expected/synthetic_km1.yaml``); the pipeline maps -> reconciles it,
and we assert: seven AUTO_PASSED fields whose values match the golden Decimal-exactly
with provenance, all three ratio identities PASS, the right table is picked over a
decoy, a missing KM1.4 SKIPs its identities (nothing fabricated), and a page with no
template table fails loud. This is the GATE: it must stay green and key-free.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from isda_p3.config import Paths
from isda_p3.config_load import load_template
from isda_p3.mapping.classify import select_template_table
from isda_p3.models import (
    BBox,
    Bank,
    CheckOutcome,
    CheckType,
    EclBasis,
    Engine,
    FloorBasis,
    Jurisdiction,
    RawCell,
    ReportingPeriod,
    SourceKind,
    Template,
    Unit,
    ValidationStatus,
)
from isda_p3.pipeline import NoTemplateTableError, extract_template
from isda_p3.reconcile.identities import load_tolerances, load_weights
from isda_p3.store.dataset import append_rows, read_dataset, read_dataset_decimals

# --- the synthetic table the stub engine returns (matches the golden yaml) --------

# (row label, current period, prior period). Current-period column is col 1; the
# mapper takes the first numeric cell right of the label = the current period.
_SYNTHETIC_KM1 = [
    ("Common Equity Tier 1 (CET1) capital", "48,000", "45,000"),
    ("Tier 1 capital", "56,000", "52,000"),
    ("Total capital", "64,000", "60,000"),
    ("Total risk-weighted assets (RWA)", "320,000", "310,000"),
    ("Common Equity Tier 1 ratio (%)", "15.0", "14.5"),
    ("Tier 1 ratio (%)", "17.5", "17.0"),
    ("Total capital ratio (%)", "20.0", "19.5"),
]

# A decoy income table with no KM1 labels — the selector must NOT pick it.
_DECOY = [
    ("Net interest income", "1,200", "1,100"),
    ("Operating expenses", "800", "790"),
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
_SPEC = load_template(Template.KM1)
_TOLS = load_tolerances()
_WEIGHTS = load_weights()
_PDF = Path("synthetic.pdf")  # never opened: the stub engine ignores the path


def _grid(rows: list[tuple[str, ...]]) -> list[RawCell]:
    """Build a left-to-right RawCell grid (label in col 0), bbox on page 4."""
    cells: list[RawCell] = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cells.append(
                RawCell(
                    row_idx=r,
                    col_idx=c,
                    text=text,
                    bbox=BBox(page=4, x0=float(c), y0=float(r), x1=float(c) + 1, y1=float(r) + 1),
                    engine=Engine.DOCLING,
                    row_label=None,
                    col_label=None,
                )
            )
    return cells


class _StubEngine:
    """Injected ExtractionEngine: returns canned table groups, never touches disk."""

    engine = Engine.DOCLING

    def __init__(self, groups: list[list[RawCell]]) -> None:
        self._groups = groups

    def extract_tables(self, pdf_path, pages=None) -> list[list[RawCell]]:
        return self._groups

    def extract(self, pdf_path, pages=None) -> list[RawCell]:
        return [c for g in self._groups for c in g]


def _run(groups: list[list[RawCell]]):
    return extract_template(
        _PDF,
        bank=_BANK,
        period=_PERIOD,
        template=Template.KM1,
        source_url=_BANK.ir_url,
        source_kind=SourceKind.PDF,
        engine=_StubEngine(groups),
        tolerances=_TOLS,
        weights=_WEIGHTS,
        mapper=None,
    )


def _by_code(results) -> dict:
    return {r.field_value.field_code: r for r in results}


@pytest.fixture
def tmp_dataset(tmp_path, monkeypatch):
    """Isolate the parquet store in a tmp dir (mirrors test_dataset.tmp_dataset)."""
    dataset_dir = tmp_path / "dataset"
    monkeypatch.setattr(Paths, "DATASET_DIR", dataset_dir)
    monkeypatch.setattr(Paths, "DATASET", dataset_dir / "values.parquet")
    monkeypatch.setattr(
        Paths, "ensure", classmethod(lambda cls: dataset_dir.mkdir(parents=True, exist_ok=True))
    )
    return tmp_path


def _golden() -> dict:
    path = Paths.GOLDEN_EXPECTED / "synthetic_km1.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# --- the happy path: clean KM1 -> 7 AUTO_PASSED rows matching golden ---------------


def test_clean_km1_all_seven_auto_passed():
    results = _run([_grid(_DECOY), _grid(_SYNTHETIC_KM1)])  # KM1 not first: order must not matter
    assert len(results) == 7
    assert all(r.status is ValidationStatus.AUTO_PASSED for r in results)
    assert {r.field_value.field_code for r in results} == {f"KM1.{i}" for i in range(1, 8)}


def test_all_three_ratio_identities_pass():
    by = _by_code(_run([_grid(_SYNTHETIC_KM1)]))
    for code in ("KM1.5", "KM1.6", "KM1.7"):
        ratio_checks = [c for c in by[code].checks if c.check_type is CheckType.RATIO_IDENTITY]
        assert ratio_checks, f"{code} should carry a ratio identity"
        assert all(c.outcome is CheckOutcome.PASS for c in ratio_checks)
        assert by[code].validation_basis == (CheckType.RATIO_IDENTITY,)


def test_values_match_golden_decimal_exactly():
    by = _by_code(_run([_grid(_SYNTHETIC_KM1)]))
    golden = _golden()["values"]
    assert set(by) == set(golden)
    for code, exp in golden.items():
        fv = by[code].field_value
        assert fv.value == Decimal(exp["value"]), code
        assert fv.unit is Unit(exp["unit"]), code
        assert fv.ecl_basis is EclBasis(exp["ecl_basis"]), code
        assert fv.floor_basis is FloorBasis(exp["floor_basis"]), code


def test_every_field_has_real_provenance():
    results = _run([_grid(_SYNTHETIC_KM1)])
    for r in results:
        bbox = r.field_value.provenance.bbox
        assert bbox is not None and bbox.page == 4
        assert r.field_value.provenance.source_url == _BANK.ir_url
        assert r.field_value.provenance.engine is Engine.DOCLING


# --- store round-trip: 7 Decimal-exact rows with provenance -----------------------


def test_append_and_read_back_seven_decimal_rows(tmp_dataset):
    results = _run([_grid(_DECOY), _grid(_SYNTHETIC_KM1)])
    written = append_rows(
        results, bank=_BANK, run_id="run-e2e-001", extracted_at="2026-06-18T00:00:00+00:00"
    )
    assert written == 7
    assert read_dataset().num_rows == 7

    rows = {rec["field"]: rec for rec in read_dataset_decimals()}
    golden = _golden()["values"]
    for code, exp in golden.items():
        rec = rows[code]
        assert isinstance(rec["value"], Decimal)
        assert rec["value"] == Decimal(exp["value"]), code  # Decimal-exact, never float
        assert rec["page"] == 4  # provenance survived the parquet boundary
        assert rec["source_url"] == _BANK.ir_url
        assert rec["status"] == "AUTO_PASSED"


# --- table selection: pick KM1 over the decoy, regardless of order ----------------


def test_select_template_table_picks_km1_over_decoy():
    km1 = _grid(_SYNTHETIC_KM1)
    assert select_template_table([_grid(_DECOY), km1], _SPEC) is km1
    assert select_template_table([km1, _grid(_DECOY)], _SPEC) is km1


def test_select_template_table_returns_none_when_no_match():
    assert select_template_table([_grid(_DECOY)], _SPEC) is None
    assert select_template_table([], _SPEC) is None


def test_pipeline_raises_when_no_template_table():
    with pytest.raises(NoTemplateTableError, match="no KM1 table found"):
        _run([_grid(_DECOY)])


def test_pipeline_raises_when_selected_table_maps_nothing():
    # Right labels (so the selector picks it) but every value cell is a dash → zero
    # fields map → empty result must fail loud, not look like a clean run (§A.2).
    labels_only = [(label, "-", "-") for label, *_ in _SYNTHETIC_KM1]
    with pytest.raises(NoTemplateTableError, match="no field mapped"):
        _run([_grid(labels_only)])


# --- missing KM1.4: identities SKIP, nothing fabricated ---------------------------


def test_missing_rwa_skips_identities_and_fabricates_nothing():
    no_rwa = [row for row in _SYNTHETIC_KM1 if "risk-weighted" not in row[0]]
    by = _by_code(_run([_grid(no_rwa)]))

    # KM1.4 was never extracted -> it is simply absent, never emitted as 0 (§A.2).
    assert "KM1.4" not in by
    assert {*by} == {"KM1.1", "KM1.2", "KM1.3", "KM1.5", "KM1.6", "KM1.7"}
    assert all(r.field_value.value != Decimal("0") for r in by.values())

    # Each ratio identity references the missing KM1.4 -> SKIP (missing input is not
    # a FAIL). The denominator-less ratios still appear, basis-tagged, unfabricated.
    for code in ("KM1.5", "KM1.6", "KM1.7"):
        ratio_checks = [c for c in by[code].checks if c.check_type is CheckType.RATIO_IDENTITY]
        assert ratio_checks and all(c.outcome is CheckOutcome.SKIP for c in ratio_checks)
        # A SKIP is NOT validation (chunk 1.9): a field whose only checks SKIPped was
        # never actually validated, so it FLAGS for human review — never auto-accepts
        # on a 0.97 skip product (§A.2 / audit C3). validation_basis is empty.
        assert by[code].status is ValidationStatus.FLAGGED
        assert by[code].validation_basis == ()
