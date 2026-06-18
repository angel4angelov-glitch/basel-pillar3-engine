"""Tests for isda_p3.models — frozen dataclasses + StrEnums (chunk 0.3).

The domain model is the spine; these tests pin its two load-bearing invariants:
immutability (every value object is frozen) and Decimal fidelity (no float
coercion of monetary/ratio values). Plus the orthogonal ECL/floor basis axes.
"""

import dataclasses
from decimal import Decimal

import pytest

from isda_p3.models import (
    Bank,
    BBox,
    CheckOutcome,
    CheckResult,
    CheckType,
    DatasetRow,
    EclBasis,
    Engine,
    FieldValue,
    FloorBasis,
    Jurisdiction,
    ManifestRow,
    MappingDecision,
    MappingMethod,
    Provenance,
    RawCell,
    ReconciliationResult,
    ReportingPeriod,
    SourceKind,
    Template,
    Unit,
    ValidationStatus,
)

# --- builders (smallest valid instances) -----------------------------------------


def _bbox() -> BBox:
    return BBox(page=4, x0=10.0, y0=20.0, x1=120.0, y1=32.0)


def _period() -> ReportingPeriod:
    return ReportingPeriod(2025, 4)


def _provenance() -> Provenance:
    return Provenance(
        bank_id="barclays",
        period=_period(),
        source_url="https://example.com/p3.pdf",
        source_kind=SourceKind.PDF,
        engine=Engine.DOCLING,
        bbox=_bbox(),
    )


def _mapping_rule() -> MappingDecision:
    return MappingDecision(
        method=MappingMethod.RULE,
        model=None,
        prompt_sha=None,
        prompt_version=None,
        matched_alias="Common Equity Tier 1 capital",
        confidence=Decimal("1.0"),
    )


def _field_value() -> FieldValue:
    return FieldValue(
        template=Template.KM1,
        field_code="KM1.5",
        value=Decimal("13.6"),
        unit=Unit.PERCENT,
        ecl_basis=EclBasis.FULLY_LOADED,
        floor_basis=FloorBasis.PRE_FLOOR,
        provenance=_provenance(),
        mapping=_mapping_rule(),
        raw_text="13.6",
        engine_values={Engine.DOCLING: Decimal("13.6"), Engine.CAMELOT: Decimal("13.6")},
    )


# --- StrEnums --------------------------------------------------------------------

_ENUMS = [
    Jurisdiction,
    Template,
    Unit,
    SourceKind,
    Engine,
    CheckType,
    CheckOutcome,
    ValidationStatus,
    EclBasis,
    FloorBasis,
    MappingMethod,
]


@pytest.mark.parametrize("enum_cls", _ENUMS)
def test_enum_members_equal_their_string_value(enum_cls):
    for member in enum_cls:
        assert member == member.value
        assert isinstance(member.value, str)


def test_known_enum_members_present():
    assert Engine.DOCLING == "DOCLING"
    assert Jurisdiction.EU == "EU"
    assert Template.KM1 == "KM1"
    assert Unit.EUR_M == "EUR_M"
    assert SourceKind.XBRL_CSV == "XBRL_CSV"
    assert CheckOutcome.SKIP == "SKIP"
    assert ValidationStatus.AUTO_PASSED == "AUTO_PASSED"
    assert MappingMethod.LLM == "LLM"


# --- ReportingPeriod -------------------------------------------------------------


def test_reporting_period_label_quarter_and_annual():
    assert ReportingPeriod(2025, 4).label == "2025Q4"
    assert ReportingPeriod(2025, None).label == "2025FY"


def test_reporting_period_parse_round_trips():
    for label in ("2025Q4", "2025FY", "2024Q1"):
        assert ReportingPeriod.parse(label).label == label


def test_reporting_period_parse_rejects_garbage():
    for bad in ("garbage", "2025", "25Q4", "2025Q5", "2025Q0", "2025FYQ4", "", "2025Q4\n", " 2025Q4"):
        with pytest.raises(ValueError):
            ReportingPeriod.parse(bad)


def test_reporting_period_sort_key_orders_quarters_then_annual():
    q1 = ReportingPeriod(2025, 1).sort_key
    q4 = ReportingPeriod(2025, 4).sort_key
    fy = ReportingPeriod(2025, None).sort_key
    assert q1 < q4 < fy


def test_reporting_period_sort_key_orders_across_years():
    assert ReportingPeriod(2024, None).sort_key < ReportingPeriod(2025, 1).sort_key


# --- frozen-ness (every value object) --------------------------------------------

_FROZEN_INSTANCES = [
    _bbox(),
    _period(),
    Bank("barclays", "Barclays", Jurisdiction.UK, "https://x", None, "en_GB", "GBP"),
    _provenance(),
    _mapping_rule(),
    RawCell(0, 1, "13.6", _bbox(), Engine.DOCLING, "CET1 ratio", "T"),
    _field_value(),
    CheckResult(CheckType.RATIO_IDENTITY, CheckOutcome.PASS, ("KM1.5",), None, None, None, "ok"),
    ReconciliationResult(_field_value(), (), Decimal("1.0"), (), ValidationStatus.AUTO_PASSED),
    DatasetRow(
        "barclays", "2025Q4", Jurisdiction.UK, Template.KM1, "KM1.5", Decimal("13.6"),
        Unit.PERCENT, EclBasis.FULLY_LOADED, FloorBasis.FINAL, "https://x", 4, None,
        Decimal("1.0"), (), ValidationStatus.AUTO_PASSED, "2026-06-18T00:00:00Z", "r1",
    ),
]


