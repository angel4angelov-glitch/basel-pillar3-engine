"""Tests for isda_p3.mapping.llm — the bounded LLM fallback mapper (chunk 1.4).

Stub-based, no network, no API key. The LLM only chooses which ROW corresponds to
an unmatched field code; the numeric value is STILL extracted deterministically via
normalise() from that row (CLAUDE.md §A — the LLM is judgment glue, never the
ledger). Every LLM decision carries a full, reproducible audit record
(model/prompt_sha/prompt_version/confidence). These tests pin that contract.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from isda_p3.config import MAP_MODEL_SIMPLE, PROMPT_VERSION
from isda_p3.config_load import load_template
from isda_p3.mapping.llm import RowMatch, RowMatchSet, map_unmatched
from isda_p3.models import (
    Bank,
    BBox,
    Engine,
    FieldValue,
    Jurisdiction,
    MappingMethod,
    RawCell,
    ReportingPeriod,
    SourceKind,
    Template,
)

_SPEC = load_template(Template.KM1)
_PERIOD = ReportingPeriod(2025, 4)
_URL = "https://home.barclays/pillar3.pdf"
_GBP_BANK = Bank(
    id="barclays",
    name="Barclays",
    jurisdiction=Jurisdiction.UK,
    ir_url="https://home.barclays",
    p3dh_lei=None,
    number_locale="en_GB",
    reporting_currency="GBP",
)


class _StubMapper:
    """Injected StructuredMapper: returns a canned RowMatchSet, records prompts."""

    def __init__(self, result: RowMatchSet) -> None:
        self._result = result
        self.prompts: list[str] = []

    def map_rows(self, prompt: str) -> RowMatchSet:
        self.prompts.append(prompt)
        return self._result


def _cell(row: int, col: int, text: str) -> RawCell:
    return RawCell(
        row_idx=row,
        col_idx=col,
        text=text,
        bbox=BBox(page=4, x0=float(col), y0=float(row), x1=float(col) + 1, y1=float(row) + 1),
        engine=Engine.DOCLING,
        row_label=None,
        col_label=None,
    )


def _grid(rows: list[tuple[str, ...]]) -> list[RawCell]:
    return [_cell(r, c, text) for r, row in enumerate(rows) for c, text in enumerate(row)]


def _run(cells, result: RowMatchSet, unmatched: list[str]):
    mapper = _StubMapper(result)
    values = map_unmatched(
        unmatched_codes=unmatched,
        cells=cells,
        spec=_SPEC,
        bank=_GBP_BANK,
        period=_PERIOD,
        source_url=_URL,
        source_kind=SourceKind.PDF,
        engine=Engine.DOCLING,
        mapper=mapper,
    )
    return values, mapper


# KM1.5 is the CET1 ratio (PERCENT). This bespoke label is NOT a km1.yaml alias,
# so the rule mapper would leave KM1.5 unmatched — exactly the LLM's job.
_BESPOKE_LABEL = "CET1 capital ratio as reported"


# --- happy path: a valid LLM match becomes a fully-audited FieldValue ----------


def test_valid_match_emits_audited_fieldvalue():
    cells = _grid([(_BESPOKE_LABEL, "13.6")])
    result = RowMatchSet(matches=[RowMatch(field_code="KM1.5", row_label=_BESPOKE_LABEL, confidence=0.91)])
    values, _ = _run(cells, result, ["KM1.5"])

    assert len(values) == 1
    fv = values[0]
    assert isinstance(fv, FieldValue)
    assert fv.field_code == "KM1.5"
    assert fv.value == Decimal("13.6")
    assert fv.mapping.method is MappingMethod.LLM
    assert fv.mapping.model == MAP_MODEL_SIMPLE
    assert fv.mapping.prompt_version == PROMPT_VERSION
    assert fv.mapping.matched_alias == _BESPOKE_LABEL
    assert fv.mapping.confidence == Decimal("0.91")
    # prompt_sha is a 64-char hex sha256
    assert len(fv.mapping.prompt_sha) == 64
    assert all(c in "0123456789abcdef" for c in fv.mapping.prompt_sha)


# --- THE LAW: the digit comes from normalise(), NOT from the LLM ---------------


def test_value_comes_from_normalise_not_the_llm():
    # The matched row's value cell carries a fused footnote superscript. The
    # emitted value must equal normalise("13.6¹") == 13.6 — proving the number was
    # extracted deterministically from the cell, never echoed from LLM output.
    cells = _grid([(_BESPOKE_LABEL, "13.6¹")])
    result = RowMatchSet(matches=[RowMatch(field_code="KM1.5", row_label=_BESPOKE_LABEL, confidence=0.8)])
    values, _ = _run(cells, result, ["KM1.5"])

    assert len(values) == 1
    fv = values[0]
    assert fv.value == Decimal("13.6")
    assert fv.raw_text == "13.6¹"  # verbatim source string retained for audit


# --- null / non-existent / unparseable => field stays ABSENT, never fabricated --


def test_null_row_label_stays_absent():
    cells = _grid([(_BESPOKE_LABEL, "13.6")])
    result = RowMatchSet(matches=[RowMatch(field_code="KM1.5", row_label=None, confidence=0.0)])
    values, _ = _run(cells, result, ["KM1.5"])
    assert values == []


def test_nonexistent_row_stays_absent_no_crash():
    cells = _grid([(_BESPOKE_LABEL, "13.6")])
    result = RowMatchSet(
        matches=[RowMatch(field_code="KM1.5", row_label="a row that is not in the table", confidence=0.99)]
    )
    values, _ = _run(cells, result, ["KM1.5"])
    assert values == []


def test_unparseable_value_cell_stays_absent_not_zero():
    # The matched row exists but its value cell is a dash → won't normalise.
    cells = _grid([(_BESPOKE_LABEL, "-")])
    result = RowMatchSet(matches=[RowMatch(field_code="KM1.5", row_label=_BESPOKE_LABEL, confidence=0.95)])
    values, _ = _run(cells, result, ["KM1.5"])
    assert values == []  # absent, NOT a fabricated 0


# --- determinism: identical inputs → identical prompt_sha (pass^k mapping step) --


def test_prompt_sha_is_deterministic_across_calls():
    cells = _grid([(_BESPOKE_LABEL, "13.6")])
    result = RowMatchSet(matches=[RowMatch(field_code="KM1.5", row_label=_BESPOKE_LABEL, confidence=0.91)])

    shas = set()
    prompts = set()
    for _ in range(5):
        values, mapper = _run(cells, result, ["KM1.5"])
        shas.add(values[0].mapping.prompt_sha)
        prompts.add(mapper.prompts[0])

    assert len(prompts) == 1  # same prompt text every time
    assert len(shas) == 1  # → same sha every time
    # and the sha actually is sha256 of that prompt
    (prompt,) = prompts
    assert next(iter(shas)) == hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    # ...and it is independent of the order cells arrive in (the index sorts by row).
    values_rev, _ = _run(list(reversed(cells)), result, ["KM1.5"])
    assert values_rev[0].mapping.prompt_sha == next(iter(shas))


def test_nonfinite_confidence_rejected_at_boundary():
    # A NaN/out-of-range confidence must fail loud at the LLM boundary, never reach
    # the audit record (where it would detonate later in reconciliation).
    import pytest
    from pydantic import ValidationError

    for bad in (float("nan"), float("inf"), 1.5, -0.1):
        with pytest.raises(ValidationError):
            RowMatch(field_code="KM1.5", row_label=_BESPOKE_LABEL, confidence=bad)


def test_empty_unmatched_makes_no_llm_call():
    cells = _grid([(_BESPOKE_LABEL, "13.6")])
    result = RowMatchSet(matches=[])
    values, mapper = _run(cells, result, [])
    assert values == []
    assert mapper.prompts == []  # short-circuited, no LLM invoked
