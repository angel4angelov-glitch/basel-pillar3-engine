"""Locale-aware number normalisation — the C2 silent-error firewall (CLAUDE.md §A).

A verbatim cell string becomes a :class:`~decimal.Decimal`, resolving thousands /
decimal separators by ``number_locale``. The one law of this module: it NEVER
returns a silently-wrong or default value. Anything ambiguous, blank, or
non-numeric RAISES :class:`NormalisationError` so the caller routes it to human
review rather than admitting a wrong digit into the dataset. ``Decimal`` only —
no float ever touches a figure here.

Locale conventions (documented choices for the under-specified ones):
  - en_GB / en_US / en_CA / ja_JP : ',' thousands, '.' decimal.
  - de_DE / de_CH / es_ES / it_IT / nl_NL : '.' thousands, ',' decimal.
  - fr_FR : space thousands (regular, NBSP U+00A0, thin U+2009, narrow U+202F),
    ',' decimal.
ja_JP follows the en convention (period decimal, comma thousands); es/it/nl follow
the continental convention (dot thousands, comma decimal).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from ..models import Unit


class NormalisationError(ValueError):
    """Raised when a cell string cannot be parsed to an unambiguous Decimal."""


# --- locale separator table -----------------------------------------------------

_FR_SPACES = (" ", " ", " ", " ")  # regular, NBSP, thin, narrow no-break

# locale -> (decimal_sep, frozenset(thousands_seps))
_LOCALES: dict[str, tuple[str, frozenset[str]]] = {
    "en_GB": (".", frozenset({","})),
    "en_US": (".", frozenset({","})),
    "en_CA": (".", frozenset({","})),
    "ja_JP": (".", frozenset({","})),
    "de_DE": (",", frozenset({"."})),
    "de_CH": (",", frozenset({"."})),
    "es_ES": (",", frozenset({"."})),
    "it_IT": (",", frozenset({"."})),
    "nl_NL": (",", frozenset({"."})),
    "fr_FR": (",", frozenset(_FR_SPACES)),
}

# --- cleaning constants ----------------------------------------------------------

_PLACEHOLDERS = {"n/a", "na"}
_DASHES = {"-", "–", "—"}  # hyphen-minus, en dash, em dash
_CURRENCY_SYMBOLS = "€£$¥"
_CURRENCY_CODE_RE = re.compile(r"^(?:EUR|GBP|USD|CHF|JPY|CAD)\s*", re.IGNORECASE)
# footnote markers: superscript digits (U+00B9/B2/B3 + U+2070–U+2079) and trailing daggers
_FOOTNOTE_CHARS = "".join(
    ["¹", "²", "³", *[chr(c) for c in range(0x2070, 0x207A)], "*", "†", "‡"]
)

# --- scale detection -------------------------------------------------------------

# a scale token at the very end of the string, optional leading space
_SCALE_RE = re.compile(r"\s*(thousand|million|billion|bn|mn|k|m)\s*$", re.IGNORECASE)
_SCALE_MULT: dict[str, Decimal] = {
    "k": Decimal(10) ** 3,
    "thousand": Decimal(10) ** 3,
    "m": Decimal(10) ** 6,
    "mn": Decimal(10) ** 6,
    "million": Decimal(10) ** 6,
    "bn": Decimal(10) ** 9,
    "billion": Decimal(10) ** 9,
}

_MONETARY = {Unit.EUR_M, Unit.GBP_M, Unit.USD_M, Unit.CHF_M, Unit.JPY_M}
_MILLION = Decimal(10) ** 6


def detect_scale(raw: str) -> Decimal:
    """Multiplier implied by a trailing scale suffix/word; ``Decimal(1)`` if none."""
    match = _SCALE_RE.search(raw.strip())
    if match is None:
        return Decimal(1)
    return _SCALE_MULT[match.group(1).lower()]


# --- core parse ------------------------------------------------------------------

_GROUP_HEAD = re.compile(r"\d{1,3}")
_GROUP_TAIL = re.compile(r"\d{3}")


def parse_decimal(raw: str, locale: str) -> Decimal:
    """Parse a verbatim cell string to a Decimal, resolving separators by locale.

    Raises :class:`NormalisationError` on empty/placeholder/ambiguous/garbage input.
    Never strips ordinary trailing digits — only recognised footnote markers.
    """
    if locale not in _LOCALES:
        raise NormalisationError(f"Unknown locale: {locale!r}")
    decimal_sep, thousands_seps = _LOCALES[locale]

    s = raw.strip()
    if not s:
        raise NormalisationError("empty / whitespace-only input")
    if s in _DASHES or s.lower() in _PLACEHOLDERS:
        raise NormalisationError(f"placeholder, not a number: {raw!r}")

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()

    # strip a leading currency symbol or ISO code
    if s and s[0] in _CURRENCY_SYMBOLS:
        s = s[1:].lstrip()
    s = _CURRENCY_CODE_RE.sub("", s).strip()

    # strip recognised footnote markers, THEN a trailing percent (footnotes may
    # sit after the '%', e.g. "13.6%¹" — strip them first to expose the '%')
    s = s.rstrip(_FOOTNOTE_CHARS).rstrip()
    if s.endswith("%"):
        s = s[:-1].rstrip()

    # leading sign
    if s.startswith("-"):
        negative = not negative
        s = s[1:].lstrip()
    elif s.startswith("+"):
        s = s[1:].lstrip()

    if not s:
        raise NormalisationError(f"no digits after cleaning: {raw!r}")

    value = _resolve_separators(s, decimal_sep, thousands_seps, raw)
    return -value if negative else value


def _resolve_separators(
    s: str, decimal_sep: str, thousands_seps: frozenset[str], raw: str
) -> Decimal:
    """Split into integer/decimal parts by locale, validate grouping, build Decimal."""
    if s.count(decimal_sep) > 1:
        raise NormalisationError(f"multiple decimal separators: {raw!r}")

    if decimal_sep in s:
        int_part, dec_part = s.split(decimal_sep)
    else:
        int_part, dec_part = s, ""

    # canonicalise every thousands variant to a single comma for grouping checks
    had_sep = any(t in int_part for t in thousands_seps)
    canonical = int_part
    for t in thousands_seps:
        canonical = canonical.replace(t, ",")

    if had_sep:
        groups = canonical.split(",")
        if not _GROUP_HEAD.fullmatch(groups[0]) or any(
            not _GROUP_TAIL.fullmatch(g) for g in groups[1:]
        ):
            raise NormalisationError(f"malformed thousands grouping: {raw!r}")
        int_digits = "".join(groups)
    else:
        int_digits = canonical

    if int_digits and not int_digits.isdigit():
        raise NormalisationError(f"residual non-numeric character: {raw!r}")
    if dec_part and not dec_part.isdigit():
        raise NormalisationError(f"residual non-numeric character: {raw!r}")
    if not int_digits and not dec_part:
        raise NormalisationError(f"no digits: {raw!r}")

    number = (int_digits or "0") + (f".{dec_part}" if dec_part else "")
    try:
        return Decimal(number)
    except InvalidOperation as exc:  # defensive — grouping checks should prevent this
        raise NormalisationError(f"cannot parse as Decimal: {raw!r}") from exc


# --- firewall entry point --------------------------------------------------------


def normalise(
    raw: str,
    locale: str,
    unit: Unit,
    *,
    pct_low: Decimal = Decimal(-100),
    pct_high: Decimal = Decimal(1000),
) -> Decimal:
    """Route a verbatim cell string to a Decimal by ``unit``. Raises on bad input.

    ``raw`` is never mutated; the caller retains it as ``raw_text`` for audit.
    Monetary convention: a cell with no scale word is assumed already in the
    template's stated unit (Pillar 3 headers declare e.g. "€m"); a scale word
    (k/m/bn) converts the absolute amount into that millions unit.
    """
    if unit is Unit.PERCENT:
        pct = parse_decimal(raw, locale)
        _check_band(pct, pct_low, pct_high, raw)
        return pct

    if unit is Unit.RATIO:
        has_pct = "%" in raw
        num = parse_decimal(raw, locale)
        fraction = num / 100 if has_pct else num
        _check_band(fraction * 100, pct_low, pct_high, raw)
        return fraction

    # A '%' here means a percent cell landed on a non-percent field (column
    # misalignment). parse_decimal would silently eat the '%' and admit a wrong
    # number — the exact C2 silent-error. Refuse it (CLAUDE.md §A C2).
    if "%" in raw:
        raise NormalisationError(f"unexpected '%' for non-percent unit {unit}: {raw!r}")

    if unit in _MONETARY:
        scale = detect_scale(raw)
        numeric = _SCALE_RE.sub("", raw) if scale != 1 else raw
        num = parse_decimal(numeric, locale)
        return num if scale == 1 else num * scale / _MILLION

    # COUNT / NONE
    return parse_decimal(raw, locale)


def _check_band(pct: Decimal, low: Decimal, high: Decimal, raw: str) -> None:
    if not (low <= pct <= high):
        raise NormalisationError(
            f"percent {pct} outside sane band [{low}, {high}] — ratio/% confusion? {raw!r}"
        )
