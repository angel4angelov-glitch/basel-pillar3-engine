"""Adversarial contract tests for the C2 locale-aware normalisation firewall.

These fixtures ARE the spec (CLAUDE.md §A C2 / plan chunk 1.2). The firewall rule:
NEVER return a silently-wrong or default value — ambiguous/blank input RAISES.
All numeric results are :class:`Decimal`, compared by exact value.
"""

from decimal import Decimal

import pytest

from isda_p3.mapping.normalise import (
    NormalisationError,
    detect_scale,
    normalise,
    parse_decimal,
)
from isda_p3.models import Unit

# --- parse_decimal: happy paths -------------------------------------------------

PARSE_OK = [
    # (raw, locale, expected)
    # --- English (',' thousands, '.' decimal) ---
    ("368,000", "en_GB", Decimal("368000")),
    ("1,234,567.89", "en_US", Decimal("1234567.89")),
    ("13.6", "en_GB", Decimal("13.6")),
    ("(1,234)", "en_US", Decimal("-1234")),
    ("£1,234", "en_GB", Decimal("1234")),
    ("13.6¹", "en_GB", Decimal("13.6")),  # superscript 1 footnote
    ("13.6²", "en_GB", Decimal("13.6")),  # superscript 2 footnote
    ("13.6*", "en_GB", Decimal("13.6")),  # asterisk footnote
    ("13.6%¹", "en_GB", Decimal("13.6")),  # percent then trailing footnote
    (" 42 ", "en_GB", Decimal("42")),
    ("-5", "en_GB", Decimal("-5")),
    # THE TRAP — real trailing digits must survive, never truncated to 13.6
    ("13.61", "en_GB", Decimal("13.61")),
    ("$1,234.56", "en_US", Decimal("1234.56")),
    ("EUR 1.234.567,89", "de_DE", Decimal("1234567.89")),
    ("+5", "en_GB", Decimal("5")),
    # --- German ('.' thousands, ',' decimal) ---
    ("368.000", "de_DE", Decimal("368000")),
    ("1.234.567,89", "de_DE", Decimal("1234567.89")),
    ("13,6", "de_DE", Decimal("13.6")),
    ("(1.234)", "de_DE", Decimal("-1234")),
    ("13,6 %", "de_DE", Decimal("13.6")),
    ("1.234.567,89", "de_CH", Decimal("1234567.89")),  # de_CH: same as de_DE per spec
    # --- French (space thousands incl. NBSP / narrow, ',' decimal) ---
    ("368 000", "fr_FR", Decimal("368000")),
    ("368 000", "fr_FR", Decimal("368000")),  # NBSP
    ("368 000", "fr_FR", Decimal("368000")),  # thin space
    ("368 000", "fr_FR", Decimal("368000")),  # narrow no-break space
    ("13,6", "fr_FR", Decimal("13.6")),
    # --- locale resolves the "1.234" ambiguity ---
    ("1.234", "en_US", Decimal("1.234")),  # en: '.' is decimal
    ("1.234", "de_DE", Decimal("1234")),  # de: '.' is thousands
    # --- ja_JP (',' thousands, '.' decimal — like en) ---
    ("1,234.5", "ja_JP", Decimal("1234.5")),
    # --- es/it/nl ('.' thousands, ',' decimal — like de) ---
    ("1.234,5", "es_ES", Decimal("1234.5")),
    ("1.234,5", "it_IT", Decimal("1234.5")),
    ("1.234,5", "nl_NL", Decimal("1234.5")),
]


@pytest.mark.parametrize("raw, locale, expected", PARSE_OK)
def test_parse_decimal_ok(raw, locale, expected):
    result = parse_decimal(raw, locale)
    assert isinstance(result, Decimal)
    assert result == expected


def test_parse_decimal_does_not_mutate_input():
    raw = " (1,234) "
    _ = parse_decimal(raw, "en_GB")
    assert raw == " (1,234) "


# --- parse_decimal: MUST RAISE --------------------------------------------------

