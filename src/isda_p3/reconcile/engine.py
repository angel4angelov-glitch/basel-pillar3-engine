"""Reconciliation engine (chunk 1.6) — checks → confidence → auto-accept/queue routing.

Ties the deterministic checks (:mod:`isda_p3.reconcile.checks`) to the confidence
product (:mod:`isda_p3.reconcile.confidence`) and decides each field's
:class:`ValidationStatus`. A field is ``AUTO_PASSED`` only when it clears the
threshold AND no applicable check FAILed; otherwise it is ``FLAGGED`` for human
review (CLAUDE.md §A.2 — no number enters the dataset on a failed/absent check).
``validation_basis`` records which check types actually fired (non-SKIP), so the
audit trail states what was validated rather than implying universal coverage (C3).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from .. import config
from ..models import (
    CheckOutcome,
    CheckResult,
    FieldValue,
    ReconciliationResult,
    Template,
    ValidationStatus,
)
from .checks import cross_foot, ratio_identity
from .confidence import compute_confidence
from .identities import load_identities


def applicable_checks(
    field_code: str, checks: Sequence[CheckResult]
) -> tuple[CheckResult, ...]:
    """The subset of ``checks`` whose ``field_codes`` references ``field_code``."""
    return tuple(c for c in checks if field_code in c.field_codes)


def reconcile_field(
    fv: FieldValue,
    checks: Sequence[CheckResult],
    weights: Mapping[str, Decimal],
    threshold: Decimal,
) -> ReconciliationResult:
    """Score one field against the checks that touch it and decide its status.

    ``AUTO_PASSED`` iff ``confidence >= threshold`` AND no applicable check FAILed;
    else ``FLAGGED``. ``validation_basis`` lists the distinct (non-SKIP) check types
    that actually fired, preserving order.
    """
    my_checks = applicable_checks(fv.field_code, checks)
    confidence = compute_confidence(my_checks, weights)
    validation_basis = tuple(
        dict.fromkeys(c.check_type for c in my_checks if c.outcome is not CheckOutcome.SKIP)
    )
    has_fail = any(c.outcome is CheckOutcome.FAIL for c in my_checks)
    status = (
        ValidationStatus.AUTO_PASSED
        if confidence >= threshold and not has_fail
        else ValidationStatus.FLAGGED
    )
    return ReconciliationResult(
        field_value=fv,
        checks=my_checks,
        confidence=confidence,
        validation_basis=validation_basis,
        status=status,
    )


def reconcile_template(
    values: Mapping[str, FieldValue],
    template: Template,
    *,
    tolerances: Mapping[str, Mapping[str, Decimal]],
    weights: Mapping[str, Decimal],
    threshold: Decimal = config.CONFIDENCE_AUTO_ACCEPT,
) -> list[ReconciliationResult]:
    """Run a template's identities over ``values`` and route every field.

    Builds the full check set (ratio identities + cross-foots) once, then scores
    each field against the checks that reference it. A :class:`CrossBasisError`
    raised by a check propagates (a config/data error, never swallowed — C1).
    """
    ident = load_identities(template)
    try:
        ratio_tol = tolerances["ratio_identity"]["bp"]
        cf_abs = tolerances["cross_foot"]["abs"]
        cf_rel = tolerances["cross_foot"]["rel"]
    except KeyError as exc:
        raise ValueError(
            f"reconcile_template({template.value}): missing tolerance key {exc} "
            f"(have: {sorted(tolerances)})"
        ) from exc

    all_checks: list[CheckResult] = [
        ratio_identity(values, ri, ratio_tol) for ri in ident.ratio_identities
    ]
    all_checks += [cross_foot(values, cf, cf_abs, cf_rel) for cf in ident.cross_foots]

    return [reconcile_field(fv, all_checks, weights, threshold) for fv in values.values()]
