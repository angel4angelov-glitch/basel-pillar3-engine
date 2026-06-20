"""Bounded LLM fallback mapper: map *unmatched* field codes to table rows (chunk 1.4).

This is the one seam where the LLM enters the numeric path, and it is bounded hard
by CLAUDE.md §A (the LLM is judgment glue, never the ledger):

  - The LLM only **chooses which row** an unmatched field code corresponds to. It
    never emits, echoes, or influences a digit.
  - The value is STILL extracted deterministically from the chosen row by the SAME
    rule as :mod:`map_fields` (``normalise`` on the first numeric cell right of the
    label) — reused here, never reimplemented.
  - Every LLM decision is persisted to a :class:`MappingDecision` (``model``,
    ``prompt_sha``, ``prompt_version``, ``matched_alias``, ``confidence``). An
    unlogged LLM judgment in the path is an audit failure, so the audit record is
    built unconditionally for every emitted value.

A null row, a row that is not in the grid, or a row whose value cell will not
normalise leaves the field ABSENT (logged with a reason). No value is ever
fabricated; no number is ever taken from the LLM output (§A.2).

Tests inject a stub :class:`StructuredMapper`; the real Anthropic wrapper
(:class:`AnthropicMapper`) lazily imports ``langchain_anthropic`` so the core stays
key-free and network-free.
"""

from __future__ import annotations

import hashlib
import logging
import math
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, Field, field_validator

from .. import config
from ..config_load import TemplateSpec
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
    unit_for,
)
from .map_fields import _build_label_index, _first_value, _group_rows, _norm_label
from .normalise import scale_multiplier

log = logging.getLogger(__name__)


# --- structured output contract --------------------------------------------------


class RowMatch(BaseModel):
    """One LLM decision: which row (if any) an unmatched field code maps to."""

    field_code: str
    row_label: str | None = Field(
        default=None,
        description="The verbatim candidate row label this field maps to, or null if none fits.",
    )
    confidence: float

    @field_validator("confidence")
    @classmethod
    def _finite_unit_interval(cls, v: float) -> float:
        """Reject NaN/inf/out-of-range at the LLM boundary (a confidence is a probability).

        A non-finite confidence would corrupt the audit record and detonate later as a
        cryptic ``InvalidOperation`` in reconciliation — fail loud here instead (§A).
        """
        if not math.isfinite(v) or not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be a finite float in [0, 1], got {v!r}")
        return v


class RowMatchSet(BaseModel):
    """The full set of row decisions returned for one prompt."""

    matches: list[RowMatch]


class StructuredMapper(Protocol):
    """Injectable mapper boundary (mirrors boe-rag's ``with_structured_output``).

    Tests inject a stub; the real implementation is :class:`AnthropicMapper`.
    """

    def map_rows(self, prompt: str) -> RowMatchSet: ...


# --- prompt rendering ------------------------------------------------------------


def _render_prompt(
    unmatched_codes: list[str], spec: TemplateSpec, candidate_labels: list[str]
) -> str:
    """Render the deterministic mapping prompt (stable ordering ⇒ stable sha)."""
    lines = [
        f"Map unmatched Basel Pillar 3 {spec.template.value} fields to table rows.",
        "",
        "For each FIELD, choose the single CANDIDATE ROW label (verbatim) whose figure "
        "is that field's value, or null if no row fits. Choose only from the candidate "
        "rows; never invent a row and never return a number.",
        "",
        "FIELDS:",
    ]
    for code in unmatched_codes:
        field = spec.by_code(code)
        aliases = " | ".join(field.row_label_aliases) if field is not None else ""
        lines.append(f"- {code}: {aliases}")
    lines.append("")
    lines.append("CANDIDATE ROWS:")
    lines.extend(f"- {label}" for label in candidate_labels)
    return "\n".join(lines)


def _unused_candidates(
    unmatched_codes: list[str],
    spec: TemplateSpec,
    index: dict[str, tuple[RawCell, list[RawCell]]],
) -> list[str]:
    """Verbatim labels of rows NOT already claimed by a rule-matched field.

    A row is "used" if it is the alias-match of a field that is *not* unmatched —
    i.e. the rule mapper already consumed it. Offering only the leftovers keeps the
    LLM from re-mapping a row the deterministic pass already owns. Order is inherited
    from ``_build_label_index`` (which visits rows in ascending ``row_idx``), so the
    candidate list — and therefore the prompt sha — is order-independent of the input
    cell list.
    """
    unmatched_set = set(unmatched_codes)
    used: set[str] = set()
    for field in spec.fields:
        if field.code in unmatched_set:
            continue
        for alias in field.row_label_aliases:
            key = _norm_label(alias)
            if key in index:
                used.add(key)
                break
    return [label_cell.text for key, (label_cell, _) in index.items() if key not in used]


