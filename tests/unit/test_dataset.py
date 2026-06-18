"""Tests for isda_p3.store.dataset (chunk 1.7) — the Decimal128 storage boundary.

The graded invariant (audit m2): values cross the parquet boundary as pyarrow
``Decimal128``, never float. ``368000`` and ``0.94`` must read back as the EXACT
same ``Decimal`` they went in as, ``13.6`` must not become ``13.600000000001``,
and a value needing more than the declared scale (6 dp) must FAIL LOUD rather than
round silently (CLAUDE.md §A — no silent failures, no number without provenance).
"""

from __future__ import annotations

from decimal import Decimal

import pyarrow as pa
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
from isda_p3.store.dataset import (
    DATASET_SCHEMA,
    append_rows,
    read_dataset,
    read_dataset_decimals,
    rows_to_table,
    to_dataset_row,
)

# --- builders ------------------------------------------------------------------

_BANK = Bank(
    id="barclays",
    name="Barclays",
    jurisdiction=Jurisdiction.UK,
    ir_url="https://home.barclays",
    p3dh_lei=None,
    number_locale="en_GB",
    reporting_currency="GBP",
)
_MAP = MappingDecision(
    method=MappingMethod.RULE,
    model=None,
    prompt_sha=None,
    prompt_version=None,
    matched_alias="cet1 ratio",
    confidence=Decimal("1"),
)
_BBOX = BBox(page=4, x0=10.0, y0=20.0, x1=30.0, y1=40.0)
_RUN_ID = "run-2025q4-001"
_AT = "2026-06-18T12:00:00Z"


def _result(
    code: str,
    value: str,
    confidence: str,
    *,
    bbox: BBox | None = _BBOX,
    unit: Unit = Unit.PERCENT,
    status: ValidationStatus = ValidationStatus.AUTO_PASSED,
    vbasis: tuple[CheckType, ...] = (CheckType.RATIO_IDENTITY,),
) -> ReconciliationResult:
    v = Decimal(value)
    prov = Provenance(
        bank_id="barclays",
        period=ReportingPeriod(2025, 4),
        source_url="https://home.barclays/p3.pdf",
        source_kind=SourceKind.PDF,
        engine=Engine.DOCLING,
        bbox=bbox,
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
        validation_basis=vbasis,
        status=status,
    )


@pytest.fixture
def tmp_dataset(tmp_path, monkeypatch):
    """Point the dataset store at an isolated tmp dir.

    ``Paths.ensure`` is stubbed to create only the tmp dataset dir, so a test never
    mkdirs the real repo ``data/`` tree (RAW/REGISTRY/...) on a clean CI runner.
    """
    dataset_dir = tmp_path / "dataset"
    monkeypatch.setattr(Paths, "DATASET_DIR", dataset_dir)
    monkeypatch.setattr(Paths, "DATASET", dataset_dir / "values.parquet")
    monkeypatch.setattr(
        Paths, "ensure", classmethod(lambda cls: dataset_dir.mkdir(parents=True, exist_ok=True))
    )
    return tmp_path


# --- to_dataset_row: pure converter --------------------------------------------


def test_to_dataset_row_maps_every_field():
    res = _result("KM1.5", "13.6", "0.94", unit=Unit.PERCENT)
    row = to_dataset_row(res, bank=_BANK, run_id=_RUN_ID, extracted_at=_AT)

    assert row.bank == "barclays"
    assert row.period == "2025Q4"
    assert row.jurisdiction is Jurisdiction.UK
    assert row.template is Template.KM1
    assert row.field == "KM1.5"
    assert row.value == Decimal("13.6")
    assert row.unit is Unit.PERCENT
    assert row.ecl_basis is EclBasis.NA
    assert row.floor_basis is FloorBasis.FINAL
    assert row.source_url == "https://home.barclays/p3.pdf"
    assert row.page == 4  # taken from provenance.bbox.page
    assert row.bbox == _BBOX
    assert row.confidence == Decimal("0.94")
    assert row.validation_basis == (CheckType.RATIO_IDENTITY,)
    assert row.status is ValidationStatus.AUTO_PASSED
    assert row.extracted_at == _AT
    assert row.run_id == _RUN_ID


def test_to_dataset_row_bbox_none_yields_null_page():
    res = _result("KM1.5", "13.6", "0.94", bbox=None)
    row = to_dataset_row(res, bank=_BANK, run_id=_RUN_ID, extracted_at=_AT)
    assert row.page is None
    assert row.bbox is None


def test_row_flattens_bbox_into_columns():
    res = _result("KM1.5", "13.6", "0.94")
    table = rows_to_table([to_dataset_row(res, bank=_BANK, run_id=_RUN_ID, extracted_at=_AT)])
    rec = table.to_pylist()[0]
    assert rec["page"] == 4
    assert rec["bbox_x0"] == 10.0
    assert rec["bbox_y0"] == 20.0
    assert rec["bbox_x1"] == 30.0
    assert rec["bbox_y1"] == 40.0
    assert rec["validation_basis"] == ["RATIO_IDENTITY"]
    assert rec["jurisdiction"] == "UK"
    assert rec["template"] == "KM1"
    assert rec["status"] == "AUTO_PASSED"


# --- THE NO-FLOAT TEST ---------------------------------------------------------