@pytest.mark.parametrize("instance", _FROZEN_INSTANCES)
def test_instances_are_frozen(instance):
    field_name = next(iter(dataclasses.fields(instance))).name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field_name, "mutated")


# --- Decimal fidelity (no float coercion) ----------------------------------------


def test_field_value_keeps_decimal_exact():
    fv = _field_value()
    assert isinstance(fv.value, Decimal)
    assert fv.value == Decimal("13.6")
    # the stored object is the same Decimal, not a float round-trip
    assert type(fv.value) is Decimal
    for v in fv.engine_values.values():
        assert type(v) is Decimal


def test_dataset_row_keeps_decimal_exact():
    row = DatasetRow(
        bank="barclays",
        period="2025Q4",
        jurisdiction=Jurisdiction.UK,
        template=Template.KM1,
        field="KM1.5",
        value=Decimal("13.6"),
        unit=Unit.PERCENT,
        ecl_basis=EclBasis.FULLY_LOADED,
        floor_basis=FloorBasis.FINAL,
        source_url="https://example.com/p3.pdf",
        page=4,
        bbox=_bbox(),
        confidence=Decimal("0.98"),
        validation_basis=(CheckType.RATIO_IDENTITY, CheckType.CROSS_FOOT),
        status=ValidationStatus.AUTO_PASSED,
        extracted_at="2026-06-18T00:00:00Z",
        run_id="run-1",
    )
    assert type(row.value) is Decimal
    assert type(row.confidence) is Decimal


# --- orthogonal basis axes -------------------------------------------------------


def test_ecl_and_floor_bases_are_independent():
    fv = dataclasses.replace(_field_value(), ecl_basis=EclBasis.FULLY_LOADED, floor_basis=FloorBasis.PRE_FLOOR)
    assert fv.ecl_basis == EclBasis.FULLY_LOADED
    assert fv.floor_basis == FloorBasis.PRE_FLOOR
    # the other corner of the product is equally valid
    other = dataclasses.replace(fv, ecl_basis=EclBasis.TRANSITIONAL, floor_basis=FloorBasis.FINAL)
    assert (other.ecl_basis, other.floor_basis) == (EclBasis.TRANSITIONAL, FloorBasis.FINAL)


# --- MappingDecision audit record ------------------------------------------------


def test_mapping_decision_rule_allows_none_llm_fields():
    md = _mapping_rule()
    assert md.method == MappingMethod.RULE
    assert md.model is None and md.prompt_sha is None and md.prompt_version is None


def test_mapping_decision_llm_carries_audit_fields():
    md = MappingDecision(
        method=MappingMethod.LLM,
        model="claude-haiku-4-5",
        prompt_sha="abc123",
        prompt_version="v1",
        matched_alias=None,
        confidence=Decimal("0.91"),
    )
    assert md.model == "claude-haiku-4-5"
    assert type(md.confidence) is Decimal


# --- full round-trip -------------------------------------------------------------


def test_field_value_round_trip_nested_provenance():
    fv = _field_value()
    assert fv.provenance.bbox.page == 4
    assert fv.provenance.period.label == "2025Q4"
    assert fv.mapping.method == MappingMethod.RULE
    assert fv.engine_values[Engine.CAMELOT] == Decimal("13.6")


def test_reconciliation_result_threads_field_and_checks():
    fv = _field_value()
    check = CheckResult(
        CheckType.RATIO_IDENTITY,
        CheckOutcome.PASS,
        ("KM1.5", "KM1.1", "KM1.4"),
        Decimal("13.6"),
        Decimal("13.6"),
        Decimal("0.1"),
        "CET1% == CET1cap / RWA",
    )
    rr = ReconciliationResult(
        field_value=fv,
        checks=(check,),
        confidence=Decimal("0.98"),
        validation_basis=(CheckType.RATIO_IDENTITY,),
        status=ValidationStatus.AUTO_PASSED,
    )
    assert rr.field_value is fv
    assert rr.checks[0].outcome == CheckOutcome.PASS
    assert rr.validation_basis == (CheckType.RATIO_IDENTITY,)


def test_manifest_row_optional_template():
    mr = ManifestRow(
        bank_id="barclays",
        period="2025Q4",
        template=None,
        url="https://example.com/p3.pdf",
        sha256="deadbeef",
        source_kind=SourceKind.PDF,
        local_path="data/raw/deadbeef.pdf",
        status="fetched",
        fetched_at="2026-06-18T00:00:00Z",
    )
    assert mr.template is None
    assert mr.source_kind == SourceKind.PDF