PARSE_RAISE = [
    ("", "en_GB"),
    ("  ", "en_GB"),
    ("-", "en_GB"),
    ("–", "en_GB"),  # en dash placeholder
    ("—", "en_GB"),  # em dash placeholder
    ("n/a", "en_GB"),
    ("N/A", "en_GB"),
    ("na", "en_GB"),
    ("1.234.56", "en_US"),  # two decimal dots (en)
    ("12,34,56", "en_US"),  # malformed thousands grouping
    ("abc", "en_GB"),
    ("12.3.4", "de_DE"),  # de: ',' decimal absent, '.' grouping invalid → residual
    ("1,2,3", "de_DE"),  # de: ',' is decimal, appears thrice
    ("£", "en_GB"),  # currency symbol with no digits
    ("()", "en_GB"),  # empty parentheses
    ("%", "en_GB"),  # bare percent, no digits
]


@pytest.mark.parametrize("raw, locale", PARSE_RAISE)
def test_parse_decimal_raises(raw, locale):
    with pytest.raises(NormalisationError):
        parse_decimal(raw, locale)


def test_parse_decimal_unknown_locale_raises():
    with pytest.raises(NormalisationError):
        parse_decimal("1,234", "zz_ZZ")


# --- detect_scale ---------------------------------------------------------------

SCALE = [
    ("1.2bn", Decimal(10) ** 9),
    ("3.4 billion", Decimal(10) ** 9),
    ("500m", Decimal(10) ** 6),
    ("500 mn", Decimal(10) ** 6),
    ("7 million", Decimal(10) ** 6),
    ("8k", Decimal(10) ** 3),
    ("9 thousand", Decimal(10) ** 3),
    ("368,000", Decimal(1)),  # no scale word
    ("13.6%", Decimal(1)),
]


@pytest.mark.parametrize("raw, expected", SCALE)
def test_detect_scale(raw, expected):
    result = detect_scale(raw)
    assert isinstance(result, Decimal)
    assert result == expected


def test_detect_scale_no_false_trigger_midword():
    # "summit" contains no scale token at the boundary; trailing 't' is not a unit
    assert detect_scale("summit") == Decimal(1)


# --- normalise: the firewall entry point ----------------------------------------

NORMALISE_OK = [
    # PERCENT — returns the percentage value
    ("13.6%", "en_US", Unit.PERCENT, Decimal("13.6")),
    ("13,6 %", "de_DE", Unit.PERCENT, Decimal("13.6")),
    ("13.6", "en_US", Unit.PERCENT, Decimal("13.6")),
    # RATIO — returns the fraction
    ("0.136", "en_US", Unit.RATIO, Decimal("0.136")),
    ("13.6%", "en_US", Unit.RATIO, Decimal("0.136")),
    # monetary — parse * scale, expressed in millions (unit's scale)
    ("1.2bn", "en_US", Unit.EUR_M, Decimal("1200")),
    ("500m", "en_US", Unit.EUR_M, Decimal("500")),
    ("368,000", "en_US", Unit.EUR_M, Decimal("368000")),  # no scale word ⇒ already in €m
    ("1.234.567,89", "de_DE", Unit.GBP_M, Decimal("1234567.89")),
    # COUNT / NONE — parse as-is
    ("1,234", "en_US", Unit.COUNT, Decimal("1234")),
    ("42", "en_US", Unit.NONE, Decimal("42")),
]


@pytest.mark.parametrize("raw, locale, unit, expected", NORMALISE_OK)
def test_normalise_ok(raw, locale, unit, expected):
    result = normalise(raw, locale, unit)
    assert isinstance(result, Decimal)
    assert result == expected


@pytest.mark.parametrize(
    "raw, unit",
    [
        ("13.6%", Unit.EUR_M),  # percent cell misaligned onto a monetary field
        ("45.2%", Unit.GBP_M),
        ("0.5%", Unit.COUNT),
        ("99.9%", Unit.NONE),
    ],
)
def test_normalise_percent_on_non_percent_unit_raises(raw, unit):
    # The '%' must NOT be silently stripped and admitted as a plain number (C2).
    with pytest.raises(NormalisationError):
        normalise(raw, "en_US", unit)


def test_normalise_percent_with_footnote_ok():
    assert normalise("13,6 %¹", "de_DE", Unit.PERCENT) == Decimal("13.6")


def test_normalise_percent_out_of_band_raises():
    # 5000% is a ratio-vs-% confusion → outside the sane percent band → RAISE
    with pytest.raises(NormalisationError):
        normalise("5000", "en_US", Unit.PERCENT)


def test_normalise_percent_negative_within_band_ok():
    assert normalise("-50", "en_US", Unit.PERCENT) == Decimal("-50")


def test_normalise_blank_raises():
    with pytest.raises(NormalisationError):
        normalise("", "en_US", Unit.EUR_M)
