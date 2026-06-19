"""Tests for isda_p3.store.analytics (chunk 4.1) — peer_compare + trend.

Analytics reads the long-format dataset back and serves two benchmarking views.
The graded invariants (CLAUDE.md §A): values stay exact ``Decimal`` (never float),
every returned row carries provenance (source_url + page) and its ``status`` so a
FLAGGED figure is never silently mistaken for a validated one, and an absent dataset
raises loud rather than returning a misleading empty list.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from isda_p3.config import Paths
from isda_p3.models import (
    BBox,
    Bank,
    CheckType,
    EclBasis,
    Engine,
    FieldValue,
    FloorBasis,
    Jurisdiction,
    MappingDecision,
    MappingMethod,
    Provenance,
    ReconciliationResult,
    ReportingPeriod,
    SourceKind,
    Template,
    Unit,
    ValidationStatus,
)
from isda_p3.store.analytics import PeerRow, TrendPoint, peer_compare, trend
from isda_p3.store.dataset import append_rows

# --- builders ------------------------------------------------------------------

_MAP = MappingDecision(
    method=MappingMethod.RULE,
    model=None,
    prompt_sha=None,
    prompt_version=None,
    matched_alias="cet1 ratio",
    confidence=Decimal("1"),
)
_RUN_ID = "run-001"
_AT = "2026-06-19T00:00:00Z"


def _bank(bank_id: str, currency: str = "GBP") -> Bank:
    return Bank(
        id=bank_id,
        name=bank_id.title(),
        jurisdiction=Jurisdiction.UK,
        ir_url=f"https://{bank_id}.example",
        p3dh_lei=None,
        number_locale="en_GB",
        reporting_currency=currency,
    )


def _result(
    bank_id: str,
    period: ReportingPeriod,
    code: str,
    value: str,
    *,
    unit: Unit = Unit.PERCENT,
    confidence: str = "0.99",
    page: int = 4,
    status: ValidationStatus = ValidationStatus.AUTO_PASSED,
) -> ReconciliationResult:
    v = Decimal(value)
    prov = Provenance(
        bank_id=bank_id,
        period=period,
        source_url=f"https://{bank_id}.example/p3-{period.label}.pdf",
        source_kind=SourceKind.PDF,
        engine=Engine.DOCLING,
        bbox=BBox(page=page, x0=1.0, y0=2.0, x1=3.0, y1=4.0),
    )
    fv = FieldValue(
        template=Template.KM1,
        field_code=code,
        value=v,
        unit=unit,
        ecl_basis=EclBasis.NA,
        floor_basis=FloorBasis.FINAL,
        provenance=prov,
        mapping=_MAP,
        raw_text=value,
        engine_values={Engine.DOCLING: v},
    )
    return ReconciliationResult(
        field_value=fv,
        checks=(),
        confidence=Decimal(confidence),
        validation_basis=(CheckType.RATIO_IDENTITY,),
        status=status,
    )


@pytest.fixture
def tmp_dataset(tmp_path, monkeypatch):
    """Point the dataset store at an isolated tmp dir (mirrors test_dataset)."""
    dataset_dir = tmp_path / "dataset"
    monkeypatch.setattr(Paths, "DATASET_DIR", dataset_dir)
    monkeypatch.setattr(Paths, "DATASET", dataset_dir / "values.parquet")
    monkeypatch.setattr(
        Paths, "ensure", classmethod(lambda cls: dataset_dir.mkdir(parents=True, exist_ok=True))
    )
    return tmp_path


def _seed_peers(q: ReportingPeriod) -> None:
    """One KM1.5 row per bank in period ``q`` (values chosen to test desc sort)."""
    append_rows([_result("barclays", q, "KM1.5", "13.6")], bank=_bank("barclays"),
                run_id=_RUN_ID, extracted_at=_AT)
    append_rows([_result("hsbc", q, "KM1.5", "14.8")], bank=_bank("hsbc"),
                run_id=_RUN_ID, extracted_at=_AT)
    append_rows([_result("lloyds", q, "KM1.5", "13.6")], bank=_bank("lloyds"),
                run_id=_RUN_ID, extracted_at=_AT)


# --- peer_compare --------------------------------------------------------------


def test_peer_compare_one_row_per_bank_sorted_desc(tmp_dataset):
    q = ReportingPeriod(2025, 4)
    _seed_peers(q)
    rows = peer_compare("KM1.5", "2025Q4")

    assert all(isinstance(r, PeerRow) for r in rows)
    # one row per bank
    assert [r.bank for r in rows] == ["hsbc", "barclays", "lloyds"]
    # value descending, document tie-break (bank id asc) for the 13.6 pair
    assert [r.value for r in rows] == [Decimal("14.8"), Decimal("13.6"), Decimal("13.6")]
    # every row carries provenance + status
    for r in rows:
        assert r.source_url.endswith("p3-2025Q4.pdf")
        assert r.page == 4
        assert r.status is ValidationStatus.AUTO_PASSED
        assert r.unit is Unit.PERCENT
        assert isinstance(r.confidence, Decimal)


def test_peer_compare_values_are_exact_decimal_no_float(tmp_dataset):
    _seed_peers(ReportingPeriod(2025, 4))
    rows = peer_compare("KM1.5", "2025Q4")
    top = rows[0]
    assert isinstance(top.value, Decimal)
    assert top.value == Decimal("14.8")
    assert top.value != Decimal("14.80000000001")  # no float contamination


def test_peer_compare_excludes_flagged_by_default(tmp_dataset):
    q = ReportingPeriod(2025, 4)
    append_rows([_result("barclays", q, "KM1.5", "13.6")], bank=_bank("barclays"),
                run_id=_RUN_ID, extracted_at=_AT)
    append_rows(
        [_result("hsbc", q, "KM1.5", "99.9", status=ValidationStatus.FLAGGED)],
        bank=_bank("hsbc"), run_id=_RUN_ID, extracted_at=_AT,
    )
    rows = peer_compare("KM1.5", "2025Q4")
    assert [r.bank for r in rows] == ["barclays"]  # flagged hsbc excluded


def test_peer_compare_includes_flagged_only_when_requested(tmp_dataset):
    q = ReportingPeriod(2025, 4)
    append_rows([_result("barclays", q, "KM1.5", "13.6")], bank=_bank("barclays"),
                run_id=_RUN_ID, extracted_at=_AT)
    append_rows(
        [_result("hsbc", q, "KM1.5", "99.9", status=ValidationStatus.FLAGGED)],
        bank=_bank("hsbc"), run_id=_RUN_ID, extracted_at=_AT,
    )
    rows = peer_compare(
        "KM1.5", "2025Q4",
        statuses=(
            ValidationStatus.AUTO_PASSED,
            ValidationStatus.HUMAN_CONFIRMED,
            ValidationStatus.HUMAN_CORRECTED,
            ValidationStatus.FLAGGED,
        ),
    )
    assert [r.bank for r in rows] == ["hsbc", "barclays"]  # 99.9 sorts first
    flagged = next(r for r in rows if r.bank == "hsbc")
    assert flagged.status is ValidationStatus.FLAGGED  # never mistaken for validated


def test_peer_compare_no_match_is_empty_not_error(tmp_dataset):
    _seed_peers(ReportingPeriod(2025, 4))
    assert peer_compare("KM1.5", "2024Q4") == []  # wrong period
    assert peer_compare("OV1.1", "2025Q4") == []  # wrong field


def test_peer_compare_missing_dataset_raises(tmp_dataset):
    with pytest.raises(FileNotFoundError):
        peer_compare("KM1.5", "2025Q4")


def test_unrecognised_status_raises_not_silently_dropped(tmp_dataset, monkeypatch):
    # A corrupt status string in the ledger must fail loud, never vanish into an
    # empty result (CLAUDE.md §A — no silent failures).
    _seed_peers(ReportingPeriod(2025, 4))
    import isda_p3.store.analytics as analytics

    bad = [{"status": "GARBAGE", "bank": "x", "field": "KM1.5", "period": "2025Q4"}]
    monkeypatch.setattr(analytics, "read_dataset_decimals", lambda: bad)
    with pytest.raises(ValueError, match="Unrecognised status"):
        peer_compare("KM1.5", "2025Q4")


# --- trend ---------------------------------------------------------------------


def _seed_trend() -> None:
    """barclays KM1.5 across 2025Q1..Q4, ascending values 13.1, 13.3, 13.4, 13.6."""
    series = {1: "13.1", 2: "13.3", 3: "13.4", 4: "13.6"}
    # insert out of order to prove the function sorts, not the insert order
    for qn in (3, 1, 4, 2):
        q = ReportingPeriod(2025, qn)
        append_rows([_result("barclays", q, "KM1.5", series[qn])],
                    bank=_bank("barclays"), run_id=_RUN_ID, extracted_at=_AT)


def test_trend_ascending_period_order_with_delta(tmp_dataset):
    _seed_trend()
    points = trend("barclays", "KM1.5")

    assert all(isinstance(p, TrendPoint) for p in points)
    assert [p.period for p in points] == ["2025Q1", "2025Q2", "2025Q3", "2025Q4"]
    assert [p.value for p in points] == [
        Decimal("13.1"), Decimal("13.3"), Decimal("13.4"), Decimal("13.6")
    ]
    # first delta is None; rest are exact period-over-period Decimal changes
    assert points[0].delta is None
    assert points[1].delta == Decimal("0.2")
    assert points[2].delta == Decimal("0.1")
    assert points[3].delta == Decimal("0.2")
    for p in points[1:]:
        assert isinstance(p.delta, Decimal)
    # provenance + status on every point
    for p in points:
        assert p.source_url.endswith(".pdf")
        assert p.page == 4
        assert p.status is ValidationStatus.AUTO_PASSED
        assert p.unit is Unit.PERCENT


def test_trend_values_are_exact_decimal_no_float(tmp_dataset):
    _seed_trend()
    points = trend("barclays", "KM1.5")
    assert all(isinstance(p.value, Decimal) for p in points)
    assert points[-1].value == Decimal("13.6")
    assert points[1].delta == Decimal("0.2")
    assert points[1].delta != Decimal("0.2000000001")  # no float drift in delta


def test_trend_excludes_flagged_by_default(tmp_dataset):
    append_rows([_result("barclays", ReportingPeriod(2025, 1), "KM1.5", "13.1")],
                bank=_bank("barclays"), run_id=_RUN_ID, extracted_at=_AT)
    append_rows(
        [_result("barclays", ReportingPeriod(2025, 2), "KM1.5", "99.9",
                 status=ValidationStatus.FLAGGED)],
        bank=_bank("barclays"), run_id=_RUN_ID, extracted_at=_AT,
    )
    points = trend("barclays", "KM1.5")
    assert [p.period for p in points] == ["2025Q1"]


def test_trend_no_match_is_empty_not_error(tmp_dataset):
    _seed_trend()
    assert trend("nonexistent-bank", "KM1.5") == []
    assert trend("barclays", "OV1.1") == []


def test_trend_missing_dataset_raises(tmp_dataset):
    with pytest.raises(FileNotFoundError):
        trend("barclays", "KM1.5")
