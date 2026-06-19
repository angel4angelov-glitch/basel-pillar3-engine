"""Structured ingest: an EBA P3DH KM1 XBRL-CSV → canonical FieldValues (chunk 3.2).

THE HYBRID PROOF (plan D1 / audit M1): structured data flows through the SAME canonical
:class:`~isda_p3.models.FieldValue` model and the SAME reconciliation gate as the PDF
path — only provenance differs (``engine=P3DH``, ``source_kind=XBRL_CSV``, ``bbox=None``).
There is no extraction risk here: XBRL values are machine-readable and canonical
('.' decimal, no thousands separators, locale-independent), so the parse path is fixed and
does NOT use the bank's ``number_locale`` (that drives PDF cell parsing only).

The parser is **config-driven** (CLAUDE.md §A.5): every datapoint→field mapping lives in
``config/p3dh_km1_map.yaml``, so refining the (currently illustrative) EBA DPM datapoint ids
is a config edit, never a code change. The auto-fetch from P3DH is intentionally NOT built —
no public EBA API is verified (audit M1); this module parses a *manually downloaded* file.

Honesty rules (CLAUDE.md §A.2): an unmapped datapoint is ignored (logged, never fabricated);
a mapped datapoint whose value will not parse makes the field ABSENT (logged, never 0); a
malformed CSV raises clearly rather than yielding a partial silent result.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

import yaml

from ..config import Paths
from ..config_load import load_template
from ..models import (
    Bank,
    Engine,
    FieldValue,
    MappingDecision,
    MappingMethod,
    Provenance,
    ReportingPeriod,
    SourceKind,
    Template,
    unit_for,
)

log = logging.getLogger(__name__)

# Required columns of a P3DH KM1 XBRL-CSV. A file missing either is malformed.
_DATAPOINT_COL = "datapoint"
_VALUE_COL = "value"

# XBRL numeric values are canonical: optional sign, digits, optional '.' fraction. No
# thousands separators, no currency symbols, no percent — those belong to the PDF path.
_CANONICAL_NUM_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_HUNDRED = Decimal("100")


class StoredAs(StrEnum):
    """How an XBRL cell represents a figure → how to emit the canonical KM1 value.

    ``AMOUNT`` passes through (assumed already in the template's canonical millions
    unit — see the TODO-verify in ``p3dh_km1_map.yaml``); ``FRACTION`` (0.136) is
    multiplied by 100 to the canonical PERCENT (13.6); ``PERCENT`` (13.6) passes through.
    """

    AMOUNT = "AMOUNT"
    FRACTION = "FRACTION"
    PERCENT = "PERCENT"


@dataclass(frozen=True)
class DatapointMapping:
    """One ``p3dh_km1_map.yaml`` entry: a datapoint id → canonical field + representation."""

    datapoint: str
    field_code: str
    stored_as: StoredAs


# --- config map loader -----------------------------------------------------------


def load_p3dh_km1_map(
    path: Path = Paths.CONFIG / "p3dh_km1_map.yaml",
) -> dict[str, DatapointMapping]:
    """Load + validate the datapoint→field map, keyed by datapoint id (fail-fast).

    Raises ``ValueError`` on a wrong template tag, a malformed/duplicate entry, or an
    unknown ``stored_as`` — each message naming the offending location, so a config
    mistake surfaces loudly rather than as a silently dropped or mis-mapped figure
    (CLAUDE.md §A — no silent failures, validate at boundaries).
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a top-level mapping, got {type(raw).__name__}")

    declared = raw.get("template")
    if declared != Template.KM1.value:
        raise ValueError(f"{path}: 'template' must be {Template.KM1.value!r}, got {declared!r}")

    entries = raw.get("datapoints")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: 'datapoints' must be a non-empty list")

    out: dict[str, DatapointMapping] = {}
    for i, entry in enumerate(entries):
        where = f"{path} datapoints[{i}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where}: expected a mapping, got {type(entry).__name__}")

        datapoint = entry.get("datapoint")
        if not isinstance(datapoint, str) or not datapoint.strip():
            raise ValueError(f"{where}: missing or empty 'datapoint'")
        if datapoint in out:
            raise ValueError(f"{where}: duplicate datapoint {datapoint!r}")

        field_code = entry.get("field_code")
        if not isinstance(field_code, str) or not field_code.strip():
            raise ValueError(f"{where}: missing or empty 'field_code'")

        stored_raw = entry.get("stored_as")
        if not isinstance(stored_raw, str):
            raise ValueError(f"{where}: 'stored_as' is missing or not a string, got {stored_raw!r}")
        try:
            stored_as = StoredAs(stored_raw)
        except ValueError:
            valid = ", ".join(m.value for m in StoredAs)
            raise ValueError(
                f"{where}: stored_as {stored_raw!r} is not one of: {valid}"
            ) from None

        out[datapoint] = DatapointMapping(
            datapoint=datapoint, field_code=field_code, stored_as=stored_as
        )

    return out


# --- numeric parse ---------------------------------------------------------------


