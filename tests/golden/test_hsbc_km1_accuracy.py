"""PDF-free golden regression on the REAL HSBC KM1 (chunk H2).

Reproduces the MEASURED extraction accuracy, the end-to-end scale fix, and the
reconciliation identities from two committed, legal, tiny fixtures — the human
golden YAML and the frozen extracted-cells JSON — with **no PDF, no Docling, no API
key**. So it runs in the default CI suite (the opt-in live-PDF re-extraction is
``tests/integration/test_m1_real``).

HSBC is the second bank and the first $bn filer: this test is the regression backstop
for the reusable ``monetary_scale`` dimension (a mapping/normalise change that drops
the x1000 lift, or re-introduces the 1000x-low magnitude, fails here on real digits).
KM1.7 is the one documented, deferred miss (rows 3/7 share the label "Total capital");
the test pins it as the ONLY permitted miss so a NEW regression cannot hide behind it.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from isda_p3.config import Paths
from isda_p3.golden import compare_to_golden, load_fixture, load_golden
from isda_p3.mapping.normalise import normalise, scale_multiplier
from isda_p3.models import CheckOutcome, CheckType, Template, Unit, ValidationStatus
from isda_p3.reconcile.checks import magnitude_sanity
from isda_p3.reconcile.engine import reconcile_template
from isda_p3.reconcile.identities import load_magnitude_bands, load_tolerances, load_weights

pytestmark = pytest.mark.golden

_GOLDEN = Paths.GOLDEN_EXPECTED / "hsbc_2026Q1_km1.yaml"
_FIXTURE = Paths.GOLDEN_EXPECTED / "hsbc_2026Q1_km1_cells.json"

# The accuracy actually MEASURED against the real PDF (chunk H2): 14 of 15 golden
# cells Decimal-exact. KM1.7 (Total capital ratio) is the one honest miss — HSBC prints
# rows 3 (amount) and 7 (ratio) with the identical label "Total capital", which the
# exact-label rule + bounded-LLM both collapse to the topmost (amount) row. Resolving it
# needs enumerator/section-aware matching in src — held for sign-off, not silently built.
_MEASURED_ACCURACY = Decimal("14") / Decimal("15")
_PERMITTED_MISSES = {"KM1.7"}

# Monetary rows are disclosed in USD billions; the canonical Unit is millions, so each
# must land at its x1000 magnitude (the scale fix), tagged USD_M (currency flows as USD).
_MONETARY_CODES = {"KM1.1", "KM1.2", "KM1.3", "KM1.4", "KM1.13", "KM1.15", "KM1.16",
                   "KM1.18", "KM1.19"}


def test_committed_fixtures_present():
    # No skip-guard: these are committed, so CI must always have them (catches an
    # accidental deletion or a path drift, rather than silently passing on nothing).
    assert _GOLDEN.exists(), _GOLDEN
    assert _FIXTURE.exists(), _FIXTURE


def test_accuracy_reproduces_from_frozen_cells():
    golden = load_golden(_GOLDEN)
    fixture = load_fixture(_FIXTURE)
    report = compare_to_golden(golden.values, fixture.values)

    # the floor the chunk asks CI to assert: never silently regress below measured.
    assert report.accuracy >= _MEASURED_ACCURACY
    # every extracted digit is correct — zero misreads is the strong invariant here.
    assert report.mismatches == ()
    # the only tolerated miss is the documented KM1.7 label collision; a NEW miss (some
    # other row dropping out) must fail this test loudly.
    assert {c.field_code for c in report.missing} <= _PERMITTED_MISSES
    assert report.extras == ()  # never fabricate a field not in the golden


def test_scale_fix_under_ci_no_1000x_error():
    # The H2 firewall, reproduced PDF-free: every monetary row is USD_M (currency flows
    # as USD) and at its $bn->$m magnitude. If the scale fix regressed (read $bn as $m),
    # KM1.1 would be 124, not 124000 — and the golden comparison above would already FAIL.
    fixture = load_fixture(_FIXTURE)
    by = {c.field_code: c for c in fixture.cells}
    for code in _MONETARY_CODES:
        if code in by:
            assert by[code].unit is Unit.USD_M, code
            assert by[code].monetary_scale == "billions", code
    # the canonical magnitudes, pinned (no 1000x-low survivor)
    assert by["KM1.1"].value == Decimal("124000")
    assert by["KM1.4"].value == Decimal("883800")
    assert by["KM1.13"].value == Decimal("2947000")


def test_capital_ratio_identities_pass_on_real_digits():
    fixture = load_fixture(_FIXTURE)
    values = fixture.to_fieldvalues(bank_id="hsbc")

    results = reconcile_template(
        values, Template.KM1, tolerances=load_tolerances(), weights=load_weights()
    )
    by = {r.field_value.field_code: r for r in results}

    # CET1 / Tier 1 ratio identities hold on the REAL USD digits (124000/883800x100 =
    # 14.03 vs stated 14.0; 146200/883800x100 = 16.54 vs stated 16.5 — both within tol),
    # so each auto-passes. This is the reconcile gate proven on real extracted $bn->$m
    # digits, reproducibly, with no PDF.
    for code in ("KM1.5", "KM1.6"):
        ratio_checks = [c for c in by[code].checks if c.check_type is CheckType.RATIO_IDENTITY]
        assert ratio_checks, f"{code}: no ratio-identity check fired"
        assert all(c.outcome is CheckOutcome.PASS for c in ratio_checks), code
        assert by[code].status is ValidationStatus.AUTO_PASSED, code

    # KM1.7 (Total capital ratio) is the deferred miss: it is absent, so its identity
    # cannot even be evaluated. Pin that it stayed out of the dataset (no wrong number
    # was admitted in its place).
    assert "KM1.7" not in by, "KM1.7 must stay absent until enumerator-aware matching lands"


def test_magnitude_sanity_firewall_on_real_hsbc_digits():
    # THE H3 HEADLINE (Step 3), on the real frozen HSBC cells. Each monetary cell is read
    # two ways: at the CORRECT $bn->$m magnitude (the committed value) it PASSES the band;
    # re-read under the WRONG scale (millions, the H2 bug) it is 1000x low and FAILS. This is
    # the exact uniform-scale error the scale-invariant ratio identities cannot see (H2).
    bands = load_magnitude_bands(Template.KM1)
    fvs = load_fixture(_FIXTURE).to_fieldvalues(bank_id="hsbc")
    monetary = {c: fv for c, fv in fvs.items() if fv.unit is Unit.USD_M}
    assert monetary, "expected USD monetary rows in the HSBC fixture"

    for code, fv in monetary.items():
        # correct (billions-scaled) magnitude -> PASS
        assert magnitude_sanity(fv, bands).outcome is CheckOutcome.PASS, code
        # the SAME raw cell parsed under the millions bug (1000x low) -> FAIL
        wrong_value = normalise(fv.raw_text, "en_GB", Unit.USD_M,
                                monetary_scale=scale_multiplier("millions"))
        wrong = replace(fv, value=wrong_value)
        assert magnitude_sanity(wrong, bands).outcome is CheckOutcome.FAIL, code


def test_real_digits_pass_magnitude_no_false_fail():
    # Step 4 (no false positives): every real HSBC golden cell PASSES its band — the bands
    # are wide enough never to false-FAIL a correct disclosure. Run via the full engine so the
    # routing is exercised: no field is FLAGGED by a magnitude FAIL on correct data.
    bands = load_magnitude_bands(Template.KM1)
    fvs = load_fixture(_FIXTURE).to_fieldvalues(bank_id="hsbc")
    for code, fv in fvs.items():
        r = magnitude_sanity(fv, bands)
        assert r.outcome is CheckOutcome.PASS, f"{code} false-FAILed magnitude: {r.detail}"

    # and the engine still routes KM1.5/KM1.6 to AUTO_PASSED (magnitude PASS did not demote
    # them; a magnitude FAIL would have). KM1.7 stays absent (the deferred miss).
    results = reconcile_template(
        fvs, Template.KM1, tolerances=load_tolerances(), weights=load_weights()
    )
    by = {r.field_value.field_code: r for r in results}
    for code in ("KM1.5", "KM1.6"):
        assert by[code].status is ValidationStatus.AUTO_PASSED, code
        mag = [c for c in by[code].checks if c.check_type is CheckType.MAGNITUDE_SANITY]
        assert mag and mag[0].outcome is CheckOutcome.PASS, code
