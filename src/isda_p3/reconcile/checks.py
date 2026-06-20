"""Deterministic reconciliation checks (chunk 1.5) — the graded accuracy core.

Pure functions over already-mapped :class:`FieldValue`s; ``Decimal`` only, no LLM,
no I/O. Each returns a :class:`CheckResult`. The rules (CLAUDE.md §A):

* A missing input is ``SKIP``, never ``FAIL`` — a value we never extracted is not a
  failed check.
* A cross-basis pairing is a hard error (:class:`CrossBasisError`), never silently
  evaluated: an identity declares each operand's expected ``(ecl, floor)`` basis and
  the engine refuses to divide, e.g., pre-floor RWA into a final ratio (C1).
* Tolerance absorbs rounding only (inputs in €m, ratios disclosed to 1 dp).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from ..models import CheckOutcome, CheckResult, CheckType, FieldValue, Unit
from .identities import CrossFoot, Operand, RatioIdentity

_HUNDRED = Decimal("100")

# Monetary units all share the canonical millions scale, so one magnitude band serves
# every currency (the point of normalising bounds to millions — checks below).
_MONETARY_UNITS = frozenset({Unit.EUR_M, Unit.GBP_M, Unit.USD_M, Unit.CHF_M, Unit.JPY_M})


class CrossBasisError(ValueError):
    """A supplied ``FieldValue``'s actual basis differs from the identity's declared
    expected basis. This is a config/data error (C1), not a check ``FAIL``: the
    engine must not pair operands across bases (e.g. pre-floor RWA into a final ratio).
    """


def _assert_basis(fv: FieldValue, operand: Operand) -> None:
    """Raise :class:`CrossBasisError` if ``fv``'s basis differs from ``operand``'s declared one."""
    if fv.ecl_basis != operand.ecl or fv.floor_basis != operand.floor:
        raise CrossBasisError(
            f"{operand.code}: identity expects ecl={operand.ecl.value}/floor={operand.floor.value} "
            f"but FieldValue has ecl={fv.ecl_basis.value}/floor={fv.floor_basis.value}"
        )


def ratio_identity(
    values: Mapping[str, FieldValue], ident: RatioIdentity, tol_bp: Decimal
) -> CheckResult:
    """Check ``ratio == numerator / denominator × factor`` within ``tol_bp`` basis points.

    SKIP if any operand is absent or the denominator is zero; raise
    :class:`CrossBasisError` if any present operand's basis ≠ the identity's
    declared one; else PASS/FAIL on the tolerant comparison.
    """
    codes = (ident.ratio.code, ident.numerator.code, ident.denominator.code)
    tol = tol_bp / _HUNDRED  # basis points → percentage points (10 bp = 0.10 pp)

    missing = [c for c in codes if c not in values]
    if missing:
        return CheckResult(
            check_type=CheckType.RATIO_IDENTITY,
            outcome=CheckOutcome.SKIP,
            field_codes=codes,
            expected=None,
            actual=None,
            tolerance=tol,
            detail=f"SKIP: missing operand(s) {', '.join(missing)}",
        )

    ratio_fv = values[ident.ratio.code]
    num_fv = values[ident.numerator.code]
    den_fv = values[ident.denominator.code]

    # C1: refuse to evaluate across bases (raises before any arithmetic).
    _assert_basis(ratio_fv, ident.ratio)
    _assert_basis(num_fv, ident.numerator)
    _assert_basis(den_fv, ident.denominator)

    if den_fv.value == 0:
        return CheckResult(
            check_type=CheckType.RATIO_IDENTITY,
            outcome=CheckOutcome.SKIP,
            field_codes=codes,
            expected=None,
            actual=ratio_fv.value,
            tolerance=tol,
            detail=f"SKIP: denominator {ident.denominator.code} is zero — cannot divide",
        )

    expected = num_fv.value / den_fv.value * ident.factor
    actual = ratio_fv.value
    outcome = CheckOutcome.PASS if abs(actual - expected) <= tol else CheckOutcome.FAIL
    return CheckResult(
        check_type=CheckType.RATIO_IDENTITY,
        outcome=outcome,
        field_codes=codes,
        expected=expected,
        actual=actual,
        tolerance=tol,
        detail=f"{ident.ratio.code} stated {actual} vs computed {expected:.2f} (±{tol:.2f}pp)",
    )


def cross_foot(
    values: Mapping[str, FieldValue], cf: CrossFoot, abs_tol: Decimal, rel_tol: Decimal
) -> CheckResult:
    """Check that the components sum to the total within ``max(abs_tol, rel_tol×|total|)``.

    Basis-pinned like :func:`ratio_identity` (C1): every operand declares its
    expected ``(ecl, floor)`` basis, and any *present* operand whose actual basis
    differs raises :class:`CrossBasisError` — the engine refuses to fold, e.g., a
    pre-floor RWA row into a final total (a config/data error, not a check FAIL).

    SKIP if the total or any component is absent (a missing addend is missing
    input, not a failed foot); else PASS/FAIL on the tolerant comparison. The OV1
    cross-foot sums the TOP-LEVEL risk-type rows only — the "of which" sub-rows are
    never operands, so they cannot double-count (TOOLING.md §2; chunk 2.3).
    """
    operands = (cf.total, *cf.components)
    codes = tuple(op.code for op in operands)

    # C1: refuse to evaluate across bases for any operand actually present (raises
    # before any arithmetic), mirroring ratio_identity's basis pin.
    for op in operands:
        fv = values.get(op.code)
        if fv is not None:
            _assert_basis(fv, op)

    missing = [op.code for op in operands if op.code not in values]
    if missing:
        return CheckResult(
            check_type=CheckType.CROSS_FOOT,
            outcome=CheckOutcome.SKIP,
            field_codes=codes,
            expected=None,
            actual=None,
            tolerance=None,
            detail=f"SKIP: missing {', '.join(missing)}",
        )

    total = values[cf.total.code].value
    components_sum = sum((values[c.code].value for c in cf.components), Decimal("0"))
    tol = max(abs_tol, rel_tol * abs(total))
    outcome = CheckOutcome.PASS if abs(components_sum - total) <= tol else CheckOutcome.FAIL
    return CheckResult(
        check_type=CheckType.CROSS_FOOT,
        outcome=outcome,
        field_codes=codes,
        expected=components_sum,
        actual=total,
        tolerance=tol,
        detail=f"{cf.total.code} stated {total} vs components sum {components_sum} (±{tol})",
    )


def two_engine_agreement(fv: FieldValue, abs_tol: Decimal, rel_tol: Decimal) -> CheckResult:
    """Cross-check the canonical value against every *other* engine's value (M2).

    A per-field check (``field_codes=(fv.field_code,)``), run post-mapping: the two
    engines segment grids differently, so we compare the FINAL mapped numbers by
    ``field_code``, never by raw cell. The primary (Docling) value stays canonical;
    each other engine in ``engine_values`` is an independent opinion.

    SKIP if no *other* engine contributed a value (a single opinion is not a failed
    check — there is nothing to cross-check against). Otherwise PASS iff *all* other
    engines agree with the primary within ``max(abs_tol, rel_tol×|primary|)``; else
    FAIL, with the detail naming the disagreeing engines and their values. A
    disagreement is a strong misread signal (its confidence weight pushes the field
    below auto-accept), but it is not fatal on its own — one engine may still be right
    (CLAUDE.md §A.2). Basing the SKIP on "≥1 other engine" rather than a raw count
    makes the single-opinion case explicit and forecloses a vacuous PASS over an
    empty comparison set.
    """
    codes = (fv.field_code,)
    primary = fv.value
    primary_engine = fv.provenance.engine
    others = {e: v for e, v in fv.engine_values.items() if e != primary_engine}

    if not others:
        return CheckResult(
            check_type=CheckType.TWO_ENGINE,
            outcome=CheckOutcome.SKIP,
            field_codes=codes,
            expected=None,
            actual=primary,
            tolerance=None,
            detail=(
                f"SKIP: only the primary engine ({primary_engine.value}) for "
                f"{fv.field_code} — need a second engine to cross-check"
            ),
        )

    tol = max(abs_tol, rel_tol * abs(primary))
    disagreeing = {e: v for e, v in others.items() if abs(v - primary) > tol}

    if disagreeing:
        gaps = ", ".join(f"{e.value}={v}" for e, v in disagreeing.items())
        return CheckResult(
            check_type=CheckType.TWO_ENGINE,
            outcome=CheckOutcome.FAIL,
            field_codes=codes,
            expected=None,
            actual=primary,
            tolerance=tol,
            detail=f"{fv.field_code}: {primary_engine.value}={primary} disagrees with {gaps} (±{tol})",
        )

    agree = ", ".join(f"{e.value}={v}" for e, v in others.items())
    return CheckResult(
        check_type=CheckType.TWO_ENGINE,
        outcome=CheckOutcome.PASS,
        field_codes=codes,
        expected=None,
        actual=primary,
        tolerance=tol,
        detail=f"{fv.field_code}: {primary_engine.value}={primary} agrees with {agree} (±{tol})",
    )


def _magnitude_class(unit: Unit) -> str | None:
    """Map a :class:`Unit` to its magnitude-band class, or ``None`` for an unbanded unit.

    Monetary units collapse to one ``"monetary"`` class (bands are millions-normalised, so
    currency does not matter); PERCENT/RATIO map to their own class. COUNT/NONE carry no
    magnitude meaning → ``None`` → the check SKIPs (never a silent PASS). A class with no
    section in ``magnitude_bands.yaml`` (e.g. ``"ratio"`` today) also SKIPs — same safe
    fallback, resolved against the bands dict by the caller, not here.
    """
    if unit in _MONETARY_UNITS:
        return "monetary"
    if unit is Unit.PERCENT:
        return "percent"
    if unit is Unit.RATIO:
        return "ratio"
    return None


def magnitude_sanity(
    fv: FieldValue, bands: Mapping[str, tuple[Decimal, Decimal]]
) -> CheckResult:
    """Order-of-magnitude plausibility backstop — the net for a uniform scale error (H3).

    A WIDE, heuristic veto (NOT ground truth): a value outside its class band is 1000×-ish
    implausible (e.g. a $bn row read as $m), so it FAILs → the field is FLAGGED → review,
    never emitted. The ratio identities are scale-INVARIANT and miss this; the band does not.
    A field whose unit has no configured band SKIPs (an honest "not checked", never a silent
    PASS). Bounds are inclusive and millions-normalised, so the band is currency-agnostic.
    LIMIT: a wrong value inside its (wide) band still slips — the golden set stays the firewall.
    """
    codes = (fv.field_code,)
    cls = _magnitude_class(fv.unit)
    # cls is None (unbanded unit) OR cls not in bands (no section) both resolve to None via
    # dict.get → SKIP below. No guard needed: dict.get(None) is a safe miss, not an error.
    band = bands.get(cls)
    if band is None:
        return CheckResult(
            check_type=CheckType.MAGNITUDE_SANITY,
            outcome=CheckOutcome.SKIP,
            field_codes=codes,
            expected=None,
            actual=fv.value,
            tolerance=None,
            detail=f"SKIP: no magnitude band for unit {fv.unit.value} ({fv.field_code})",
        )

    lo, hi = band
    inside = lo <= fv.value <= hi
    return CheckResult(
        check_type=CheckType.MAGNITUDE_SANITY,
        outcome=CheckOutcome.PASS if inside else CheckOutcome.FAIL,
        field_codes=codes,
        expected=None,
        actual=fv.value,
        tolerance=None,
        detail=(
            f"{fv.field_code}={fv.value} {'within' if inside else 'OUTSIDE'} {cls} magnitude "
            f"band [{lo}, {hi}] ($m-normalised)"
        ),
    )
