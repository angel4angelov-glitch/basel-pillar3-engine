"""Confidence scoring (chunk 1.6) — a weighted PRODUCT over a field's checks.

Pure, ``Decimal`` throughout, no I/O. The product (not a mean) is deliberate: one
hard FAIL (weight 0.0) floors confidence to ~0, so it dominates and the field is
guaranteed below the auto-accept threshold (CLAUDE.md §A — no number auto-accepts
on a failed check). A field touched by NO check returns the ``unchecked`` baseline
(0.90 < 0.95), never 1.0: an unvalidated number must not silently auto-accept, so it
routes to human review (§A.2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from ..models import CheckOutcome, CheckResult

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _weight(weights: Mapping[str, Decimal], key: str) -> Decimal:
    """Look up a weight, raising a loud config error (never a silent default) if absent."""
    try:
        return weights[key]
    except KeyError:
        raise ValueError(
            f"missing confidence weight {key!r} (have: {', '.join(sorted(weights))})"
        ) from None


def compute_confidence(
    checks: Sequence[CheckResult], weights: Mapping[str, Decimal]
) -> Decimal:
    """Weighted product of per-check factors, clamped to ``[0, 1]``.

    PASS → ``weights["pass"]`` (1.0); SKIP → ``weights["skip"]`` (0.97);
    FAIL → ``weights[f"{check_type}_fail"]`` (e.g. ``ratio_identity_fail`` → 0.0).
    Empty ``checks`` → ``weights["unchecked"]`` (0.90) — the unvalidated baseline.
    """
    if not checks:
        return _clamp(_weight(weights, "unchecked"))

    confidence = _ONE
    for check in checks:
        if check.outcome is CheckOutcome.PASS:
            confidence *= _weight(weights, "pass")
        elif check.outcome is CheckOutcome.SKIP:
            confidence *= _weight(weights, "skip")
        elif check.outcome is CheckOutcome.FAIL:
            confidence *= _weight(weights, f"{check.check_type.value.lower()}_fail")
        else:  # pragma: no cover — guards against a future CheckOutcome member
            raise ValueError(f"unhandled CheckOutcome {check.outcome!r}")
    return _clamp(confidence)


def _clamp(value: Decimal) -> Decimal:
    return max(_ZERO, min(_ONE, value))