def test_no_float_value_and_confidence_are_exact_decimal128(tmp_dataset):
    res = _result("KM1.4", "368000", "0.94", unit=Unit.GBP_M)
    written = append_rows([res], bank=_BANK, run_id=_RUN_ID, extracted_at=_AT)
    assert written == 1

    # schema field types are decimal128, never float64
    assert pa.types.is_decimal(DATASET_SCHEMA.field("value").type)
    assert pa.types.is_decimal(DATASET_SCHEMA.field("confidence").type)
    assert not pa.types.is_floating(DATASET_SCHEMA.field("value").type)
    assert DATASET_SCHEMA.field("value").type == pa.decimal128(38, 6)
    assert DATASET_SCHEMA.field("confidence").type == pa.decimal128(38, 6)

    # the parquet on disk preserves the decimal types
    table = read_dataset()
    assert table.schema.field("value").type == pa.decimal128(38, 6)
    assert table.schema.field("confidence").type == pa.decimal128(38, 6)

    rec = read_dataset_decimals()[0]
    assert isinstance(rec["value"], Decimal)
    assert isinstance(rec["confidence"], Decimal)
    assert rec["value"] == Decimal("368000")
    assert rec["confidence"] == Decimal("0.94")


def test_ratio_value_round_trips_exactly(tmp_dataset):
    append_rows([_result("KM1.5", "13.6", "1.0")], bank=_BANK, run_id=_RUN_ID, extracted_at=_AT)
    rec = read_dataset_decimals()[0]
    assert rec["value"] == Decimal("13.6")
    # no float contamination: 13.6 must not become 13.6000000000001
    assert rec["value"] != Decimal("13.600000000001")
    assert str(rec["value"]) in {"13.6", "13.600000"}


# --- append is additive --------------------------------------------------------


def test_append_is_additive(tmp_dataset):
    batch = [_result(f"KM1.{i}", str(1000 * i), "0.99", unit=Unit.GBP_M) for i in range(1, 8)]
    assert append_rows(batch, bank=_BANK, run_id=_RUN_ID, extracted_at=_AT) == 7
    assert read_dataset().num_rows == 7
    assert append_rows(batch, bank=_BANK, run_id=_RUN_ID, extracted_at=_AT) == 7
    assert read_dataset().num_rows == 14


def test_bbox_none_round_trips_with_null_columns(tmp_dataset):
    res = _result("KM1.5", "13.6", "0.94", bbox=None)
    append_rows([res], bank=_BANK, run_id=_RUN_ID, extracted_at=_AT)
    rec = read_dataset().to_pylist()[0]
    assert rec["page"] is None
    assert rec["bbox_x0"] is None
    assert rec["bbox_y0"] is None
    assert rec["bbox_x1"] is None
    assert rec["bbox_y1"] is None
    # the decimal value still round-trips
    assert read_dataset_decimals()[0]["value"] == Decimal("13.6")


# --- no silent rounding: >6 dp fails loud --------------------------------------


def test_value_exceeding_scale_raises_not_rounds():
    res = _result("KM1.5", "0.1234567", "0.94")  # 7 dp > scale 6
    rows = [to_dataset_row(res, bank=_BANK, run_id=_RUN_ID, extracted_at=_AT)]
    with pytest.raises(ValueError, match="value"):
        rows_to_table(rows)


def test_confidence_exceeding_scale_raises_not_rounds():
    res = _result("KM1.5", "13.6", "0.9412345")  # 7 dp confidence
    rows = [to_dataset_row(res, bank=_BANK, run_id=_RUN_ID, extracted_at=_AT)]
    with pytest.raises(ValueError, match="confidence"):
        rows_to_table(rows)


# --- read errors loud when absent ----------------------------------------------


def test_read_dataset_missing_raises(tmp_dataset):
    with pytest.raises(FileNotFoundError):
        read_dataset()


def test_non_finite_value_fails_loud_named():
    # A NaN/Inf is not representable as decimal128 — it must be reported as an
    # offender, never silently dropped (and sNaN must not crash the diagnostic).
    res = _result("KM1.5", "13.6", "0.94")
    bad = res.field_value
    object.__setattr__(bad, "value", Decimal("NaN"))
    rows = [to_dataset_row(res, bank=_BANK, run_id=_RUN_ID, extracted_at=_AT)]
    with pytest.raises(ValueError, match="value"):
        rows_to_table(rows)


def test_append_rejects_mismatched_existing_schema(tmp_dataset):
    # An on-disk parquet whose value column is float64 must NOT be cast/coerced;
    # append refuses loudly rather than silently rounding a stray float.
    import pyarrow as pa
    import pyarrow.parquet as pq

    from isda_p3.config import Paths as P

    P.ensure()
    bad = pa.table({"value": [1.5]}, schema=pa.schema([("value", pa.float64())]))
    pq.write_table(bad, P.DATASET)
    with pytest.raises(ValueError, match="schema does not match"):
        append_rows([_result("KM1.5", "13.6", "0.94")], bank=_BANK, run_id=_RUN_ID, extracted_at=_AT)


def test_append_empty_results_is_noop(tmp_dataset):
    assert append_rows([], bank=_BANK, run_id=_RUN_ID, extracted_at=_AT) == 0
    with pytest.raises(FileNotFoundError):  # nothing written
        read_dataset()
