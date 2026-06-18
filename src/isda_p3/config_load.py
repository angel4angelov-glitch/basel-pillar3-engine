"""Typed loaders for ``config/banks.yaml`` and ``config/templates/*.yaml``.

Stdlib + PyYAML only. Every loader is fail-fast: any malformed entry raises a
``ValueError`` whose message names the offending bank id / field code, so a config
mistake surfaces loudly at startup rather than as a silent downstream bad number
(CLAUDE.md §A — no silent failures, validate at boundaries).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TypeVar

import yaml

from isda_p3.config import Paths
from isda_p3.models import (
    Bank,
    EclBasis,
    FloorBasis,
    Jurisdiction,
    Template,
    Unit,
)

# --- template specs --------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    """One canonical field of a Pillar 3 template (from ``templates/*.yaml``)."""

    code: str
    row_label_aliases: tuple[str, ...]
    unit: Unit
    ecl_basis: EclBasis
    floor_basis: FloorBasis


@dataclass(frozen=True)
class TemplateSpec:
    """A template's full field set, loaded and validated."""

    template: Template
    fields: tuple[FieldSpec, ...]

    def by_code(self, code: str) -> FieldSpec | None:
        """Return the :class:`FieldSpec` with ``code``, or ``None`` if absent."""
        return next((f for f in self.fields if f.code == code), None)


# --- helpers ---------------------------------------------------------------------

_REQUIRED_BANK_FIELDS = (
    "name",
    "jurisdiction",
    "ir_url",
    "number_locale",
    "reporting_currency",
)
_BANK_STR_FIELDS = ("name", "ir_url", "number_locale")

_E = TypeVar("_E", bound=Enum)


def _parse_enum(enum_cls: type[_E], value: object, *, where: str) -> _E:
    """Coerce ``value`` into ``enum_cls`` or raise a ``ValueError`` naming ``where``."""
    try:
        return enum_cls(value)
    except ValueError:
        valid = ", ".join(m.value for m in enum_cls)
        raise ValueError(
            f"{where}: {value!r} is not a valid {enum_cls.__name__} (expected one of: {valid})"
        ) from None


def _load_yaml_mapping(path: Path) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a top-level mapping, got {type(raw).__name__}")
    return raw


# --- banks -----------------------------------------------------------------------


def load_banks(path: Path = Paths.CONFIG / "banks.yaml") -> tuple[Bank, ...]:
    """Parse, validate and construct the bank roster from ``banks.yaml``.

    Fail-fast on: missing/empty required fields, unknown jurisdiction, currency
    that is not 3-letter uppercase ISO 4217, and duplicate ids. ``p3dh_lei`` may
    be ``None``. The error message always names the offending bank.
    """
    raw = _load_yaml_mapping(path)
    entries = raw.get("banks")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: 'banks' must be a non-empty list")

    banks: list[Bank] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"{path} banks[{i}]: expected a mapping, got {type(entry).__name__}")

        bank_id = entry.get("id")
        if not isinstance(bank_id, str) or not bank_id.strip():
            raise ValueError(f"{path} banks[{i}]: missing or empty 'id'")

        for key in _REQUIRED_BANK_FIELDS:
            if entry.get(key) is None:  # absent OR explicit null both count as missing
                raise ValueError(f"bank {bank_id!r}: missing required field {key!r}")

        if bank_id in seen:
            raise ValueError(f"bank {bank_id!r}: duplicate id")
        seen.add(bank_id)

        jurisdiction = _parse_enum(
            Jurisdiction, entry["jurisdiction"], where=f"bank {bank_id!r} jurisdiction"
        )

        currency = entry["reporting_currency"]
        if not (
            isinstance(currency, str)
            and len(currency) == 3
            and currency.isalpha()
            and currency.isupper()
        ):
            raise ValueError(
                f"bank {bank_id!r}: reporting_currency must be a 3-letter uppercase "
                f"ISO 4217 code, got {currency!r}"
            )

        for key in _BANK_STR_FIELDS:
            val = entry[key]
            if not (isinstance(val, str) and val.strip()):
                raise ValueError(f"bank {bank_id!r}: {key!r} must be a non-empty string")

        lei = entry.get("p3dh_lei")
        if lei is not None and not (isinstance(lei, str) and lei.strip()):
            raise ValueError(f"bank {bank_id!r}: p3dh_lei must be a non-empty string or null")

        banks.append(
            Bank(
                id=bank_id,
                name=entry["name"],
                jurisdiction=jurisdiction,
                ir_url=entry["ir_url"],
                p3dh_lei=lei,
                number_locale=entry["number_locale"],
                reporting_currency=currency,
            )
        )

    return tuple(banks)


# --- templates -------------------------------------------------------------------


def load_template(t: Template) -> TemplateSpec:
    """Load and validate ``config/templates/{t}.yaml`` into a :class:`TemplateSpec`.

    Raises ``FileNotFoundError`` if the file is missing, and ``ValueError`` on a
    template-tag mismatch, duplicate field codes, empty aliases, or any
    unit/basis value outside its enum — each message naming the offending field.
    """
    path = Paths.TEMPLATES / f"{t.value.lower()}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"template config not found for {t}: {path}")

    raw = _load_yaml_mapping(path)
    declared = _parse_enum(Template, raw.get("template"), where=f"{path} 'template'")
    if declared is not t:
        raise ValueError(f"{path}: declares template {declared} but {t} was requested")

    raw_fields = raw.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ValueError(f"{path}: 'fields' must be a non-empty list")

    fields: list[FieldSpec] = []
    seen: set[str] = set()
    for i, fld in enumerate(raw_fields):
        if not isinstance(fld, dict):
            raise ValueError(f"{path} fields[{i}]: expected a mapping, got {type(fld).__name__}")

        code = fld.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"{path} fields[{i}]: missing or empty 'code'")
        if code in seen:
            raise ValueError(f"{path}: duplicate field code {code!r}")
        seen.add(code)

        aliases = fld.get("row_label_aliases")
        if (
            not isinstance(aliases, list)
            or not aliases
            or not all(isinstance(a, str) and a.strip() for a in aliases)
        ):
            raise ValueError(
                f"field {code!r}: row_label_aliases must be a non-empty list of non-empty strings"
            )

        unit = _parse_enum(Unit, fld.get("unit"), where=f"field {code!r} unit")
        ecl = _parse_enum(EclBasis, fld.get("ecl_basis"), where=f"field {code!r} ecl_basis")
        floor = _parse_enum(FloorBasis, fld.get("floor_basis"), where=f"field {code!r} floor_basis")

        fields.append(
            FieldSpec(
                code=code,
                row_label_aliases=tuple(aliases),
                unit=unit,
                ecl_basis=ecl,
                floor_basis=floor,
            )
        )

    return TemplateSpec(template=t, fields=tuple(fields))