def _parse_canonical(raw: str) -> Decimal:
    """Parse a verbatim XBRL cell via the FIXED canonical path. Raises on anything else.

    XBRL is locale-independent ('.' decimal, no grouping), so this is deliberately strict
    and does NOT consult the bank locale: a stray comma, symbol, or word is junk here, not
    a number to be salvaged. Raising lets the caller mark the field absent (never 0).
    """
    s = raw.strip()
    if not _CANONICAL_NUM_RE.fullmatch(s):
        raise ValueError(f"not a canonical XBRL number: {raw!r}")
    try:
        parsed = Decimal(s)
    except InvalidOperation as exc:  # defensive — the regex should already preclude this
        raise ValueError(f"cannot parse as Decimal: {raw!r}") from exc
    # NaN/inf can never enter the ledger (the regex precludes them today; this guards a
    # future regex widening from silently admitting a non-finite figure — CLAUDE.md §A.2).
    if not parsed.is_finite():
        raise ValueError(f"non-finite XBRL value: {raw!r}")
    return parsed


def _to_canonical(value: Decimal, stored_as: StoredAs) -> Decimal:
    """Apply ``stored_as`` to emit the canonical KM1 representation (Decimal-only)."""
    if stored_as is StoredAs.FRACTION:
        return value * _HUNDRED  # 0.136 → 13.6 (canonical PERCENT)
    return value  # AMOUNT and PERCENT pass through unchanged


# --- main parser -----------------------------------------------------------------


def _read_text(source: Path | bytes) -> str:
    """Decode the source to text, raising a clear ``ValueError`` on undecodable bytes.

    The docstring of :func:`parse_km1_xbrl_csv` promises a malformed file raises
    ``ValueError``; a raw ``UnicodeDecodeError`` would break that contract for a caller
    catching ``ValueError`` (CLAUDE.md §A — fail clearly, never past the handler).
    """
    try:
        if isinstance(source, bytes):
            return source.decode("utf-8")
        return source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        label = "<bytes>" if isinstance(source, bytes) else str(source)
        raise ValueError(f"malformed XBRL-CSV: cannot decode {label} as UTF-8: {exc}") from exc


def parse_km1_xbrl_csv(
    source: Path | bytes,
    *,
    bank: Bank,
    period: ReportingPeriod,
    source_url: str,
) -> list[FieldValue]:
    """Parse a manually-downloaded P3DH KM1 XBRL-CSV into canonical :class:`FieldValue`s.

    Each row whose ``datapoint`` is in ``p3dh_km1_map.yaml`` becomes one FieldValue with
    ``engine=P3DH``, ``source_kind=XBRL_CSV``, ``bbox=None`` — identical in every other
    respect to a PDF-path value, so it clears the same reconciliation gate. Basis axes,
    field kind (→ unit), come from the KM1 :class:`~isda_p3.config_load.TemplateSpec`.

    Unknown datapoints are ignored (logged); a mapped datapoint with an unparseable value
    yields NO FieldValue (logged, never 0); a malformed CSV (missing required columns,
    undecodable bytes) raises ``ValueError`` (CLAUDE.md §A.2).
    """
    mapping = load_p3dh_km1_map()
    spec = load_template(Template.KM1)

    reader = csv.DictReader(io.StringIO(_read_text(source)))
    if reader.fieldnames is None:
        raise ValueError("malformed XBRL-CSV: empty file (no header row)")
    missing_cols = {_DATAPOINT_COL, _VALUE_COL} - set(reader.fieldnames)
    if missing_cols:
        raise ValueError(
            f"malformed XBRL-CSV: missing required column(s) {sorted(missing_cols)} "
            f"(have: {reader.fieldnames})"
        )

    values: list[FieldValue] = []
    for line_no, record in enumerate(reader, start=1):
        datapoint = (record.get(_DATAPOINT_COL) or "").strip()
        if not datapoint:
            raise ValueError(f"malformed XBRL-CSV: empty datapoint at data line {line_no}")

        dp = mapping.get(datapoint)
        if dp is None:
            log.debug("p3dh: ignoring unmapped datapoint %r (line %s)", datapoint, line_no)
            continue

        field = spec.by_code(dp.field_code)
        if field is None:  # config map references a field the template does not declare
            raise ValueError(
                f"p3dh_km1_map.yaml: datapoint {datapoint!r} maps to unknown KM1 field "
                f"{dp.field_code!r}"
            )

        raw_value = record.get(_VALUE_COL) or ""
        try:
            parsed = _parse_canonical(raw_value)
        except ValueError as exc:
            log.warning(
                "p3dh: %s (datapoint %r) value %r will not parse — treating as absent (%s)",
                dp.field_code,
                datapoint,
                raw_value,
                exc,
            )
            continue

        value = _to_canonical(parsed, dp.stored_as)
        unit = unit_for(field.kind, bank.reporting_currency)
        values.append(
            FieldValue(
                template=Template.KM1,
                field_code=dp.field_code,
                value=value,
                unit=unit,
                ecl_basis=field.ecl_basis,
                floor_basis=field.floor_basis,
                provenance=Provenance(
                    bank_id=bank.id,
                    period=period,
                    source_url=source_url,
                    source_kind=SourceKind.XBRL_CSV,
                    engine=Engine.P3DH,
                    bbox=None,
                ),
                mapping=MappingDecision(
                    method=MappingMethod.RULE,
                    model=None,
                    prompt_sha=None,
                    prompt_version=None,
                    matched_alias=datapoint,
                    confidence=Decimal("1"),
                ),
                raw_text=raw_value,
                engine_values={Engine.P3DH: value},
            )
        )

    return values
