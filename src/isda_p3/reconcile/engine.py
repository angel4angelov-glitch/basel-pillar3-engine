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
from .checks import cross_foot, ratio_identity, two_engine_agreement
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

    ``AUTO_PASSED`` iff ``confidence >= threshold`` AND no applicable check FAILed AND
    ``validation_basis`` is non-empty (i.e. at least one check actually fired); else
    ``FLAGGED``. ``validation_basis`` lists the distinct (non-SKIP) check types that
    fired, preserving order — so "non-empty validation_basis" == "≥1 fired check". The
    guard is made explicit so routing can never drift from confidence alone: a field
    whose checks all SKIPped (never validated) cannot auto-accept even if a future
    weight pushed the unchecked baseline ≥ threshold (chunk 1.9, §A.2).
    """
    my_checks = applicable_checks(fv.field_code, checks)
    confidence = compute_confidence(my_checks, weights)
    validation_basis = tuple(
        dict.fromkeys(c.check_type for c in my_checks if c.outcome is not CheckOutcome.SKIP)
    )
    has_fail = any(c.outcome is CheckOutcome.FAIL for c in my_checks)
    status = (
        ValidationStatus.AUTO_PASSED
        if confidence >= threshold and not has_fail and validation_basis
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

    Builds the full check set (cross-field ratio identities + cross-foots, plus a
    per-field two-engine agreement) once, then scores each field against the checks
    that reference it. Identity checks are cross-field; two-engine is per-field — both
    flow through ``applicable_checks`` by ``field_code`` (M2). A :class:`CrossBasisError`
    raised by a check propagates (a config/data error, never swallowed — C1).
    """
    ident = load_identities(template)
    try:
        ratio_tol = tolerances["ratio_identity"]["bp"]
        cf_abs = tolerances["cross_foot"]["abs"]
        cf_rel = tolerances["cross_foot"]["rel"]
        te_abs = tolerances["two_engine"]["abs"]
        te_rel = tolerances["two_engine"]["rel"]
    except KeyError as exc:
        raise ValueError(
            f"reconcile_template({template.value}): missing tolerance key {exc} "
            f"(have: {sorted(tolerances)})"
        ) from exc

    all_checks: list[CheckResult] = [
        ratio_identity(values, ri, ratio_tol) for ri in ident.ratio_identities
    ]
    all_checks += [cross_foot(values, cf, cf_abs, cf_rel) for cf in ident.cross_foots]
    all_checks += [two_engine_agreement(fv, te_abs, te_rel) for fv in values.values()]

    return [reconcile_field(fv, all_checks, weights, threshold) for fv in values.values()]
