"""Reconciliation identity structures + their typed loaders (chunk 1.5).

Loads the *structure* of each template's arithmetic checks from
``config/identities.yaml`` (which fields relate, and on which basis) and the
*tolerances* from ``config/reconciliation.yaml``. Pure config parsing — no
arithmetic here (that lives in :mod:`isda_p3.reconcile.checks`).

Every operand of a ratio identity carries its EXPECTED ``(ecl, floor)`` basis;
the checks engine refuses to evaluate when a supplied :class:`FieldValue`'s actual
basis differs, so pre-floor RWA can never be divided into a final ratio
(CLAUDE.md §A C1). Loaders are fail-fast: a malformed entry raises a ``ValueError``
naming the offending location rather than yielding a silently wrong identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import TypeVar

import yaml

from ..config import Paths
from ..models import EclBasis, FloorBasis, Template

# --- identity structures ---------------------------------------------------------


@dataclass(frozen=True)
class Operand:
    """One field in an identity, pinned to its expected basis (C1)."""

    code: str
    ecl: EclBasis
    floor: FloorBasis


@dataclass(frozen=True)
class RatioIdentity:
    """``ratio.value == numerator.value / denominator.value × factor`` (± tolerance)."""

    ratio: Operand
    numerator: Operand
    denominator: Operand
    factor: Decimal


@dataclass(frozen=True)
class CrossFoot:
    """``total.value == sum(components[i].value)`` (± tolerance)."""

    total: str
    components: tuple[str, ...]


@dataclass(frozen=True)
class TemplateIdentities:
    """A template's full identity set (frozen, like :class:`config_load.TemplateSpec`)."""

    ratio_identities: tuple[RatioIdentity, ...]
    cross_foots: tuple[CrossFoot, ...]


# --- parsing helpers -------------------------------------------------------------

_E = TypeVar("_E", bound=Enum)


def _parse_enum(enum_cls: type[_E], value: object, *, where: str) -> _E:
    try:
        return enum_cls(value)
    except ValueError:
        valid = ", ".join(m.value for m in enum_cls)
        raise ValueError(
            f"{where}: {value!r} is not a valid {enum_cls.__name__} (expected one of: {valid})"
        ) from None


def _parse_operand(raw: object, *, where: str) -> Operand:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: expected a mapping, got {type(raw).__name__}")
    code = raw.get("code")
    if not isinstance(code, str) or not code.strip():
        raise ValueError(f"{where}: missing or empty 'code'")
    ecl = _parse_enum(EclBasis, raw.get("ecl"), where=f"{where} ecl")
    floor = _parse_enum(FloorBasis, raw.get("floor"), where=f"{where} floor")
    return Operand(code=code, ecl=ecl, floor=floor)


