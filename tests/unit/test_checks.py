"""Tests for isda_p3.reconcile.checks + identities (chunk 1.5).

Deterministic arithmetic only — no LLM, no I/O beyond the checked-in config YAMLs.
These pin the graded accuracy core (CLAUDE.md §A): every value is ``Decimal`` and
exact; a missing input is ``SKIP`` not ``FAIL``; and a cross-basis pairing is a
hard config/data error (``CrossBasisError``), never silently evaluated (C1).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from isda_p3.models import (
    BBox,
    CheckOutcome,
    CheckType,
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
from isda_p3.reconcile.checks import CrossBasisError, cross_foot, ratio_identity
from isda_p3.reconcile.identities import (
    CrossFoot,
    Operand,
    RatioIdentity,
    load_identities,
    load_tolerances,
)

# --- builders ------------------------------------------------------------------

_PERIOD = ReportingPeriod(2025, 4)
_PROV = Provenance(
    bank_id="barclays",
    period=_PERIOD,
    source_url="https://home.barclays/p3.pdf",
    source_kind=SourceKind.PDF,
    engine=Engine.DOCLING,
    bbox=BBox(page=4, x0=0.0, y0=0.0, x1=1.0, y1=1.0),
)
_MAP = MappingDecision(
    method=MappingMethod.RULE,
    model=None,
    prompt_sha=None,
    prompt_version=None,
    matched_alias="x",
    confidence=Decimal("1"),
)


def _fv(
    code: str,
    value: str,
    *,
    ecl: EclBasis = EclBasis.NA,
    floor: FloorBasis = FloorBasis.NA,
    unit: Unit = Unit.PERCENT,
) -> FieldValue:
    v = Decimal(value)
    return FieldValue(
        template=Template.KM1,
        field_code=code,
        value=v,
        unit=unit,
        ecl_basis=ecl,
        floor_basis=floor,
        provenance=_PROV,
        mapping=_MAP,
        raw_text=value,
        engine_values={Engine.DOCLING: v},
    )


# The KM1 CET1-ratio identity (KM1.5 = KM1.1 / KM1.4 ×100), basis-pinned per C1.
_CET1 = RatioIdentity(
    ratio=Operand("KM1.5", EclBasis.NA, FloorBasis.FINAL),
    numerator=Operand("KM1.1", EclBasis.TRANSITIONAL, FloorBasis.NA),
    denominator=Operand("KM1.4", EclBasis.NA, FloorBasis.FINAL),
    factor=Decimal("100"),
)
_TOL_BP = Decimal("10")


def _cet1_values(*, cet1: str = "50000", rwa: str = "368000", ratio: str = "13.6") -> dict:
    return {
        "KM1.1": _fv(
            "KM1.1", cet1, ecl=EclBasis.TRANSITIONAL, floor=FloorBasis.NA, unit=Unit.GBP_M
        ),
        "KM1.4": _fv("KM1.4", rwa, ecl=EclBasis.NA, floor=FloorBasis.FINAL, unit=Unit.GBP_M),
        "KM1.5": _fv("KM1.5", ratio, ecl=EclBasis.NA, floor=FloorBasis.FINAL),
    }


# --- ratio_identity: PASS / FAIL / SKIP ----------------------------------------


def test_ratio_identity_pass():
    # 50000 / 368000 ×100 = 13.5869… vs stated 13.6 → diff 0.013 ≤ 0.10pp → PASS
    r = ratio_identity(_cet1_values(), _CET1, _TOL_BP)
    assert r.outcome is CheckOutcome.PASS
    assert r.check_type is CheckType.RATIO_IDENTITY
    assert r.field_codes == ("KM1.5", "KM1.1", "KM1.4")
    assert r.actual == Decimal("13.6")
    assert r.expected == Decimal("50000") / Decimal("368000") * Decimal("100")
    assert r.tolerance == Decimal("10") / Decimal("100")
    # everything Decimal
    assert isinstance(r.expected, Decimal)
    assert isinstance(r.actual, Decimal)
    assert isinstance(r.tolerance, Decimal)


def test_ratio_identity_fail_detail_shows_both():
    r = ratio_identity(_cet1_values(ratio="15.0"), _CET1, _TOL_BP)
    assert r.outcome is CheckOutcome.FAIL
    assert r.actual == Decimal("15.0")
    # the human detail shows stated AND computed so a reviewer sees the gap
    assert "15.0" in r.detail
    assert "13.59" in r.detail


def test_ratio_identity_skip_when_operand_absent():
    values = _cet1_values()
    del values["KM1.1"]  # numerator absent
    r = ratio_identity(values, _CET1, _TOL_BP)
    assert r.outcome is CheckOutcome.SKIP  # SKIP, never FAIL
    assert r.field_codes == ("KM1.5", "KM1.1", "KM1.4")
    assert r.expected is None
    assert "KM1.1" in r.detail


def test_ratio_identity_boundary_exact_passes():
    # 50000 / 400000 ×100 = 12.5 exactly; stated 12.6 → diff 0.10pp == tol → PASS
    r = ratio_identity(_cet1_values(rwa="400000", ratio="12.6"), _CET1, _TOL_BP)
    assert r.outcome is CheckOutcome.PASS
    assert r.expected == Decimal("12.5")


def test_ratio_identity_just_outside_tolerance_fails():
    # 12.5 computed vs stated 12.61 → diff 0.11pp > 0.10pp → FAIL
    r = ratio_identity(_cet1_values(rwa="400000", ratio="12.61"), _CET1, _TOL_BP)
    assert r.outcome is CheckOutcome.FAIL


# --- ratio_identity: basis pinning (C1) ----------------------------------------


def test_basis_mismatch_raises_cross_basis_error():
    # KM1.4 supplied pre-floor where the identity expects FINAL → refuse to evaluate
    values = _cet1_values()
    values["KM1.4"] = _fv(
        "KM1.4", "368000", ecl=EclBasis.NA, floor=FloorBasis.PRE_FLOOR, unit=Unit.GBP_M
    )
    with pytest.raises(CrossBasisError, match="KM1.4"):
        ratio_identity(values, _CET1, _TOL_BP)


def test_ecl_basis_mismatch_raises():
    values = _cet1_values()
    values["KM1.1"] = _fv(
        "KM1.1", "50000", ecl=EclBasis.FULLY_LOADED, floor=FloorBasis.NA, unit=Unit.GBP_M
    )
    with pytest.raises(CrossBasisError, match="KM1.1"):
        ratio_identity(values, _CET1, _TOL_BP)


# --- ratio_identity: division by zero → SKIP, never crash ----------------------


def test_zero_denominator_skips_no_crash():
    r = ratio_identity(_cet1_values(rwa="0"), _CET1, _TOL_BP)
    assert r.outcome is CheckOutcome.SKIP
    assert "KM1.4" in r.detail


def test_absent_denominator_skips():
    values = _cet1_values()
    del values["KM1.4"]
    r = ratio_identity(values, _CET1, _TOL_BP)
    assert r.outcome is CheckOutcome.SKIP


# --- cross_foot: PASS / FAIL / SKIP --------------------------------------------

_CF = CrossFoot(total="T", components=("A", "B", "C"))
_ABS = Decimal("1.0")
_REL = Decimal("0.001")


def _cf_values(*, a="100", b="200", c="50", t="350") -> dict:
    return {
        "A": _fv("A", a, unit=Unit.GBP_M),
        "B": _fv("B", b, unit=Unit.GBP_M),
        "C": _fv("C", c, unit=Unit.GBP_M),
        "T": _fv("T", t, unit=Unit.GBP_M),
    }


def test_cross_foot_pass():
    r = cross_foot(_cf_values(), _CF, _ABS, _REL)
    assert r.outcome is CheckOutcome.PASS
    assert r.check_type is CheckType.CROSS_FOOT
    assert r.field_codes == ("T", "A", "B", "C")
    assert r.expected == Decimal("350")  # sum of components
    assert r.actual == Decimal("350")  # stated total
    assert isinstance(r.expected, Decimal)


def test_cross_foot_fail_detail_shows_both():
    r = cross_foot(_cf_values(t="400"), _CF, _ABS, _REL)
    assert r.outcome is CheckOutcome.FAIL
    assert "350" in r.detail
    assert "400" in r.detail


def test_cross_foot_boundary_exact_passes():
    # components sum 350, total 351 → diff 1.0 == abs_tol (rel 0.001×351=0.351 smaller) → PASS
    r = cross_foot(_cf_values(t="351"), _CF, _ABS, _REL)
    assert r.outcome is CheckOutcome.PASS


def test_cross_foot_just_outside_fails():
    r = cross_foot(_cf_values(t="351.01"), _CF, _ABS, _REL)
    assert r.outcome is CheckOutcome.FAIL


def test_cross_foot_skip_missing_total():
    values = _cf_values()
    del values["T"]
    r = cross_foot(values, _CF, _ABS, _REL)
    assert r.outcome is CheckOutcome.SKIP
    assert "T" in r.detail


def test_cross_foot_skip_missing_component():
    # a missing addend is missing input → SKIP, never a false FAIL
    values = _cf_values()
    del values["C"]
    r = cross_foot(values, _CF, _ABS, _REL)
    assert r.outcome is CheckOutcome.SKIP
    assert "C" in r.detail


# --- loaders -------------------------------------------------------------------


def test_load_identities_km1():
    ident = load_identities(Template.KM1)
    assert len(ident.ratio_identities) == 3
    assert ident.cross_foots == ()  # KM1 has none (OV1 gets them in 2.3)

    first = ident.ratio_identities[0]
    assert first.ratio.code == "KM1.5"
    assert first.ratio.ecl is EclBasis.NA
    assert first.ratio.floor is FloorBasis.FINAL
    assert first.numerator.code == "KM1.1"
    assert first.numerator.ecl is EclBasis.TRANSITIONAL
    assert first.numerator.floor is FloorBasis.NA
    assert first.denominator.code == "KM1.4"
    assert first.denominator.floor is FloorBasis.FINAL
    assert first.factor == Decimal("100")
    assert isinstance(first.factor, Decimal)


def test_load_identities_all_ratios_share_denominator_and_bases():
    ident = load_identities(Template.KM1)
    nums = [ri.numerator.code for ri in ident.ratio_identities]
    ratios = [ri.ratio.code for ri in ident.ratio_identities]
    assert nums == ["KM1.1", "KM1.2", "KM1.3"]
    assert ratios == ["KM1.5", "KM1.6", "KM1.7"]
    for ri in ident.ratio_identities:
        assert ri.denominator.code == "KM1.4"
        assert ri.denominator.floor is FloorBasis.FINAL


def test_load_identities_loaded_identity_runs_pass():
    # loader → checks wiring: the real KM1.5 identity PASSes on clean inputs
    ident = load_identities(Template.KM1)
    tol_bp = load_tolerances()["ratio_identity"]["bp"]
    r = ratio_identity(_cet1_values(), ident.ratio_identities[0], tol_bp)
    assert r.outcome is CheckOutcome.PASS


def test_load_identities_missing_section_raises():
    with pytest.raises(ValueError, match="OV1"):
        load_identities(Template.OV1)


def test_load_tolerances():
    tol = load_tolerances()
    assert tol["ratio_identity"]["bp"] == Decimal("10")
    assert isinstance(tol["ratio_identity"]["bp"], Decimal)
    assert tol["cross_foot"]["abs"] == Decimal("1.0")
    assert tol["cross_foot"]["rel"] == Decimal("0.001")
    assert isinstance(tol["cross_foot"]["abs"], Decimal)
    assert isinstance(tol["cross_foot"]["rel"], Decimal)


# --- loader fail-fast on malformed config --------------------------------------


def test_load_identities_bad_basis_raises(tmp_path):
    bad = tmp_path / "identities.yaml"
    bad.write_text(
        "KM1:\n"
        "  ratio_identities:\n"
        "    - ratio:       {code: KM1.5, ecl: NA, floor: BOGUS}\n"
        "      numerator:   {code: KM1.1, ecl: TRANSITIONAL, floor: NA}\n"
        "      denominator: {code: KM1.4, ecl: NA, floor: FINAL}\n"
        "      factor: 100\n"
        "  cross_foots: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="BOGUS"):
        load_identities(Template.KM1, path=bad)


def test_load_identities_missing_factor_raises(tmp_path):
    bad = tmp_path / "identities.yaml"
    bad.write_text(
        "KM1:\n"
        "  ratio_identities:\n"
        "    - ratio:       {code: KM1.5, ecl: NA, floor: FINAL}\n"
        "      numerator:   {code: KM1.1, ecl: TRANSITIONAL, floor: NA}\n"
        "      denominator: {code: KM1.4, ecl: NA, floor: FINAL}\n"
        "  cross_foots: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="factor"):
        load_identities(Template.KM1, path=bad)


def test_load_identities_non_list_section_raises(tmp_path):
    # a mistyped (mapping, not list) ratio_identities key must fail loud, not
    # silently drop every ratio check (§A.2).
    bad = tmp_path / "identities.yaml"
    bad.write_text("KM1:\n  ratio_identities: {oops: 1}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ratio_identities"):
        load_identities(Template.KM1, path=bad)


def test_load_identities_bad_cross_foot_raises(tmp_path):
    bad = tmp_path / "identities.yaml"
    bad.write_text(
        "KM1:\n"
        "  ratio_identities: []\n"
        "  cross_foots:\n"
        "    - total: T\n"
        "      components: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="components"):
        load_identities(Template.KM1, path=bad)
