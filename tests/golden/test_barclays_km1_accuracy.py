"""PDF-free golden regression on the REAL Barclays KM1 (chunk 5.1).

Reproduces the MEASURED extraction accuracy and the reconciliation identities from
two committed, legal, tiny fixtures — the human golden YAML and the frozen
extracted-cells JSON — with **no PDF, no Docling, no API key**. So this runs in the
default CI suite (the opt-in live-PDF re-extraction is ``tests/integration/test_m1_real``).

It is the regression backstop for the whole slice: a mapping or reconcile change that
silently drops Barclays' real digits, or breaks an identity on real numbers, fails here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from isda_p3.config import Paths
from isda_p3.golden import compare_to_golden, load_fixture, load_golden
from isda_p3.models import CheckOutcome, CheckType, Template, ValidationStatus
from isda_p3.reconcile.checks import magnitude_sanity
from isda_p3.reconcile.engine import reconcile_template
from isda_p3.reconcile.identities import load_magnitude_bands, load_tolerances, load_weights

pytestmark = pytest.mark.golden

_GOLDEN = Paths.GOLDEN_EXPECTED / "barclays_2026Q1_km1.yaml"
_FIXTURE = Paths.GOLDEN_EXPECTED / "barclays_2026Q1_km1_cells.json"

# The accuracy actually MEASURED against the real PDF (chunk 5.1): 14 of 15 golden
# cells Decimal-exact. KM1.14 (leverage ratio) is the one honest miss — Docling merged
# its label cell with the next section header, so the exact-match mapper cannot claim it.
_MEASURED_ACCURACY = Decimal("14") / Decimal("15")
_PERMITTED_MISSES = {"KM1.14"}


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
    # the only tolerated miss is the documented Docling cell-merge on KM1.14; a NEW
    # miss (some other row dropping out) must fail this test loudly.
    assert {c.field_code for c in report.missing} <= _PERMITTED_MISSES
    assert report.extras == ()  # never fabricate a field not in the golden


def test_capital_ratio_identities_pass_on_real_digits():
    fixture = load_fixture(_FIXTURE)
    values = fixture.to_fieldvalues(bank_id="barclays")

    results = reconcile_template(
        values, Template.KM1, tolerances=load_tolerances(), weights=load_weights()
    )
    by = {r.field_value.field_code: r for r in results}

    # CET1 / Tier 1 / Total-capital ratio identities all hold on the REAL numbers,
    # so each auto-passes (confidence >= threshold, no FAIL). This is the reconcile
    # gate proven on real extracted digits, reproducibly, with no PDF.
    for code in ("KM1.5", "KM1.6", "KM1.7"):
        ratio_checks = [c for c in by[code].checks if c.check_type is CheckType.RATIO_IDENTITY]
        assert ratio_checks, f"{code}: no ratio-identity check fired"
        assert all(c.outcome is CheckOutcome.PASS for c in ratio_checks), code
        assert by[code].status is ValidationStatus.AUTO_PASSED, code


def test_real_gbp_digits_pass_magnitude_no_false_fail():
    # Step 4 (no false positives), GBP side: the magnitude bands are millions-normalised and
    # currency-agnostic, so every real Barclays (£m) golden cell PASSES — a GBP filer is not
    # false-FAILed by bands shared with the USD HSBC case. KM1.14 stays the only miss.
    bands = load_magnitude_bands(Template.KM1)
    fvs = load_fixture(_FIXTURE).to_fieldvalues(bank_id="barclays")
    for code, fv in fvs.items():
        r = magnitude_sanity(fv, bands)
        assert r.outcome is CheckOutcome.PASS, f"{code} false-FAILed magnitude: {r.detail}"
