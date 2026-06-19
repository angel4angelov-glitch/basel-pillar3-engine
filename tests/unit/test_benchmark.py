"""Tests for isda_p3.analytics.benchmark (chunk 4.2) — exact-match + bootstrap CI.

The pure statistics that turn a golden set into the slide's headline accuracy
number. ``exact_match`` is the deterministic scorer (Decimal ==, never float
tolerance); ``bootstrap_ci`` is seeded so the reported CI is reproducible. The
audit point (CLAUDE.md §A / plan C3): a wide CI from a small golden N must never be
silently presented as precise — these tests pin the determinism the honesty rests
on, and the integration test logs N.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from isda_p3.analytics.benchmark import bootstrap_ci, exact_match

# --- exact_match ---------------------------------------------------------------


def test_exact_match_perfect():
    extracted = {"KM1.1": Decimal("48000"), "KM1.5": Decimal("15.0")}
    expected = {"KM1.1": Decimal("48000"), "KM1.5": Decimal("15.0")}
    assert exact_match(extracted, expected) == (2, 2)


def test_exact_match_partial_value_mismatch():
    extracted = {"KM1.1": Decimal("48000"), "KM1.5": Decimal("15.1")}  # 15.1 != 15.0
    expected = {"KM1.1": Decimal("48000"), "KM1.5": Decimal("15.0")}
    assert exact_match(extracted, expected) == (1, 2)


def test_exact_match_missing_field_is_non_match():
    extracted = {"KM1.1": Decimal("48000")}  # KM1.5 missing
    expected = {"KM1.1": Decimal("48000"), "KM1.5": Decimal("15.0")}
    assert exact_match(extracted, expected) == (1, 2)


def test_exact_match_extra_field_does_not_inflate():
    # Extra extracted field (not expected) is a non-match: it adds no credit and
    # the denominator stays len(expected).
    extracted = {"KM1.1": Decimal("48000"), "KM1.9": Decimal("999")}
    expected = {"KM1.1": Decimal("48000")}
    assert exact_match(extracted, expected) == (1, 1)


def test_exact_match_decimal_exact_not_float_tolerant():
    # 15.00 == 15.0 as Decimal (numerically equal); a true digit difference fails.
    assert exact_match({"a": Decimal("15.00")}, {"a": Decimal("15.0")}) == (1, 1)
    assert exact_match({"a": Decimal("15.01")}, {"a": Decimal("15.0")}) == (0, 1)


def test_exact_match_empty_expected_is_zero_total():
    assert exact_match({"a": Decimal("1")}, {}) == (0, 0)


# --- bootstrap_ci --------------------------------------------------------------

# Regression-pinned for (hits=7, n=10, iters=2000, alpha=0.05, seed=0). Captured
# from the seeded implementation; locks reproducibility (plan: seeded CI).
_PINNED_7_10 = (0.4, 1.0)


def test_bootstrap_ci_deterministic_for_fixed_seed():
    a = bootstrap_ci(7, 10, seed=0)
    b = bootstrap_ci(7, 10, seed=0)
    assert a == b  # same seed -> identical CI
    assert a == _PINNED_7_10  # stable lo/hi (regression pin)


def test_bootstrap_ci_brackets_the_rate():
    lo, hi = bootstrap_ci(7, 10, seed=0)
    rate = 7 / 10
    assert 0.0 <= lo <= rate <= hi <= 1.0


def test_bootstrap_ci_different_seed_can_differ_but_stays_in_range():
    lo, hi = bootstrap_ci(7, 10, seed=99)
    assert 0.0 <= lo <= hi <= 1.0


def test_bootstrap_ci_degenerate_all_hits_is_one():
    assert bootstrap_ci(10, 10, seed=0) == (1.0, 1.0)


def test_bootstrap_ci_degenerate_no_hits_is_zero():
    assert bootstrap_ci(0, 10, seed=0) == (0.0, 0.0)


def test_bootstrap_ci_n_zero_returns_zero_zero():
    # Documented: no trials -> no information -> (0.0, 0.0).
    assert bootstrap_ci(0, 0, seed=0) == (0.0, 0.0)


def test_bootstrap_ci_rejects_hits_out_of_range():
    with pytest.raises(ValueError):
        bootstrap_ci(11, 10, seed=0)
    with pytest.raises(ValueError):
        bootstrap_ci(-1, 10, seed=0)
    # Negative hits must raise even when n==0 (range check precedes the shortcut).
    with pytest.raises(ValueError):
        bootstrap_ci(-1, 0, seed=0)
