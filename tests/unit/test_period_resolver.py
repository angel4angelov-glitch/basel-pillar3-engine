"""Tests for isda_p3.discovery.period_resolver (chunk 3.3).

The case study leads with "automatically identify the LATEST reports". File dates and URLs
lie (re-uploads, mirror sites, "2024" in a 2025 filing's URL), so the reporting period is
resolved from the document's own CONTENT and the evidence for the choice is recorded
(CLAUDE.md §A — auditability: the choice of "latest" must be explained, not assumed). These
tests pin: content-heading signals outrank scattered prose; the latest period wins; a doc
with no recognisable date yields None (caller fails loud — never guess a period); genuine
near-ties are surfaced rather than hidden.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from isda_p3.discovery.period_resolver import (
    PeriodCandidate,
    explain,
    find_period_candidates,
    is_ambiguous,
    resolve_latest,
)
from isda_p3.models import ReportingPeriod


def test_resolve_km1_heading_picks_year_end() -> None:
    text = "Key metrics (KM1) as at 31 December 2025 are presented below."
    c = resolve_latest(text)
    assert c is not None
    assert c.period.year == 2025
    # a year-end quarter-end maps to Q4 (or annual) — both acceptable per the chunk.
    assert c.period.quarter in (4, None)
    # the evidence span points back at the matched substring (auditability).
    assert c.char_span is not None
    s, e = c.char_span
    assert "31 December 2025" in text[s:e]
    # an explicit "as at <quarter-end>" next to a KM1 heading is a strong signal.
    assert c.confidence >= Decimal("0.9")


def test_picks_latest_of_two_dates() -> None:
    text = (
        "The prior quarter ended 30 September 2025. "
        "These key metrics are presented as at 31 December 2025."
    )
    c = resolve_latest(text)
    assert c is not None
    assert c.period == ReportingPeriod(2025, 4)  # December is later than September


def test_content_heading_outranks_scattered_prose() -> None:
    # URL/filename trap analogue: a quarter-end date buried in a footnote vs one in a heading.
    text = (
        "Footnote: the 31 March 2025 figure was subsequently restated. "
        "Key prudential metrics (KM1) as at 30 June 2025 follow."
    )
    cands = find_period_candidates(text)
    by_period = {c.period: c for c in cands}
    mar = by_period[ReportingPeriod(2025, 1)]
    jun = by_period[ReportingPeriod(2025, 2)]
    # we do NOT trust scattered dates equally: the heading date is more confident.
    assert jun.confidence > mar.confidence
    # and it is also the latest, so it is resolved.
    c = resolve_latest(text)
    assert c is not None
    assert c.period == ReportingPeriod(2025, 2)


def test_no_recognisable_date_returns_none() -> None:
    text = "This document discusses our risk-management framework and governance approach."
    assert resolve_latest(text) is None
    assert find_period_candidates(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Disclosures for Q4 2025.", ReportingPeriod(2025, 4)),
        ("Results for the fourth quarter 2025.", ReportingPeriod(2025, 4)),
        ("Results for the fourth quarter of 2025.", ReportingPeriod(2025, 4)),
        ("Annual report FY2025.", ReportingPeriod(2025, None)),
        ("Annual report for the full year 2025.", ReportingPeriod(2025, None)),
        ("Highlights: 2025 Q4 results.", ReportingPeriod(2025, 4)),
        ("Position as at 31/12/2025.", ReportingPeriod(2025, 4)),
        ("Position as at 31.12.2025.", ReportingPeriod(2025, 4)),
        ("Reporting date 2025-12-31.", ReportingPeriod(2025, 4)),
        ("Quarter ended 30 September 2025.", ReportingPeriod(2025, 3)),
        ("As at 30 June 2025.", ReportingPeriod(2025, 2)),
        ("As at 31 March 2025.", ReportingPeriod(2025, 1)),
    ],
)
def test_period_token_and_date_variants_parse(text: str, expected: ReportingPeriod) -> None:
    c = resolve_latest(text)
    assert c is not None, text
    assert c.period == expected, text


def test_non_quarter_end_date_is_not_a_period_signal() -> None:
    # 15 May is not a Basel quarter-end; resolving a period from it would be guessing.
    assert resolve_latest("Board met on 15 May 2025 to approve the report.") is None


def test_implausible_year_is_rejected() -> None:
    # An OCR artefact / out-of-window year must NOT become a candidate — otherwise its
    # sort_key could silently dominate every real period (CLAUDE.md §A).
    assert resolve_latest("As at 31 December 1850.") is None
    assert resolve_latest("Annual report FY1850.") is None
    assert find_period_candidates("As at 31 December 99990.") == []


def test_explicit_as_at_outranks_bare_at() -> None:
    # "as at <date>" is canonical reporting phrasing; a bare "at" (e.g. "data at ...") is
    # a weaker signal and must earn less confidence, not the same.
    strong = resolve_latest("Reported as at 31 December 2025.")
    weak = resolve_latest("Carrying amounts measured at 31 December 2025.")
    assert strong is not None and weak is not None
    assert strong.confidence > weak.confidence


def test_explain_yields_audit_string() -> None:
    text = "Key metrics (KM1) as at 31 December 2025."
    c = resolve_latest(text)
    assert c is not None
    s = explain(c)
    assert "2025Q4" in s
    assert "31 December 2025" in s
    assert "confidence" in s.lower()


def test_is_ambiguous_flags_same_year_near_tie() -> None:
    text = (
        "Key metrics (KM1) as at 31 December 2025. "
        "Comparative key metrics (KM1) as at 30 September 2025."
    )
    cands = find_period_candidates(text)
    assert is_ambiguous(cands) is True
    # the resolver still returns the latest despite the flagged contest.
    c = resolve_latest(text)
    assert c is not None
    assert c.period == ReportingPeriod(2025, 4)


def test_clear_confidence_gap_is_not_ambiguous() -> None:
    text = (
        "Key metrics (KM1) as at 31 December 2025 are shown. "
        "Separately, an unrelated note mentions the date 30 September 2025 only once."
    )
    assert is_ambiguous(find_period_candidates(text)) is False


def test_prior_year_comparative_is_not_ambiguous() -> None:
    # Every annual report repeats last year's heading at equal confidence; recency
    # (different year) disambiguates, so this must NOT flag as ambiguous (no noise).
    text = (
        "Key metrics (KM1) as at 31 December 2025. "
        "Prior-year comparative key metrics (KM1) as at 31 December 2024."
    )
    cands = find_period_candidates(text)
    assert is_ambiguous(cands) is False
    c = resolve_latest(text)
    assert c is not None
    assert c.period == ReportingPeriod(2025, 4)


def test_is_ambiguous_trivial_cases() -> None:
    assert is_ambiguous([]) is False
    single = [PeriodCandidate(ReportingPeriod(2025, 4), "Q4 2025", Decimal("0.7"), (0, 7))]
    assert is_ambiguous(single) is False


def test_duplicate_period_is_deduplicated() -> None:
    text = "31 December 2025 ... and again 31 December 2025, reported as at 31 December 2025."
    cands = find_period_candidates(text)
    periods = [c.period for c in cands]
    assert periods.count(ReportingPeriod(2025, 4)) == 1


def test_prefer_quarter_end_excludes_annual() -> None:
    text = "Annual report FY2025 and key metrics (KM1) as at 31 December 2025."
    # FY (2025,FY) sorts after Q4 (2025,Q4), so "any" returns the annual period.
    any_c = resolve_latest(text, prefer="any")
    assert any_c is not None
    assert any_c.period == ReportingPeriod(2025, None)
    # quarter_end drops the annual candidate, leaving the Q4 quarter-end.
    qe_c = resolve_latest(text, prefer="quarter_end")
    assert qe_c is not None
    assert qe_c.period == ReportingPeriod(2025, 4)


def test_invalid_prefer_raises() -> None:
    with pytest.raises(ValueError):
        resolve_latest("As at 31 December 2025.", prefer="newest")  # type: ignore[arg-type]
