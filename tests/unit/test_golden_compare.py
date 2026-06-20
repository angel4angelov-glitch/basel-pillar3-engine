"""Unit tests for the golden-set accuracy comparator (chunk 5.1).

The comparator is the measuring instrument; if it lies, every accuracy number is
worthless. So it is tested harder than the thing it measures: Decimal-exactness,
the empty-extraction trap (0/N, never "100% of 0"), misses vs extras vs mismatches,
and that accuracy is a Decimal (never a drifting float).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from isda_p3.golden import (
    CellOutcome,
    compare_to_golden,
    fixture_from_fieldvalues,
    load_fixture,
    load_golden,
    write_fixture,
)
from isda_p3.models import (
    BBox,
    EclBasis,
    Engine,
    FieldValue,
    FloorBasis,
    MappingDecision,
    MappingMethod,
    Provenance,
    ReportingPeriod,
    SourceKind,
    Template,
    Unit,
)


def _d(m: dict[str, str]) -> dict[str, Decimal]:
    return {k: Decimal(v) for k, v in m.items()}


# --- the happy path --------------------------------------------------------------


def test_all_correct_is_full_accuracy():
    golden = _d({"KM1.1": "51219", "KM1.5": "14.1"})
    report = compare_to_golden(golden, dict(golden))
    assert report.accuracy == Decimal(1)
    assert report.n_correct == 2 and report.n_golden == 2
    assert not report.mismatches and not report.missing and not report.extras
    assert all(c.outcome is CellOutcome.CORRECT for c in report.comparisons)


def test_accuracy_is_decimal_not_float():
    golden = _d({"a": "1", "b": "2", "c": "3"})
    extracted = _d({"a": "1", "b": "2", "c": "999"})  # 2/3
    report = compare_to_golden(golden, extracted)
    assert isinstance(report.accuracy, Decimal)
    assert report.accuracy == Decimal(2) / Decimal(3)


# --- decimal exactness -----------------------------------------------------------


def test_trailing_zeros_are_equal_not_a_mismatch():
    # 15 == 15.0 == 15.00 numerically; disclosure precision must not create a miss.
    golden = _d({"KM1.x": "15"})
    extracted = _d({"KM1.x": "15.00"})
    report = compare_to_golden(golden, extracted)
    assert report.accuracy == Decimal(1)
    assert report.comparisons[0].outcome is CellOutcome.CORRECT


def test_tiny_difference_is_a_mismatch():
    golden = _d({"KM1.5": "13.6"})
    extracted = _d({"KM1.5": "13.60001"})  # a real misread, however small
    report = compare_to_golden(golden, extracted)
    assert report.accuracy == Decimal(0)
    c = report.comparisons[0]
    assert c.outcome is CellOutcome.MISMATCH
    assert c.golden == Decimal("13.6") and c.extracted == Decimal("13.60001")


# --- misses / extras / mismatches ------------------------------------------------


def test_missing_golden_cell_counts_against_accuracy():
    golden = _d({"KM1.1": "51219", "KM1.2": "63933"})
    extracted = _d({"KM1.1": "51219"})  # KM1.2 not extracted
    report = compare_to_golden(golden, extracted)
    assert report.accuracy == Decimal(1) / Decimal(2)
    assert [c.field_code for c in report.missing] == ["KM1.2"]
    assert report.missing[0].extracted is None


def test_extra_extracted_cell_is_reported_but_not_in_denominator():
    golden = _d({"KM1.1": "51219"})
    extracted = _d({"KM1.1": "51219", "KM1.99": "123"})  # spurious extra
    report = compare_to_golden(golden, extracted)
    # accuracy denominator is the GOLDEN set, so an extra does not dilute it...
    assert report.accuracy == Decimal(1)
    assert report.n_golden == 1
    # ...but the extra is surfaced loudly, never silently dropped.
    assert [c.field_code for c in report.extras] == ["KM1.99"]
    assert report.extras[0].golden is None and report.extras[0].extracted == Decimal("123")


# --- the empty-extraction trap (the whole point of the chunk) --------------------


def test_zero_extracted_against_n_golden_is_zero_not_full():
    golden = _d({"KM1.1": "51219", "KM1.2": "63933", "KM1.3": "71789"})
    report = compare_to_golden(golden, {})  # extraction produced NOTHING
    assert report.accuracy == Decimal(0)  # 0/3, never "100% of 0"
    assert report.n_correct == 0 and report.n_golden == 3
    assert len(report.missing) == 3


def test_empty_golden_raises_never_reports_100_percent_of_zero():
    with pytest.raises(ValueError):
        compare_to_golden({}, {"KM1.1": Decimal("51219")})


# --- loader ----------------------------------------------------------------------


def test_load_golden_parses_values_as_exact_decimals(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text(
        "bank: barclays\nperiod: 2026Q1\ntemplate: KM1\n"
        "values:\n"
        '  KM1.5:\n    value: "14.1"\n    unit: PERCENT\n    ecl_basis: NA\n    floor_basis: FINAL\n',
        encoding="utf-8",
    )
    g = load_golden(p)
    assert g.bank == "barclays" and g.period == "2026Q1" and g.template == "KM1"
    assert g.values == {"KM1.5": Decimal("14.1")}
    assert isinstance(g.values["KM1.5"], Decimal)


def test_load_golden_empty_values_raises(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("bank: x\nperiod: 2026Q1\ntemplate: KM1\nvalues: {}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_golden(p)


# --- frozen extracted-cells fixture round-trip -----------------------------------


def _fv(code: str, value: str, unit: Unit, ecl: EclBasis, floor: FloorBasis) -> FieldValue:
    return FieldValue(
        template=Template.KM1,
        field_code=code,
        value=Decimal(value),
        unit=unit,
        ecl_basis=ecl,
        floor_basis=floor,
        provenance=Provenance(
            bank_id="barclays",
            period=ReportingPeriod(2026, 1),
            source_url="https://example/pdf",
            source_kind=SourceKind.PDF,
            engine=Engine.DOCLING,
            bbox=BBox(page=5, x0=1.0, y0=2.0, x1=3.0, y1=4.0),
        ),
        mapping=MappingDecision(MappingMethod.RULE, None, None, None, None, Decimal("1")),
        raw_text=value,
        engine_values={Engine.DOCLING: Decimal(value)},
    )


def test_fixture_round_trip_preserves_decimals_and_basis(tmp_path):
    fvs = [
        _fv("KM1.1", "51219", Unit.GBP_M, EclBasis.TRANSITIONAL, FloorBasis.NA),
        _fv("KM1.5", "14.10", Unit.PERCENT, EclBasis.NA, FloorBasis.FINAL),
    ]
    fx = fixture_from_fieldvalues(
        fvs, bank="barclays", period="2026Q1", template="KM1",
        source_url="https://example/pdf", sha256="deadbeef",
    )
    path = tmp_path / "cells.json"
    write_fixture(fx, path)
    back = load_fixture(path)

    assert back.engine is Engine.DOCLING and back.sha256 == "deadbeef"
    assert back.values == {"KM1.1": Decimal("51219"), "KM1.5": Decimal("14.10")}
    rebuilt = back.to_fieldvalues(bank_id="barclays")
    assert rebuilt["KM1.1"].ecl_basis is EclBasis.TRANSITIONAL
    assert rebuilt["KM1.5"].floor_basis is FloorBasis.FINAL
    assert rebuilt["KM1.1"].provenance.bbox == BBox(page=5, x0=1.0, y0=2.0, x1=3.0, y1=4.0)
    assert rebuilt["KM1.5"].value == Decimal("14.10")  # exactness, not 14.1 float drift
