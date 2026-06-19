"""Resolve the LATEST reporting period from document CONTENT (chunk 3.3).

The case study leads with "automatically identify the *latest* reports". File dates and
URLs lie — re-uploads, mirror sites, a "2024" in a 2025 filing's URL — so the reporting
period is resolved from the document's own text and the evidence for the choice is recorded
(CLAUDE.md §A — auditability: the choice of "latest" must be *explained*, not assumed). This
module is pure logic over already-extracted text: no PDF parsing, no network, and crucially
no ``datetime.now`` (a document's period comes from its content, never from "today").

Design decisions (documented because they are not obvious and they are load-bearing):

* **Quarter-end date → its quarter.** ``31 December YYYY`` → Q4, ``30 Sep`` → Q3, ``30 Jun``
  → Q2, ``31 Mar`` → Q1. Explicit *annual* wording (``FY2025``, "full year 2025") → annual
  (``quarter=None``). A non-quarter-end full date (e.g. ``15 May``) is NOT a reporting
  signal — resolving a period from it would be guessing, which §A forbids.
* **A bare year alone yields NO candidate.** A year with no quarter/annual qualifier cannot
  identify a reporting *period*; emitting one would guess a quarter AND — since an annual
  ``sort_key`` outranks Q4 — let a scattered year dominate the ranking. Dropped deliberately.
* **Confidence reflects signal strength,** not arithmetic certainty: an explicit "as at
  <quarter-end>" beside a KM1 heading outranks the same date in stray prose. It never reaches
  1.0 (reserved for exact structured sources like P3DH); content inference is always < 1.
* **Ranking is ``sort_key`` DESC then confidence DESC** (the chunk's contract). The latest
  period wins; confidence is the *tiebreak within a period rank* and, separately, the audit
  reason and the input to :func:`is_ambiguous`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from ..models import ReportingPeriod

# --- value object ----------------------------------------------------------------


@dataclass(frozen=True)
class PeriodCandidate:
    """One resolved-period hypothesis with the evidence that produced it.

    ``char_span`` is the ``[start, end)`` index range of ``evidence`` in the source text
    (``None`` only when a candidate is constructed without a source span, e.g. in tests).
    """

    period: ReportingPeriod
    evidence: str
    confidence: Decimal
    char_span: tuple[int, int] | None


# --- signal strength -------------------------------------------------------------

# Base confidence by signal type. An explicit calendar quarter-end date is a clear reporting
# signal; a numeric/ISO date is marginally weaker (more likely an incidental date); an
# explicit period token ("Q4 2025", "FY2025") names the reporting period outright.
_BASE_LONG_DATE = Decimal("0.60")
_BASE_NUMERIC_DATE = Decimal("0.55")
_BASE_PERIOD_TOKEN = Decimal("0.70")

# Context bonuses (additive, capped). A nearby template/section heading (KM1, OV1, "key
# metrics") makes the date the table's own. "as at"/"as of" is canonical reporting phrasing
# (strong); a bare "at" is weaker — it also fires on incidental prose ("data at 31 Dec") —
# so it earns a smaller bonus rather than masquerading as an explicit reporting reference.
_BONUS_AS_AT = Decimal("0.20")
_BONUS_AT = Decimal("0.10")
_BONUS_HEADING = Decimal("0.15")
_MAX_CONFIDENCE = Decimal("0.99")  # content inference is never certain — never 1.0

# Plausible Basel reporting-year window. A year outside it is an OCR artefact / page number,
# not a reporting period — discarded so it can never silently dominate the ranking (§A).
_MIN_YEAR = 2000
_MAX_YEAR = 2099

# How far back to look for a section heading preceding a date match (characters). Keywords
# are matched whole-word (\b) so a two-char code like "km1" cannot match inside a cell ref.
_HEADING_WINDOW = 80
_HEADING_RE = re.compile(
    r"\b(?:key prudential metrics|prudential metrics|key metrics|km1|ov1)\b", re.IGNORECASE
)

# Default confidence delta below which the two best-supported periods are a "near tie".
_AMBIGUITY_DELTA = Decimal("0.05")

# --- regexes ---------------------------------------------------------------------

# A leading reporting-date phrase, captured so it (a) earns the as-at bonus and (b) is folded
# into the evidence span. Strong ("as at"/"as of") and weak ("at") arms are separate groups so
# they score differently. ``\b`` stops "at" matching inside a word like "format".
_PREFIX = r"(?P<as_at>\b(?:as\s+at|as\s+of)\s+)?(?P<at>\bat\s+)?"

# (day, month) -> quarter. Only Basel quarter-ends map; any other date is not a period.
_QUARTER_END: dict[tuple[int, int], int] = {(31, 3): 1, (30, 6): 2, (30, 9): 3, (31, 12): 4}

_MONTHS: dict[str, int] = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
# Longest spelling first so "september" matches before "sep" (alternation is ordered).
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

# ``(?!\d)`` after each 4-digit year stops a 5+-digit OCR run (e.g. "99990") matching as a
# spurious year; the plausible-year guard in find_period_candidates is the second line.
_LONG_DATE_RE = re.compile(
    rf"{_PREFIX}(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+"
    rf"(?P<month>{_MONTH_ALT})\b\.?\s+(?P<year>\d{{4}})(?!\d)",
    re.IGNORECASE,
)
# Day-first numeric date (DD/MM/YYYY with / . or -). Our quarter-ends are unambiguous as
# day-first: 31/12, 30/09, 30/06, 31/03 are invalid read month-first, so no DD/MM vs MM/DD trap.
_NUMERIC_DATE_RE = re.compile(rf"{_PREFIX}(?P<day>\d{{1,2}})[/.-](?P<month>\d{{1,2}})[/.-](?P<year>\d{{4}})(?!\d)")
_ISO_DATE_RE = re.compile(rf"{_PREFIX}(?P<year>\d{{4}})-(?P<month>\d{{2}})-(?P<day>\d{{2}})(?!\d)")
# "Q4 2025" / "Q4 FY2025" / "2025 Q4".
_QUARTER_TOKEN_RE = re.compile(
    rf"{_PREFIX}(?:Q(?P<q1>[1-4])\s*(?:FY)?\s*(?P<y1>\d{{4}})(?!\d)|(?P<y2>\d{{4}})\s*Q(?P<q2>[1-4]))",
    re.IGNORECASE,
)
_WORD_QUARTER_RE = re.compile(
    r"(?P<word>first|second|third|fourth)\s+quarter\s+(?:of\s+)?(?P<year>\d{4})(?!\d)", re.IGNORECASE
)
_WORD_QUARTER: dict[str, int] = {"first": 1, "second": 2, "third": 3, "fourth": 4}
# Annual: "FY2025", "full year 2025", "financial year 2025", "fiscal year 2025". Whitespace is
# tolerant (\s+) so a PDF extraction artefact like "full  year" is not silently dropped.
_FY_RE = re.compile(
    r"\bFY\s*(?P<y1>\d{4})\b|\b(?:full[\s-]+year|financial\s+year|fiscal\s+year)\s+(?P<y2>\d{4})\b",
    re.IGNORECASE,
)


# --- candidate construction ------------------------------------------------------


def _heading_start(text: str, date_start: int) -> int | None:
    """Index where a section heading begins within the window before a date, else ``None``.

    Returns the *earliest* keyword position in the window so the evidence span captures the
    whole heading (e.g. "Key metrics (KM1) as at 31 December 2025").
    """
    window_start = max(0, date_start - _HEADING_WINDOW)
    match = _HEADING_RE.search(text, window_start, date_start)
    return match.start() if match else None


def _make_candidate(text: str, match: re.Match[str], period: ReportingPeriod, base: Decimal) -> PeriodCandidate:
    """Score a single regex match into a :class:`PeriodCandidate` with evidence + span.

    ``match.start()`` already includes any captured prefix ("as at"/"at") because ``_PREFIX``
    is part of the pattern, so the evidence span folds the prefix in without extra work.
    """
    start, end = match.start(), match.end()
    confidence = base
    groups = match.groupdict()
    if groups.get("as_at"):
        confidence += _BONUS_AS_AT
    elif groups.get("at"):
        confidence += _BONUS_AT
    evidence_start = start
    heading = _heading_start(text, start)
    if heading is not None:
        confidence += _BONUS_HEADING
        evidence_start = min(evidence_start, heading)
    confidence = min(confidence, _MAX_CONFIDENCE)
    return PeriodCandidate(
        period=period,
        evidence=text[evidence_start:end],
        confidence=confidence,
        char_span=(evidence_start, end),
    )


def _scan_calendar_dates(text: str) -> list[PeriodCandidate]:
    """Long-form, numeric (DD/MM/YYYY) and ISO dates that fall on a Basel quarter-end."""
    out: list[PeriodCandidate] = []
    for regex, base in (
        (_LONG_DATE_RE, _BASE_LONG_DATE),
        (_NUMERIC_DATE_RE, _BASE_NUMERIC_DATE),
        (_ISO_DATE_RE, _BASE_NUMERIC_DATE),
    ):
        for m in regex.finditer(text):
            raw_month = m.group("month")
            month = _MONTHS[raw_month.lower()] if regex is _LONG_DATE_RE else int(raw_month)
            quarter = _QUARTER_END.get((int(m.group("day")), month))
            if quarter is None:  # not a quarter-end → not a reporting-period signal
                continue
            out.append(_make_candidate(text, m, ReportingPeriod(int(m.group("year")), quarter), base))
    return out


def _scan_period_tokens(text: str) -> list[PeriodCandidate]:
    """Explicit quarter ("Q4 2025", "fourth quarter 2025") and annual ("FY2025") tokens."""
    out: list[PeriodCandidate] = []
    for m in _QUARTER_TOKEN_RE.finditer(text):
        if m.group("q1"):
            period = ReportingPeriod(int(m.group("y1")), int(m.group("q1")))
        else:
            period = ReportingPeriod(int(m.group("y2")), int(m.group("q2")))
        out.append(_make_candidate(text, m, period, _BASE_PERIOD_TOKEN))
    for m in _WORD_QUARTER_RE.finditer(text):
        period = ReportingPeriod(int(m.group("year")), _WORD_QUARTER[m.group("word").lower()])
        out.append(_make_candidate(text, m, period, _BASE_PERIOD_TOKEN))
    for m in _FY_RE.finditer(text):
        year = m.group("y1") or m.group("y2")
        out.append(_make_candidate(text, m, ReportingPeriod(int(year), None), _BASE_PERIOD_TOKEN))
    return out


# --- public API ------------------------------------------------------------------


def find_period_candidates(text: str) -> list[PeriodCandidate]:
    """Scan ``text`` for reporting-date signals → de-duplicated :class:`PeriodCandidate`s.

    Every recognised quarter-end date / period token becomes a candidate carrying the matched
    evidence substring, its char span, and a confidence reflecting signal strength. Candidates
    are de-duplicated by period: when the same period is signalled more than once, the
    highest-confidence (best-evidenced) occurrence is kept. Candidates whose year falls outside
    the plausible Basel window are discarded (OCR artefacts must not silently win the ranking).
    Returns ``[]`` when nothing is recognised — the caller must then fail loud / route to human
    review, never guess.
    """
    best: dict[ReportingPeriod, PeriodCandidate] = {}
    for cand in _scan_calendar_dates(text) + _scan_period_tokens(text):
        if not _MIN_YEAR <= cand.period.year <= _MAX_YEAR:
            continue
        current = best.get(cand.period)
        if current is None or cand.confidence > current.confidence:
            best[cand.period] = cand
    return list(best.values())


def _rank(candidates: list[PeriodCandidate]) -> list[PeriodCandidate]:
    """Rank by recency then confidence: ``(period.sort_key DESC, confidence DESC)``."""
    return sorted(candidates, key=lambda c: (c.period.sort_key, c.confidence), reverse=True)


def resolve_latest(
    text: str, *, prefer: Literal["any", "quarter_end"] = "any"
) -> PeriodCandidate | None:
    """Return the single latest-period candidate, or ``None`` if none is recognised.

    Candidates are ranked ``(sort_key DESC, confidence DESC)`` and the top one returned: the
    most recent reporting period wins, with confidence breaking ties within a period rank.
    ``prefer="quarter_end"`` drops annual (``quarter=None``) candidates first — use it when a
    quarterly cadence is expected and annual signals should be ignored. ``None`` (no candidate)
    is a hard stop for the caller: a missing period must route to human review, never be guessed
    (CLAUDE.md §A).
    """
    if prefer not in ("any", "quarter_end"):
        raise ValueError(f"prefer must be 'any' or 'quarter_end', got {prefer!r}")
    candidates = find_period_candidates(text)
    if prefer == "quarter_end":
        candidates = [c for c in candidates if c.period.quarter is not None]
    if not candidates:
        return None
    return _rank(candidates)[0]


def is_ambiguous(
    candidates: list[PeriodCandidate], *, delta: Decimal = _AMBIGUITY_DELTA
) -> bool:
    """True when the two best-supported candidates are a genuine near-tie worth human review.

    Defined as: the top two ranked candidates share the **same year** and their confidences are
    within ``delta``. The same-year condition is deliberate — different-year candidates are the
    prior-period comparatives every disclosure repeats (e.g. "as at 31 Dec 2025" beside "as at
    31 Dec 2024") at equal confidence; recency cleanly disambiguates those, so they must not
    flag. A same-year contest (Q3 vs Q4, or Q4 vs annual) at near-equal confidence is the real
    ambiguity. (The chunk's "same sort_key" is unreachable here: candidates are de-duplicated by
    period, so two survivors can never share a sort_key — same year is the meaningful analogue.)
    """
    if len(candidates) < 2:
        return False
    top, second = _rank(candidates)[:2]
    if top.period.year != second.period.year:
        return False
    return abs(top.confidence - second.confidence) <= delta


def explain(candidate: PeriodCandidate) -> str:
    """One-line audit string recording WHY a period was selected (period + evidence + conf)."""
    span = f"chars[{candidate.char_span[0]}:{candidate.char_span[1]}]" if candidate.char_span else "chars[n/a]"
    return (
        f"period={candidate.period.label} confidence={candidate.confidence} "
        f"evidence={candidate.evidence!r} ({span})"
    )
