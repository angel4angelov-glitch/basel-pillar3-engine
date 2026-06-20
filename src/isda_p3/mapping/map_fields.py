"""Rule-first template mapping: a ``RawCell`` grid -> ``list[FieldValue]`` (chunk 1.3).

Deterministic, LLM-free (CLAUDE.md §A — the LLM is judgment glue, never the
ledger; the bounded LLM fallback for unmatched labels lands in chunk 1.4). The
mapper turns a grid of verbatim cells into canonical :class:`FieldValue`s by
*exact* normalised-label match against a template's row-label aliases, then reads
the first cell to the right of the label that parses as a number.

Algorithm (each step documented inline):
  1. Group cells by ``row_idx``; a row's LABEL is its leftmost cell that still has
     alphabetic content after stripping a leading enumerator ("1", "1a", "UK 7a").
  2. RULE match = a normalised alias EXACTLY equals the normalised row label.
     Exact only — never a substring (so "Tier 1 ratio" never maps to "Tier 1
     capital"). Unmatched fields are returned for the chunk-1.4 LLM fallback.
  3. The VALUE is the first cell right of the label that ``normalise`` accepts.
     KM1 orders periods most-recent-first, so that first numeric cell is the
     current period. A cell that fails to normalise (dash, blank, footnote-only)
     is skipped, never coerced to 0.
  4. If no cell normalises, the field is ABSENT: it is logged and returned in the
     unmatched list, never emitted as a defaulted/zero value (CLAUDE.md §A.2 / C2).
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal

from ..config_load import FieldSpec, TemplateSpec
from ..models import (
    Bank,
    Engine,
    FieldValue,
    MappingDecision,
    MappingMethod,
    Provenance,
    RawCell,
    ReportingPeriod,
    SourceKind,
    Unit,
    unit_for,
)
from .normalise import NormalisationError, normalise

log = logging.getLogger(__name__)

# A leading enumerator: optional surrounding punctuation, an optional "UK"
# jurisdiction prefix, a row number, an optional letter suffix (1a / 7b), then one
# optional separator char and trailing space. The ``(?=\s|$)`` lookahead means the
# enumerator must end at whitespace or end-of-string, so "1."/"1)"/"UK 7a" strip
# but a decimal like "14.8" is never mistaken for an enumerator (the '.' is not a
# boundary there). Anchored to the start; applied once.
_ENUM_RE = re.compile(r"^[\s.)(]*(?:uk\s+)?\d+[a-z]?[.):]?(?=\s|$)\s*")
_WS_RE = re.compile(r"\s+")
# A trailing footnote reference. Pillar 3 tables footnote rows (e.g. "Total capital¹",
# "Liquidity coverage ratio (%)³"); Docling renders the superscript as a trailing
# " 1"/" 3" (space + 1-2 ASCII digits). Strip it so the row still matches its alias —
# the label-side analogue of stripping footnotes from *values* in mapping.normalise.
# Anchored to the END only and limited to 1-2 digits: no real KM1/OV1 row label ends
# in a bare standalone number, so a genuine label is never truncated.
_FOOTNOTE_TAIL_RE = re.compile(r"\s+\d{1,2}$")


def _norm_label(text: str) -> str:
    """Lowercase, collapse whitespace, strip a leading enumerator and a trailing footnote.

    Applied identically to both aliases and row labels so that matching is exact
    equality on the cleaned strings.
    """
    s = _WS_RE.sub(" ", text.strip().lower())
    s = _ENUM_RE.sub("", s, count=1)
    stripped = _FOOTNOTE_TAIL_RE.sub("", s)
    if stripped != s:
        # Footnote stripped. No current KM1/OV1 label ends in a bare number, but log it
        # so that if a future template's label legitimately ends in a tier number, the
        # resulting mis-map is diagnosable rather than silent (CLAUDE.md §A.2).
        log.debug("_norm_label: stripped trailing footnote %r -> %r", s, stripped)
    return stripped.strip()


def _is_label_text(text: str) -> bool:
    """True if the cell reads as a row label (has letters once the enumerator is gone)."""
    return any(ch.isalpha() for ch in _norm_label(text))


def _find_label_cell(row_cells: list[RawCell]) -> RawCell | None:
    """The leftmost cell in the row that reads as a label, or ``None`` if the row has none."""
    for cell in row_cells:  # row_cells is sorted left-to-right by col_idx
        if _is_label_text(cell.text):
            return cell
    return None


def _first_value(
    row_cells: list[RawCell], label_cell: RawCell, locale: str, unit: Unit, code: str
) -> tuple[RawCell, Decimal] | None:
    """First cell right of the label that ``normalise`` accepts, with its value.

    Cells that raise :class:`NormalisationError` (dashes, blanks, footnote-only,
    misaligned percents) are logged and skipped — the parser advances rather than
    admitting a wrong digit. Returns ``None`` if no cell in the row normalises.
    """
    for cell in row_cells:
        if cell.col_idx <= label_cell.col_idx:
            continue
        try:
            return cell, normalise(cell.text, locale, unit)
        except NormalisationError as exc:
            log.debug("map %s: skip non-numeric cell %r (%s)", code, cell.text, exc)
    return None


def _group_rows(cells: list[RawCell]) -> dict[int, list[RawCell]]:
    """Group cells by ``row_idx``, each row sorted left-to-right by ``col_idx``."""
    rows: dict[int, list[RawCell]] = {}
    for cell in cells:
        rows.setdefault(cell.row_idx, []).append(cell)
    for row in rows.values():
        row.sort(key=lambda c: c.col_idx)
    return rows


def _build_label_index(rows: dict[int, list[RawCell]]) -> dict[str, tuple[RawCell, list[RawCell]]]:
    """Map each normalised row label -> (label cell, full row).

    Rows are visited in ascending ``row_idx`` so that on a label collision the
    *topmost* row deterministically wins (independent of the engine's cell order).
    """
    index: dict[str, tuple[RawCell, list[RawCell]]] = {}
    for row_idx in sorted(rows):
        row_cells = rows[row_idx]
        label_cell = _find_label_cell(row_cells)
        if label_cell is None:
            continue
        key = _norm_label(label_cell.text)
        if not key:
            continue
        if key in index:
            log.debug("label collision on %r — keeping topmost row, ignoring row_idx=%s", key, row_idx)
            continue
        index[key] = (label_cell, row_cells)
    return index


def _match_alias(
    spec_field: FieldSpec, index: dict[str, tuple[RawCell, list[RawCell]]]
) -> tuple[str, RawCell, list[RawCell]] | None:
    """Return ``(alias, label_cell, row_cells)`` for the first alias that matches, else ``None``."""
    for alias in spec_field.row_label_aliases:
        hit = index.get(_norm_label(alias))
        if hit is not None:
            return alias, hit[0], hit[1]
    return None


def map_fields(
    cells: list[RawCell],
    spec: TemplateSpec,
    bank: Bank,
    period: ReportingPeriod,
    source_url: str,
    source_kind: SourceKind,
    engine: Engine,
) -> tuple[list[FieldValue], list[str]]:
    """Map a ``RawCell`` grid to canonical :class:`FieldValue`s by exact label rule.

    Returns ``(values, unmatched)`` where ``unmatched`` holds the field codes that
    had no matching row label *or* no parseable value cell — the input to the
    chunk-1.4 LLM fallback. No value is ever fabricated: an absent or unparseable
    figure surfaces in ``unmatched``, never as a zero (CLAUDE.md §A.2 / C2).
    """
    index = _build_label_index(_group_rows(cells))
    currency = bank.reporting_currency

    values: list[FieldValue] = []
    unmatched: list[str] = []

    for field in spec.fields:
        match = _match_alias(field, index)
        if match is None:
            unmatched.append(field.code)
            continue
        alias, label_cell, row_cells = match

        unit = unit_for(field.kind, currency)
        found = _first_value(row_cells, label_cell, bank.number_locale, unit, field.code)
        if found is None:
            log.warning(
                "map %s: row %r matched alias %r but no cell normalised — treating as absent",
                field.code,
                label_cell.text,
                alias,
            )
            unmatched.append(field.code)
            continue
        value_cell, value = found

        values.append(
            FieldValue(
                template=spec.template,
                field_code=field.code,
                value=value,
                unit=unit,
                ecl_basis=field.ecl_basis,
                floor_basis=field.floor_basis,
                provenance=Provenance(
                    bank_id=bank.id,
                    period=period,
                    source_url=source_url,
                    source_kind=source_kind,
                    engine=engine,
                    bbox=value_cell.bbox,
                ),
                mapping=MappingDecision(
                    method=MappingMethod.RULE,
                    model=None,
                    prompt_sha=None,
                    prompt_version=None,
                    matched_alias=alias,
                    confidence=Decimal("1"),
                ),
                raw_text=value_cell.text,
                engine_values={engine: value},
            )
        )

    return values, unmatched