# --- the bounded fallback mapper -------------------------------------------------


def map_unmatched(
    unmatched_codes: list[str],
    cells: list[RawCell],
    spec: TemplateSpec,
    bank: Bank,
    period: ReportingPeriod,
    source_url: str,
    source_kind: SourceKind,
    engine: Engine,
    mapper: StructuredMapper,
) -> list[FieldValue]:
    """LLM-map the leftover field codes to rows; extract their digits deterministically.

    Returns only the :class:`FieldValue`s the LLM could ground in a real, parseable
    row. Anything else stays absent and is logged — never fabricated.
    """
    if not unmatched_codes:
        return []

    index = _build_label_index(_group_rows(cells))
    candidate_labels = _unused_candidates(unmatched_codes, spec, index)
    prompt = _render_prompt(unmatched_codes, spec, candidate_labels)
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    model = config.MAP_MODEL_SIMPLE

    result = mapper.map_rows(prompt)
    unmatched_set = set(unmatched_codes)
    currency = bank.reporting_currency
    scale = scale_multiplier(bank.monetary_scale)

    values: list[FieldValue] = []
    for match in result.matches:
        code = match.field_code
        if code not in unmatched_set:
            log.warning("llm-map: ignoring out-of-scope field %r (not in unmatched set)", code)
            continue
        field = spec.by_code(code)
        if field is None:
            log.warning("llm-map %s: not a field of template %s — skipping", code, spec.template)
            continue
        if match.row_label is None:
            log.info("llm-map %s: LLM returned no row — field stays absent", code)
            continue

        hit = index.get(_norm_label(match.row_label))
        if hit is None:
            log.warning(
                "llm-map %s: LLM row %r not present in grid — field stays absent",
                code,
                match.row_label,
            )
            continue
        label_cell, row_cells = hit

        unit = unit_for(field.kind, currency)
        found = _first_value(row_cells, label_cell, bank.number_locale, unit, code, scale)
        if found is None:
            log.warning(
                "llm-map %s: row %r matched but no cell normalised — field stays absent",
                code,
                match.row_label,
            )
            continue
        value_cell, value = found

        values.append(
            FieldValue(
                template=spec.template,
                field_code=code,
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
                    monetary_scale=bank.monetary_scale,
                ),
                mapping=MappingDecision(
                    method=MappingMethod.LLM,
                    model=model,
                    prompt_sha=prompt_sha,
                    prompt_version=config.PROMPT_VERSION,
                    matched_alias=match.row_label,
                    confidence=Decimal(str(match.confidence)),
                ),
                raw_text=value_cell.text,
                engine_values={engine: value},
            )
        )

    # No silent drops: a code the LLM never decided on must be visible as absent,
    # not just shorter output the caller can't distinguish from "no row fits".
    answered = {m.field_code for m in result.matches}
    for code in unmatched_codes:
        if code not in answered:
            log.warning("llm-map %s: LLM returned no decision for this field — stays absent", code)

    return values


# --- real mapper (not exercised in tests) ----------------------------------------


class AnthropicMapper:
    """Thin ``langchain_anthropic`` wrapper enforcing :class:`RowMatchSet` output.

    Lazily constructs the client so importing this module needs no API key. Never
    used by the test suite — the bounded mapper is always stub-injected there.
    """

    def __init__(self, model: str | None = None) -> None:
        from langchain_anthropic import ChatAnthropic

        self._llm = ChatAnthropic(
            model=model or config.MAP_MODEL_SIMPLE,
            max_tokens=config.MAP_MAX_TOKENS,
            temperature=0,
        ).with_structured_output(RowMatchSet)

    def map_rows(self, prompt: str) -> RowMatchSet:
        # langchain's structured-output stub is typed as a union; the runtime
        # guarantee here is RowMatchSet (enforced by with_structured_output).
        return self._llm.invoke(prompt)  # type: ignore[return-value]
