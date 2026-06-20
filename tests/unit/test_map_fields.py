"""Tests for isda_p3.mapping.map_fields — rule-first RawCell grid -> FieldValue (chunk 1.3).

Synthetic ``RawCell`` grids only — no Docling, no LLM, no network. These pin the
deterministic mapping contract: exact normalised-label match (no substrings),
first-numeric-cell = current period, locale/currency-resolved units, footnote
carry-through, and the no-silent-failure rule that a missing/unparseable value is
NEVER emitted as 0 — it surfaces in the returned unmatched list (CLAUDE.md §A C2).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from isda_p3.config_load import load_template
from isda_p3.mapping.map_fields import map_fields
from isda_p3.models import (
    Bank,
    BBox,
    Engine,
    FieldKind,
    FieldValue,
    Jurisdiction,
    MappingMethod,
    RawCell,
    ReportingPeriod,
    SourceKind,
    Template,
    Unit,
    unit_for,
)

# --- builders ------------------------------------------------------------------

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
_USD_BANK = Bank(
    id="hsbc",
    name="HSBC",
    jurisdiction=Jurisdiction.UK,
    ir_url="https://hsbc.com",
    p3dh_lei=None,
    number_locale="en_GB",
    reporting_currency="USD",
)


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


def _grid(rows: list[tuple[str, ...]]) -> list:
    """Build a left-to-right grid: rows[i] = (label, current, prior, ...)."""
    cells = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cells.append(_cell(r, c, text))
    return cells


# a clean, complete KM1 grid (most-recent period in col 1)
_CLEAN_KM1 = [
    ("Common Equity Tier 1 (CET1) capital", "47,200", "45,000"),
    ("Tier 1 capital", "52,000", "50,000"),
    ("Total capital", "60,000", "58,000"),
    ("Total risk-weighted assets (RWA)", "320,000", "310,000"),
    ("Common Equity Tier 1 ratio (%)", "14.8", "14.5"),
    ("Tier 1 ratio (%)", "16.3", "16.0"),
    ("Total capital ratio (%)", "18.8", "18.5"),
]


def _run(cells, bank=_GBP_BANK):
    return map_fields(
        cells, _SPEC, bank, _PERIOD, _URL, SourceKind.PDF, Engine.DOCLING
    )


def _by_code(values: list[FieldValue]) -> dict[str, FieldValue]:
    return {fv.field_code: fv for fv in values}


# --- the happy path ------------------------------------------------------------


def test_clean_km1_maps_all_seven_fields():
    values, unmatched = _run(_grid(_CLEAN_KM1))
    assert len(values) == 7
    assert {fv.field_code for fv in values} == {f"KM1.{i}" for i in range(1, 8)}
    # This fixture is the 7 headline rows only; the leverage/LCR/NSFR codes the
    # template also declares (KM1.13-20) are legitimately unmatched in this grid —
    # absent, never fabricated (CLAUDE.md §A.2).
    expected_unmatched = {f.code for f in _SPEC.fields} - {f"KM1.{i}" for i in range(1, 8)}
    assert set(unmatched) == expected_unmatched


def test_units_resolved_by_kind_and_currency():
    values, _ = _run(_grid(_CLEAN_KM1))
    by = _by_code(values)
    assert by["KM1.1"].unit is Unit.GBP_M  # MONETARY + GBP
    assert by["KM1.5"].unit is Unit.PERCENT


def test_values_and_raw_text_preserved():
    values, _ = _run(_grid(_CLEAN_KM1))
    by = _by_code(values)
    assert by["KM1.1"].value == Decimal("47200")
    assert by["KM1.1"].raw_text == "47,200"  # verbatim, not the parsed digits
    assert by["KM1.5"].value == Decimal("14.8")


# --- monetary_scale threading (the HSBC $bn fix, at the mapping layer) ----------

# bare $bn cells, no inline scale word — exactly HSBC's KM1 layout
_BARE_BN_KM1 = [
    ("Common Equity Tier 1 (CET1) capital", "124.0", "132.6"),
    ("Total risk-weighted assets (RWA)", "883.8", "888.6"),
    ("Common Equity Tier 1 ratio (%)", "14.0", "14.9"),
]
_USD_BN_BANK = Bank(
    id="hsbc",
    name="HSBC",
    jurisdiction=Jurisdiction.UK,
    ir_url="https://hsbc.com",
    p3dh_lei=None,
    number_locale="en_GB",
    reporting_currency="USD",
    monetary_scale="billions",
)


def test_billions_bank_lifts_bare_monetary_cells_x1000():
    by = _by_code(_run(_grid(_BARE_BN_KM1), bank=_USD_BN_BANK)[0])
    # bare "124.0" $bn -> 124000 $m; the scale is applied at the mapping layer
    assert by["KM1.1"].value == Decimal("124000")
    assert by["KM1.1"].unit is Unit.USD_M
    assert by["KM1.4"].value == Decimal("883800")
    # percent rows are untouched by monetary_scale
    assert by["KM1.5"].value == Decimal("14.0")
    assert by["KM1.5"].unit is Unit.PERCENT
    # the applied scale is recorded in provenance for audit
    assert by["KM1.1"].provenance.monetary_scale == "billions"


def test_default_millions_bank_leaves_bare_cells_unscaled():
    # Same grid, default-scale USD bank: a bare "124.0" is read as 124 $m (NOT lifted).
    # Proves the lift is the config dimension, not an unconditional code path.
    by = _by_code(_run(_grid(_BARE_BN_KM1), bank=_USD_BANK)[0])
    assert by["KM1.1"].value == Decimal("124.0")
    assert by["KM1.1"].provenance.monetary_scale == "millions"


def test_mapping_decision_is_rule():
    values, _ = _run(_grid(_CLEAN_KM1))
    fv = _by_code(values)["KM1.1"]
    assert fv.mapping.method is MappingMethod.RULE
    assert fv.mapping.matched_alias == "Common Equity Tier 1 (CET1) capital"
    assert fv.mapping.model is None
    assert fv.mapping.confidence == Decimal("1")


def test_provenance_and_engine_values_from_value_cell():
    values, _ = _run(_grid(_CLEAN_KM1))
    fv = _by_code(values)["KM1.1"]
    # value cell is col 1 (the current-period column), not the label cell (col 0)
    assert fv.provenance.bbox == BBox(page=4, x0=1.0, y0=0.0, x1=2.0, y1=1.0)
    assert fv.provenance.bank_id == "barclays"
    assert fv.provenance.engine is Engine.DOCLING
    assert fv.engine_values == {Engine.DOCLING: Decimal("47200")}


# --- enumerator handling -------------------------------------------------------


def test_enumerator_prefixed_label_still_matches():
    cells = _grid([("1 Common Equity Tier 1 (CET1) capital", "47,200")])
    values, _ = _run(cells)
    by = _by_code(values)
    assert "KM1.1" in by
    assert by["KM1.1"].value == Decimal("47200")


def test_uk_enumerator_token_stripped():
    cells = _grid([("UK 7a Total capital ratio (%)", "18.8")])
    values, _ = _run(cells)
    assert "KM1.7" in _by_code(values)


def test_trailing_footnote_marker_stripped_from_label():
    # Pillar 3 footnotes render as a trailing " 1"/" 3" (Docling on the real Barclays
    # KM1). The row must still match its alias — the label-side analogue of stripping
    # footnotes from values; this is the fix that lifts real KM1.3/KM1.7/KM1.17.
    values, _ = _run(_grid([
        ("Total capital 1", "60,000"),
        ("Total capital ratio (%) 3", "18.8"),
    ]))
    by = _by_code(values)
    assert by["KM1.3"].value == Decimal("60000")
    assert by["KM1.7"].value == Decimal("18.8")


def test_internal_digit_is_not_treated_as_a_footnote():
    # "Tier 1 capital" ends in a word, not a bare number — its internal "1" must survive.
    values, _ = _run(_grid([("Tier 1 capital", "52,000")]))
    assert _by_code(values)["KM1.2"].value == Decimal("52000")


# --- false-positive guard ------------------------------------------------------


def test_tier1_ratio_does_not_map_to_tier1_capital():
    # "Tier 1 ratio (%)" must NOT match KM1.2 ("Tier 1 capital") via substring.
    cells = _grid([("Tier 1 ratio (%)", "16.3")])
    values, unmatched = _run(cells)
    codes = {fv.field_code for fv in values}
    assert "KM1.2" not in codes  # the false positive we forbid
    assert "KM1.6" in codes  # it legitimately matches the ratio field
    assert "KM1.2" in unmatched


# --- first-numeric-cell = value, dash never becomes 0 --------------------------


def test_first_numeric_cell_skips_dash():
    # label, empty/dash cell, then the real value -> picks 13.6, dash is NOT 0.
    cells = _grid([("Common Equity Tier 1 ratio (%)", "-", "13.6")])
    values, _ = _run(cells)
    fv = _by_code(values)["KM1.5"]
    assert fv.value == Decimal("13.6")
    assert fv.raw_text == "13.6"  # the dash cell was skipped, not parsed to 0


def test_dash_value_is_not_emitted_as_zero():
    cells = _grid([("Common Equity Tier 1 ratio (%)", "-")])
    values, unmatched = _run(cells)
    assert all(fv.value != Decimal("0") for fv in values)
    assert "KM1.5" in unmatched  # matched the label but no parseable value


# --- missing rows go to unmatched, not 0 ---------------------------------------


def test_missing_field_appears_in_unmatched():
    cells = _grid([("Common Equity Tier 1 (CET1) capital", "47,200")])
    values, unmatched = _run(cells)
    assert {fv.field_code for fv in values} == {"KM1.1"}
    for code in ("KM1.2", "KM1.3", "KM1.4", "KM1.5", "KM1.6", "KM1.7"):
        assert code in unmatched
    assert not any(fv.value == Decimal("0") for fv in values)


# --- currency resolution -------------------------------------------------------


def test_usd_bank_resolves_monetary_to_usd_m():
    cells = _grid([("Common Equity Tier 1 (CET1) capital", "47,200")])
    values, _ = _run(cells, bank=_USD_BANK)
    assert _by_code(values)["KM1.1"].unit is Unit.USD_M


# --- footnote carry-through ----------------------------------------------------


def test_footnote_superscript_is_normalised_but_raw_text_kept():
    cells = _grid([("Common Equity Tier 1 ratio (%)", "13.6¹")])
    values, _ = _run(cells)
    fv = _by_code(values)["KM1.5"]
    assert fv.value == Decimal("13.6")
    assert fv.raw_text == "13.6¹"


# --- unit_for: kind + currency resolution (Part A) -----------------------------


@pytest.mark.parametrize(
    "currency,expected",
    [("GBP", Unit.GBP_M), ("USD", Unit.USD_M), ("EUR", Unit.EUR_M), ("CHF", Unit.CHF_M)],
)
def test_unit_for_monetary_depends_on_currency(currency, expected):
    assert unit_for(FieldKind.MONETARY, currency) is expected


@pytest.mark.parametrize(
    "kind,expected",
    [(FieldKind.PERCENT, Unit.PERCENT), (FieldKind.RATIO, Unit.RATIO), (FieldKind.COUNT, Unit.COUNT)],
)
def test_unit_for_non_monetary_ignores_currency(kind, expected):
    assert unit_for(kind, "GBP") is expected
    assert unit_for(kind, "EUR") is expected


def test_unit_for_unknown_currency_raises():
    with pytest.raises(ValueError, match="CNY"):
        unit_for(FieldKind.MONETARY, "CNY")


def test_unit_for_string_kind_does_not_silently_become_monetary():
    # A plain-string kind must still resolve correctly (== not is), never fall
    # through to a currency unit (CLAUDE.md §A — no silent wrong unit).
    assert unit_for("PERCENT", "GBP") is Unit.PERCENT
