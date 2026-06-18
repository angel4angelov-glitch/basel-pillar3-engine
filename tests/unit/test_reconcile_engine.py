"""Tests for isda_p3.reconcile.confidence + engine (chunk 1.6).

The auto-accept/queue routing core. Confidence is a weighted PRODUCT so one hard
FAIL floors it to ~0 (guaranteed below threshold); a field NO check touched gets
the ``unchecked`` baseline (0.90 < 0.95) so it never silently auto-accepts
(CLAUDE.md §A.2). Status is AUTO_PASSED iff confidence ≥ threshold AND no
applicable check FAILed; everything is ``Decimal``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from isda_p3.config import CONFIDENCE_AUTO_ACCEPT
from isda_p3.models import (
    BBox,
    CheckOutcome,
    CheckResult,
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
    ValidationStatus,
)
from isda_p3.reconcile.confidence import compute_confidence
from isda_p3.reconcile.engine import (
    applicable_checks,
    reconcile_field,
    reconcile_template,
)
from isda_p3.reconcile.identities import load_tolerances, load_weights

# --- fixtures / builders -------------------------------------------------------

_WEIGHTS = load_weights()
_TOLS = load_tolerances()

_PROV = Provenance(
    bank_id="barclays",
    period=ReportingPeriod(2025, 4),
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


def _check(
    outcome: CheckOutcome,
    *codes: str,
    check_type: CheckType = CheckType.RATIO_IDENTITY,
) -> CheckResult:
    return CheckResult(
        check_type=check_type,
        outcome=outcome,
        field_codes=codes,
        expected=None,
        actual=None,
        tolerance=None,
        detail="",
    )


def _clean_km1() -> dict[str, FieldValue]:
    """A KM1 value set where all three CET1/T1/Total ratio identities PASS."""
    return {
        "KM1.1": _fv("KM1.1", "50000", ecl=EclBasis.TRANSITIONAL, unit=Unit.GBP_M),
        "KM1.2": _fv("KM1.2", "55000", ecl=EclBasis.TRANSITIONAL, unit=Unit.GBP_M),
        "KM1.3": _fv("KM1.3", "65000", ecl=EclBasis.TRANSITIONAL, unit=Unit.GBP_M),
        "KM1.4": _fv("KM1.4", "368000", floor=FloorBasis.FINAL, unit=Unit.GBP_M),
        "KM1.5": _fv("KM1.5", "13.6", floor=FloorBasis.FINAL),  # 50000/368000×100=13.587
        "KM1.6": _fv("KM1.6", "14.9", floor=FloorBasis.FINAL),  # 55000/368000×100=14.946
        "KM1.7": _fv("KM1.7", "17.7", floor=FloorBasis.FINAL),  # 65000/368000×100=17.663
    }


# --- compute_confidence: the weighted product ----------------------------------


def test_confidence_all_pass_is_one():
    checks = [_check(CheckOutcome.PASS, "A"), _check(CheckOutcome.PASS, "A")]
    assert compute_confidence(checks, _WEIGHTS) == Decimal("1")


def test_confidence_one_ratio_fail_floors_to_zero():
    checks = [_check(CheckOutcome.PASS, "A"), _check(CheckOutcome.FAIL, "A")]
    c = compute_confidence(checks, _WEIGHTS)
    assert c == Decimal("0")
    assert isinstance(c, Decimal)


def test_confidence_cross_foot_fail_floors_to_zero():
    checks = [_check(CheckOutcome.FAIL, "A", check_type=CheckType.CROSS_FOOT)]
    assert compute_confidence(checks, _WEIGHTS) == Decimal("0")


def test_confidence_one_skip_only_is_unchecked_baseline():
    # A skip is NOT validation: a field whose only check SKIPped was never actually
    # validated, so it gets the unchecked baseline (0.90), identical to having no
    # check at all — NOT the 0.97 one-skip product (the M1 auto-accept bug).
    assert compute_confidence([_check(CheckOutcome.SKIP, "A")], _WEIGHTS) == Decimal("0.90")


def test_confidence_two_skip_only_is_unchecked_baseline():
    # Two skips are still no validation → 0.90, NOT 0.97² = 0.9409.
    checks = [_check(CheckOutcome.SKIP, "A"), _check(CheckOutcome.SKIP, "A")]
    assert compute_confidence(checks, _WEIGHTS) == Decimal("0.90")


def test_confidence_empty_is_unchecked_baseline():
    # A field touched by NO check must NOT auto-accept (0.90 < 0.95) — it routes to review.
    c = compute_confidence([], _WEIGHTS)
    assert c == Decimal("0.90")
    assert isinstance(c, Decimal)


def test_confidence_pass_and_skip_is_skip_penalised_product():
    # At least one real check fired (PASS) → the SKIP is a mild penalty on an
    # otherwise-validated field: 1.0 × 0.97 = 0.97 (>= 0.95 → still auto-accepts).
    checks = [_check(CheckOutcome.PASS, "A"), _check(CheckOutcome.SKIP, "A")]
    assert compute_confidence(checks, _WEIGHTS) == Decimal("0.97")


def test_confidence_missing_weight_raises():
    # A fail weight absent from config is a loud config error, never a silent default.
    with pytest.raises(ValueError, match="cross_foot_fail"):
        compute_confidence(
            [_check(CheckOutcome.FAIL, "A", check_type=CheckType.CROSS_FOOT)],
            {"pass": Decimal("1"), "skip": Decimal("0.97"), "unchecked": Decimal("0.90")},
        )


# --- applicable_checks ----------------------------------------------------------


def test_applicable_checks_filters_by_field_code():
    a = _check(CheckOutcome.PASS, "KM1.5", "KM1.1", "KM1.4")
    b = _check(CheckOutcome.PASS, "KM1.6", "KM1.2", "KM1.4")
    assert applicable_checks("KM1.5", [a, b]) == (a,)
    assert applicable_checks("KM1.4", [a, b]) == (a, b)  # shared denominator
    assert applicable_checks("KM1.9", [a, b]) == ()


# --- reconcile_field routing ----------------------------------------------------


def test_field_auto_passed_when_confident_and_no_fail():
    fv = _fv("KM1.5", "13.6", floor=FloorBasis.FINAL)
    res = reconcile_field(fv, [_check(CheckOutcome.PASS, "KM1.5")], _WEIGHTS, CONFIDENCE_AUTO_ACCEPT)
    assert res.status is ValidationStatus.AUTO_PASSED
    assert res.confidence == Decimal("1")


def test_field_flagged_when_any_fail_even_if_others_pass():
    # FAIL gate is independent of the confidence threshold: custom weights keep
    # confidence high (fail factor 0.99) yet a present FAIL must still FLAG.
    weights = {**_WEIGHTS, "ratio_identity_fail": Decimal("0.99")}
    fv = _fv("KM1.5", "13.6", floor=FloorBasis.FINAL)
    checks = [_check(CheckOutcome.PASS, "KM1.5"), _check(CheckOutcome.FAIL, "KM1.5")]
    res = reconcile_field(fv, checks, weights, CONFIDENCE_AUTO_ACCEPT)
    assert res.confidence == Decimal("0.99")  # >= threshold
    assert res.status is ValidationStatus.FLAGGED  # but a FAIL is present


def test_field_boundary_confidence_exactly_threshold_auto_passes():
    # confidence == 0.95 exactly → AUTO_PASSED (>=, not >).
    weights = {**_WEIGHTS, "pass": Decimal("0.95")}
    fv = _fv("KM1.5", "13.6", floor=FloorBasis.FINAL)
    res = reconcile_field(fv, [_check(CheckOutcome.PASS, "KM1.5")], weights, CONFIDENCE_AUTO_ACCEPT)
    assert res.confidence == Decimal("0.95")
    assert res.status is ValidationStatus.AUTO_PASSED


def test_field_unchecked_routes_to_review():
    # No applicable check → unchecked baseline 0.90 < 0.95 → FLAGGED.
    fv = _fv("KM1.9", "1.0")
    res = reconcile_field(fv, [_check(CheckOutcome.PASS, "KM1.5")], _WEIGHTS, CONFIDENCE_AUTO_ACCEPT)
    assert res.checks == ()
    assert res.confidence == Decimal("0.90")
    assert res.status is ValidationStatus.FLAGGED


def test_field_skip_only_routes_to_review():
    # Every applicable check SKIPped → never validated → unchecked baseline 0.90
    # < 0.95 → FLAGGED, and validation_basis is empty (no check fired).
    fv = _fv("KM1.5", "13.6", floor=FloorBasis.FINAL)
    res = reconcile_field(fv, [_check(CheckOutcome.SKIP, "KM1.5")], _WEIGHTS, CONFIDENCE_AUTO_ACCEPT)
    assert res.confidence == Decimal("0.90")
    assert res.validation_basis == ()
    assert res.status is ValidationStatus.FLAGGED


def test_field_pass_plus_skip_auto_passes():
    # A real PASS fired; the SKIP is a mild penalty → 0.97 >= 0.95 → AUTO_PASSED.
    fv = _fv("KM1.5", "13.6", floor=FloorBasis.FINAL)
    checks = [_check(CheckOutcome.PASS, "KM1.5"), _check(CheckOutcome.SKIP, "KM1.5")]
    res = reconcile_field(fv, checks, _WEIGHTS, CONFIDENCE_AUTO_ACCEPT)
    assert res.confidence == Decimal("0.97")
    assert res.validation_basis == (CheckType.RATIO_IDENTITY,)
    assert res.status is ValidationStatus.AUTO_PASSED


@pytest.mark.parametrize(
    "checks",
    [
        [],  # no check at all
        [_check(CheckOutcome.SKIP, "KM1.5")],  # skip-only
        [_check(CheckOutcome.SKIP, "KM1.5"), _check(CheckOutcome.SKIP, "KM1.5")],  # all-skip
    ],
)
def test_empty_validation_basis_never_auto_passes(checks):
    # PROPERTY: an empty validation_basis (no PASS/FAIL fired) ⟺ status can never be
    # AUTO_PASSED. The fail weight is pushed to 0.99 so confidence alone would clear
    # the threshold — proving the explicit non-empty-basis guard, not the threshold,
    # is what blocks the never-validated field.
    weights = {**_WEIGHTS, "ratio_identity_fail": Decimal("0.99")}
    fv = _fv("KM1.5", "13.6", floor=FloorBasis.FINAL)
    res = reconcile_field(fv, checks, weights, CONFIDENCE_AUTO_ACCEPT)
    assert res.validation_basis == ()
    assert res.status is not ValidationStatus.AUTO_PASSED


def test_validation_basis_lists_only_fired_checks_deduped():
    # Two RATIO_IDENTITY (one PASS, one FAIL) + a SKIP CROSS_FOOT, all on KM1.4.
    fv = _fv("KM1.4", "368000", floor=FloorBasis.FINAL, unit=Unit.GBP_M)
    checks = [
        _check(CheckOutcome.PASS, "KM1.4", check_type=CheckType.RATIO_IDENTITY),
        _check(CheckOutcome.FAIL, "KM1.4", check_type=CheckType.RATIO_IDENTITY),
        _check(CheckOutcome.SKIP, "KM1.4", check_type=CheckType.CROSS_FOOT),
    ]
    res = reconcile_field(fv, checks, _WEIGHTS, CONFIDENCE_AUTO_ACCEPT)
    # SKIP excluded; RATIO_IDENTITY de-duplicated; CROSS_FOOT never fired.
    assert res.validation_basis == (CheckType.RATIO_IDENTITY,)


# --- reconcile_template (loader → checks → routing) ----------------------------


def test_reconcile_template_clean_km1_all_auto_passed():
    results = reconcile_template(
        _clean_km1(), Template.KM1, tolerances=_TOLS, weights=_WEIGHTS
    )
    assert len(results) == 7
    assert all(r.status is ValidationStatus.AUTO_PASSED for r in results)
    # every value carries the RATIO_IDENTITY fired basis
    by_code = {r.field_value.field_code: r for r in results}
    assert by_code["KM1.5"].validation_basis == (CheckType.RATIO_IDENTITY,)
    # everything Decimal; status is the ValidationStatus enum
    for r in results:
        assert isinstance(r.confidence, Decimal)
        assert isinstance(r.status, ValidationStatus)


def test_reconcile_template_broken_ratio_flags_only_sharing_fields():
    values = _clean_km1()
    values["KM1.5"] = _fv("KM1.5", "15.0", floor=FloorBasis.FINAL)  # 13.587 computed → FAIL
    results = reconcile_template(values, Template.KM1, tolerances=_TOLS, weights=_WEIGHTS)
    status = {r.field_value.field_code: r.status for r in results}
    # KM1.5 and the operands sharing its failed identity (KM1.1 numerator, KM1.4
    # denominator) are FLAGGED; the other two ratios are untouched and AUTO_PASSED.
    assert status["KM1.5"] is ValidationStatus.FLAGGED
    assert status["KM1.1"] is ValidationStatus.FLAGGED
    assert status["KM1.4"] is ValidationStatus.FLAGGED  # shares the failed check
    assert status["KM1.2"] is ValidationStatus.AUTO_PASSED
    assert status["KM1.3"] is ValidationStatus.AUTO_PASSED
    assert status["KM1.6"] is ValidationStatus.AUTO_PASSED
    assert status["KM1.7"] is ValidationStatus.AUTO_PASSED


def test_reconcile_template_missing_rwa_flags_all_ratio_fields():
    # THE M1 REGRESSION (chunk 1.9): KM1.4 (RWA) never extracted → all three ratio
    # identities reference a missing denominator → SKIP. A skip is not validation, so
    # every field touched only by those skipped identities (KM1.5/.6/.7 ratios AND
    # their KM1.1/.2/.3 numerators) FLAGS — it must NOT auto-accept on a 0.97 skip
    # product (§A.2 / audit C3). KM1.4 stays absent: nothing fabricated.
    values = _clean_km1()
    del values["KM1.4"]
    results = reconcile_template(values, Template.KM1, tolerances=_TOLS, weights=_WEIGHTS)
    by = {r.field_value.field_code: r for r in results}

    assert "KM1.4" not in by  # never fabricated as 0
    for code in ("KM1.1", "KM1.2", "KM1.3", "KM1.5", "KM1.6", "KM1.7"):
        assert by[code].status is ValidationStatus.FLAGGED, code
        assert by[code].confidence == Decimal("0.90"), code  # unchecked baseline, not 0.97
        assert by[code].validation_basis == (), code  # no check actually fired


def test_reconcile_template_missing_tolerance_key_raises_with_context():
    # A tolerances dict missing a sub-key fails loud with template context, not a
    # bare KeyError mid-pipeline.
    with pytest.raises(ValueError, match="reconcile_template\\(KM1\\)"):
        reconcile_template(
            _clean_km1(),
            Template.KM1,
            tolerances={"cross_foot": {"abs": Decimal("1"), "rel": Decimal("0.001")}},
            weights=_WEIGHTS,
        )


def test_load_weights_missing_required_key_raises(tmp_path):
    # An unvalidated baseline weight absent from config is a hard load-time error
    # (§A.4 — fail at startup, not on the first unchecked field).
    bad = tmp_path / "reconciliation.yaml"
    bad.write_text(
        "tolerances:\n"
        "  ratio_identity: {bp: 10}\n"
        "  cross_foot: {abs: 1.0, rel: 0.001}\n"
        "confidence:\n"
        "  pass: 1.0\n"
        "  skip: 0.97\n"
        "  ratio_identity_fail: 0.0\n"
        "  cross_foot_fail: 0.0\n",  # 'unchecked' omitted
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unchecked"):
        load_weights(path=bad)


def test_reconcile_template_cross_basis_error_propagates():
    # KM1.4 supplied PRE_FLOOR where the identity pins FINAL → CrossBasisError must
    # propagate out of reconcile_template (config/data error, never swallowed).
    from isda_p3.reconcile.checks import CrossBasisError

    values = _clean_km1()
    values["KM1.4"] = _fv("KM1.4", "368000", floor=FloorBasis.PRE_FLOOR, unit=Unit.GBP_M)
    with pytest.raises(CrossBasisError, match="KM1.4"):
        reconcile_template(values, Template.KM1, tolerances=_TOLS, weights=_WEIGHTS)
