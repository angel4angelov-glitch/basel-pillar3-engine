"""Tests for the OV1 "Overview of RWA" template + its basis-aware cross-foot (chunk 2.3).

Synthetic ``RawCell`` grids + direct ``FieldValue`` builders — no Docling, no LLM,
no network. These pin the OV1 contract (CLAUDE.md §A):

* The cross-foot sums the TOP-LEVEL risk rows into the total; an "of which" sub-row
  can never match a top-level alias (exact normalised match, no substrings).
* The cross-foot is basis-pinned like a ratio identity: a pre-floor row folded into
  a final total is a ``CrossBasisError`` (C1), not a tolerance miss.
* Every value is ``Decimal``; a missing addend is ``SKIP`` not ``FAIL``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from isda_p3.config_load import load_template
from isda_p3.mapping.classify import select_template_table
from isda_p3.mapping.map_fields import map_fields
from isda_p3.models import (
    BBox,
    Bank,
    CheckOutcome,
    CheckType,
    EclBasis,
    Engine,
    FieldValue,
    FloorBasis,
    Jurisdiction,
    MappingDecision,
    MappingMethod,
    Provenance,
    RawCell,
    ReportingPeriod,
    SourceKind,
    Template,
    Unit,
    ValidationStatus,
)
from isda_p3.reconcile.checks import CrossBasisError, cross_foot
from isda_p3.reconcile.engine import reconcile_template
from isda_p3.reconcile.identities import load_identities, load_tolerances, load_weights

# --- builders ------------------------------------------------------------------

_OV1_SPEC = load_template(Template.OV1)
_KM1_SPEC = load_template(Template.KM1)
_PERIOD = ReportingPeriod(2025, 4)
_URL = "https://home.barclays/pillar3.pdf"
_WEIGHTS = load_weights()
_TOLS = load_tolerances()

_GBP_BANK = Bank(
    id="barclays",
    name="Barclays",
    jurisdiction=Jurisdiction.UK,
    ir_url="https://home.barclays",
    p3dh_lei=None,
    number_locale="en_GB",
    reporting_currency="GBP",
)
_EUR_BANK = Bank(
    id="deutsche-bank",
    name="Deutsche Bank",
    jurisdiction=Jurisdiction.EU,
    ir_url="https://db.com",
    p3dh_lei=None,
    number_locale="de_DE",
    reporting_currency="EUR",
)


def _cell(row: int, col: int, text: str) -> RawCell:
    return RawCell(
        row_idx=row,
        col_idx=col,
        text=text,
        bbox=BBox(page=8, x0=float(col), y0=float(row), x1=float(col) + 1, y1=float(row) + 1),
        engine=Engine.DOCLING,
        row_label=None,
        col_label=None,
    )


def _grid(rows: list[tuple[str, ...]]) -> list:
    cells = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cells.append(_cell(r, c, text))
    return cells


# A clean OV1 grid: cols are (label, a=RWA current, b=RWA prior, c=min capital). The
# mapper takes the first numeric cell right of the label = col a. Includes an
# "of which" sub-row under credit risk to prove it never maps to OV1.1.
# Cross-foot: 200000+20000+100+5000+15000+40000 = 280100 = OV1.29.
_CLEAN_OV1 = [
    ("Credit risk (excluding counterparty credit risk)", "200,000", "195,000", "16,000"),
    ("Of which: the standardised approach (SA)", "80,000", "78,000", "6,400"),
    ("Counterparty credit risk (CCR)", "20,000", "19,000", "1,600"),
    ("Settlement/delivery risk", "100", "90", "8"),
    ("Securitisation exposures in the non-trading book", "5,000", "4,800", "400"),
    ("Position, foreign exchange and commodities risks (Market risk)", "15,000", "14,000", "1,200"),
    ("Operational risk", "40,000", "40,000", "3,200"),
    ("Total risk-weighted exposure amount", "280,100", "272,880", "22,408"),
]


def _map(cells, bank=_GBP_BANK):
    return map_fields(cells, _OV1_SPEC, bank, _PERIOD, _URL, SourceKind.PDF, Engine.DOCLING)


def _by_code(values: list[FieldValue]) -> dict[str, FieldValue]:
    return {fv.field_code: fv for fv in values}


# --- mapping: 7 top-level fields, sub-row guard, currency unit ------------------


def test_clean_ov1_maps_seven_top_level_fields():
    values, unmatched = _map(_grid(_CLEAN_OV1))
    assert unmatched == []
    assert {fv.field_code for fv in values} == {
        "OV1.1",
        "OV1.6",
        "OV1.15",
        "OV1.16",
        "OV1.20",
        "OV1.23",
        "OV1.29",
    }


def test_column_a_value_is_mapped():
    # first numeric cell right of the label = col a (current RWA), not b or c.
    by = _by_code(_map(_grid(_CLEAN_OV1))[0])
    assert by["OV1.1"].value == Decimal("200000")
    assert by["OV1.29"].value == Decimal("280100")
    assert by["OV1.15"].value == Decimal("100")


def test_of_which_subrow_does_not_map_to_credit_risk():
    # FALSE-POSITIVE GUARD: the "of which: the standardised approach" sub-row (value
    # 80,000) must NOT map to OV1.1 — exact normalised match forbids the substring.
    by = _by_code(_map(_grid(_CLEAN_OV1))[0])
    assert by["OV1.1"].value == Decimal("200000")  # the TOP-LEVEL row, not the sub-row
    assert all(fv.value != Decimal("80000") for fv in by.values())  # sub-row never emitted


def test_monetary_resolves_to_bank_currency_unit():
    gbp = _by_code(_map(_grid(_CLEAN_OV1), bank=_GBP_BANK)[0])
    assert all(fv.unit is Unit.GBP_M for fv in gbp.values())  # MONETARY + GBP
    eur = _by_code(_map(_grid(_CLEAN_OV1), bank=_EUR_BANK)[0])
    assert eur["OV1.1"].unit is Unit.EUR_M  # same template, different bank currency


# --- cross_foot: PASS / FAIL / SKIP (basis-aware) ------------------------------

_CF = load_identities(Template.OV1).cross_foots[0]
_CF_ABS = _TOLS["cross_foot"]["abs"]
_CF_REL = _TOLS["cross_foot"]["rel"]

_PROV = Provenance(
    bank_id="barclays",
    period=_PERIOD,
    source_url=_URL,
    source_kind=SourceKind.PDF,
    engine=Engine.DOCLING,
    bbox=BBox(page=8, x0=0.0, y0=0.0, x1=1.0, y1=1.0),
)
_MAP = MappingDecision(
    method=MappingMethod.RULE,
    model=None,
    prompt_sha=None,
    prompt_version=None,
    matched_alias="x",
    confidence=Decimal("1"),
)


def _ov1_fv(code: str, value: str, *, floor: FloorBasis = FloorBasis.FINAL) -> FieldValue:
    v = Decimal(value)
    return FieldValue(
        template=Template.OV1,
        field_code=code,
        value=v,
        unit=Unit.GBP_M,
        ecl_basis=EclBasis.NA,
        floor_basis=floor,
        provenance=_PROV,
        mapping=_MAP,
        raw_text=value,
        engine_values={Engine.DOCLING: v},
    )


def _ov1_values(*, total: str = "280100") -> dict[str, FieldValue]:
    """The six top-level rows + total, footing exactly when total == 280100."""
    return {
        "OV1.1": _ov1_fv("OV1.1", "200000"),
        "OV1.6": _ov1_fv("OV1.6", "20000"),
        "OV1.15": _ov1_fv("OV1.15", "100"),
        "OV1.16": _ov1_fv("OV1.16", "5000"),
        "OV1.20": _ov1_fv("OV1.20", "15000"),
        "OV1.23": _ov1_fv("OV1.23", "40000"),
        "OV1.29": _ov1_fv("OV1.29", total),
    }


def test_cross_foot_pass_components_sum_to_total():
    r = cross_foot(_ov1_values(), _CF, _CF_ABS, _CF_REL)
    assert r.outcome is CheckOutcome.PASS
    assert r.check_type is CheckType.CROSS_FOOT
    assert r.expected == Decimal("280100")  # sum of the six top-level rows
    assert r.actual == Decimal("280100")  # stated total
    assert r.field_codes == ("OV1.29", "OV1.1", "OV1.6", "OV1.15", "OV1.16", "OV1.20", "OV1.23")


def test_cross_foot_fail_when_total_wrong():
    r = cross_foot(_ov1_values(total="300000"), _CF, _CF_ABS, _CF_REL)
    assert r.outcome is CheckOutcome.FAIL
    assert "280100" in r.detail  # components sum
    assert "300000" in r.detail  # stated total


def test_cross_foot_skip_when_component_missing():
    values = _ov1_values()
    del values["OV1.20"]  # market risk absent → missing addend → SKIP, never FAIL
    r = cross_foot(values, _CF, _CF_ABS, _CF_REL)
    assert r.outcome is CheckOutcome.SKIP
    assert "OV1.20" in r.detail


# --- cross_foot: basis pinning (C1) --------------------------------------------


def test_cross_foot_basis_pin_total_pre_floor_raises():
    # BASIS-PIN: OV1.29 supplied PRE_FLOOR where the identity pins FINAL → refuse.
    values = _ov1_values()
    values["OV1.29"] = _ov1_fv("OV1.29", "280100", floor=FloorBasis.PRE_FLOOR)
    with pytest.raises(CrossBasisError, match="OV1.29"):
        cross_foot(values, _CF, _CF_ABS, _CF_REL)


def test_cross_foot_basis_pin_component_pre_floor_raises():
    # The pin fires on COMPONENT rows too, against the real OV1 identity codes: a
    # pre-floor credit-risk row can never be folded into the final total.
    values = _ov1_values()
    values["OV1.1"] = _ov1_fv("OV1.1", "200000", floor=FloorBasis.PRE_FLOOR)
    with pytest.raises(CrossBasisError, match="OV1.1"):
        cross_foot(values, _CF, _CF_ABS, _CF_REL)


# --- reconcile_template(OV1): routing via the cross-foot -----------------------


def test_reconcile_template_ov1_all_consistent_all_auto_passed():
    # Every OV1 field is touched by the cross-foot (PASS) → a check fires for each →
    # validation_basis non-empty → AUTO_PASSED. Two-engine SKIPs (single engine).
    results = reconcile_template(_ov1_values(), Template.OV1, tolerances=_TOLS, weights=_WEIGHTS)
    assert len(results) == 7
    assert all(r.status is ValidationStatus.AUTO_PASSED for r in results)
    by = {r.field_value.field_code: r for r in results}
    assert by["OV1.29"].validation_basis == (CheckType.CROSS_FOOT,)
    assert by["OV1.1"].validation_basis == (CheckType.CROSS_FOOT,)


def test_reconcile_template_ov1_broken_sum_flags_all_cross_foot_fields():
    # Break the total → the single cross-foot FAILs → every field sharing it FLAGS.
    results = reconcile_template(
        _ov1_values(total="300000"), Template.OV1, tolerances=_TOLS, weights=_WEIGHTS
    )
    assert len(results) == 7
    assert all(r.status is ValidationStatus.FLAGGED for r in results)
    by = {r.field_value.field_code: r for r in results}
    assert by["OV1.29"].confidence == Decimal("0")  # cross_foot FAIL floors the product


def test_reconcile_template_ov1_from_mapped_grid_all_auto_passed():
    # End-to-end: the mapped synthetic grid reconciles cleanly through the cross-foot.
    values = {fv.field_code: fv for fv in _map(_grid(_CLEAN_OV1))[0]}
    results = reconcile_template(values, Template.OV1, tolerances=_TOLS, weights=_WEIGHTS)
    assert all(r.status is ValidationStatus.AUTO_PASSED for r in results)


# --- classify: OV1 table chosen over a KM1 decoy -------------------------------

_KM1_DECOY = [
    ("Common Equity Tier 1 (CET1) capital", "47,200", "45,000"),
    ("Tier 1 capital", "52,000", "50,000"),
    ("Total capital", "60,000", "58,000"),
    ("Total risk-weighted assets (RWA)", "320,000", "310,000"),
]


def test_select_template_table_picks_ov1_over_km1_decoy():
    ov1_group = _grid(_CLEAN_OV1)
    km1_group = _grid(_KM1_DECOY)
    chosen = select_template_table([km1_group, ov1_group], _OV1_SPEC)
    assert chosen is ov1_group  # the OV1 grid matches all 7 OV1 aliases; KM1 matches ~0


def test_select_template_table_km1_spec_still_picks_km1():
    # The decoy is the right table for the KM1 spec — selection is spec-directed.
    ov1_group = _grid(_CLEAN_OV1)
    km1_group = _grid(_KM1_DECOY)
    chosen = select_template_table([ov1_group, km1_group], _KM1_SPEC)
    assert chosen is km1_group