def _parse_decimal(value: object, *, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{where}: expected a number, got {type(value).__name__}")
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{where}: not a valid number: {value!r}") from None


def _load_yaml_mapping(path: Path) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a top-level mapping, got {type(raw).__name__}")
    return raw


def _section_list(section: dict, key: str, *, where: str) -> list:
    """Return ``section[key]`` as a list. Absent/``null`` ⇒ ``[]`` (the section is
    legitimately empty); any other type raises ``ValueError`` rather than being
    silently treated as empty — a mistyped key must not drop every check (§A.2).
    """
    value = section.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{where}.{key}: expected a list, got {type(value).__name__}")
    return value


# --- loaders ---------------------------------------------------------------------


def load_identities(
    template: Template, path: Path = Paths.CONFIG / "identities.yaml"
) -> TemplateIdentities:
    """Load and validate the identity structure for ``template`` from ``identities.yaml``.

    Raises ``ValueError`` if the template has no section, or on any malformed
    identity (bad operand, non-numeric factor, empty cross-foot components) —
    each message naming the offending location.
    """
    raw = _load_yaml_mapping(path)
    section = raw.get(template.value)
    if not isinstance(section, dict):
        raise ValueError(f"{path}: no identities section for template {template.value!r}")

    base = f"{path} {template.value}"
    ratio_identities: list[RatioIdentity] = []
    for i, item in enumerate(_section_list(section, "ratio_identities", where=base)):
        where = f"{base}.ratio_identities[{i}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where}: expected a mapping, got {type(item).__name__}")
        if "factor" not in item:
            raise ValueError(f"{where}: missing 'factor'")
        ratio_identities.append(
            RatioIdentity(
                ratio=_parse_operand(item.get("ratio"), where=f"{where}.ratio"),
                numerator=_parse_operand(item.get("numerator"), where=f"{where}.numerator"),
                denominator=_parse_operand(item.get("denominator"), where=f"{where}.denominator"),
                factor=_parse_decimal(item["factor"], where=where),
            )
        )

    cross_foots: list[CrossFoot] = []
    for i, item in enumerate(_section_list(section, "cross_foots", where=base)):
        where = f"{base}.cross_foots[{i}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where}: expected a mapping, got {type(item).__name__}")
        total = item.get("total")
        if not isinstance(total, str) or not total.strip():
            raise ValueError(f"{where}: missing or empty 'total'")
        components = item.get("components")
        if (
            not isinstance(components, list)
            or not components
            or not all(isinstance(c, str) and c.strip() for c in components)
        ):
            raise ValueError(
                f"{where}: 'components' must be a non-empty list of non-empty strings"
            )
        cross_foots.append(CrossFoot(total=total, components=tuple(components)))

    return TemplateIdentities(
        ratio_identities=tuple(ratio_identities), cross_foots=tuple(cross_foots)
    )


def load_tolerances(
    path: Path = Paths.CONFIG / "reconciliation.yaml",
) -> dict[str, dict[str, Decimal]]:
    """Load per-check tolerances as ``Decimal`` (never float) from ``reconciliation.yaml``.

    Returns ``{check_name: {param: Decimal}}`` — e.g.
    ``{"ratio_identity": {"bp": Decimal("10")}, "cross_foot": {"abs": ..., "rel": ...}}``.
    """
    raw = _load_yaml_mapping(path)
    tolerances = raw.get("tolerances")
    if not isinstance(tolerances, dict):
        raise ValueError(f"{path}: 'tolerances' must be a mapping")

    out: dict[str, dict[str, Decimal]] = {}
    for check, params in tolerances.items():
        if not isinstance(params, dict):
            raise ValueError(f"{path}: tolerances.{check} must be a mapping")
        out[check] = {
            k: _parse_decimal(v, where=f"{path} tolerances.{check}.{k}")
            for k, v in params.items()
        }
    return out


# Keys :func:`isda_p3.reconcile.confidence.compute_confidence` may look up — every
# outcome path must have a weight, so absence is a hard config error caught at load.
_REQUIRED_WEIGHTS = frozenset(
    {"pass", "skip", "unchecked", "ratio_identity_fail", "cross_foot_fail"}
)


def load_weights(
    path: Path = Paths.CONFIG / "reconciliation.yaml",
) -> dict[str, Decimal]:
    """Load the confidence-weight map as ``Decimal`` from ``reconciliation.yaml``.

    Returns ``{weight_name: Decimal}`` — e.g. ``{"pass": Decimal("1.0"), "skip":
    Decimal("0.97"), "unchecked": Decimal("0.90"), "ratio_identity_fail":
    Decimal("0.0"), ...}`` — consumed by
    :func:`isda_p3.reconcile.confidence.compute_confidence`. Raises ``ValueError``
    if any weight :func:`compute_confidence` needs is absent, so a missing weight
    fails at load (§A.4), not mid-pipeline on the first field that needs it.
    """
    raw = _load_yaml_mapping(path)
    confidence = raw.get("confidence")
    if not isinstance(confidence, dict):
        raise ValueError(f"{path}: 'confidence' must be a mapping")
    weights = {
        k: _parse_decimal(v, where=f"{path} confidence.{k}") for k, v in confidence.items()
    }
    missing = _REQUIRED_WEIGHTS - weights.keys()
    if missing:
        raise ValueError(
            f"{path}: confidence section missing required weight(s): {', '.join(sorted(missing))}"
        )
    return weights
