"""Tests for isda_p3.discovery.p3dh (chunk 3.2) — XBRL-CSV → canonical FieldValues.

THE HYBRID PROOF (plan D1 / audit M1): structured P3DH data flows through the SAME
canonical model and the SAME reconciliation gate as the PDF path — only provenance
differs (engine=P3DH, source_kind=XBRL_CSV, bbox=None). A manually-downloaded EU KM1
XBRL-CSV is parsed into :class:`FieldValue`s and reconciled to AUTO_PASSED, exactly as a
PDF extraction would be. The auto-fetch is intentionally stubbed (no verified EBA API);
the parser is real and driven entirely by ``config/p3dh_km1_map.yaml`` (CLAUDE.md §A.5).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import pytest

from isda_p3.discovery.p3dh import (
    DatapointMapping,
    StoredAs,
    load_p3dh_km1_map,
    parse_km1_xbrl_csv,
)
from isda_p3.models import (
    Bank,
    CheckOutcome,
    CheckType,
    Engine,
    Jurisdiction,
    MappingMethod,
    ReportingPeriod,
    SourceKind,
    Template,
    Unit,
    ValidationStatus,
)
from isda_p3.reconcile.engine import reconcile_template
from isda_p3.reconcile.identities import load_tolerances, load_weights

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "p3dh_km1_sample.csv"
_SOURCE_URL = "https://p3dh.eba.europa.eu/edap/deutsche-bank/2025Q4/km1.csv"

_BANK = Bank(
    id="deutsche-bank",
    name="Deutsche Bank AG",
    jurisdiction=Jurisdiction.EU,
    ir_url="https://www.db.com/ir",
    p3dh_lei="7LTWFZYICNSX8D621K86",
    number_locale="de_DE",  # XBRL is locale-independent — parse must NOT use this
    reporting_currency="EUR",
)
_PERIOD = ReportingPeriod(2025, 4)

_WEIGHTS = load_weights()
_TOLS = load_tolerances()


@pytest.fixture
def p3dh_caplog(caplog):
    """caplog that captures ``isda_p3.discovery.p3dh`` directly.

    ``setup_logging`` (triggered by another test) sets ``propagate=False`` on the
    ``isda_p3`` package logger, so caplog's root handler never sees these logs. Attach
    caplog's handler straight to the module logger so the never-fabricated assertions
    are robust regardless of suite order (same pattern as ``test_reconcile_engine``).
    """
    logger = logging.getLogger("isda_p3.discovery.p3dh")
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG, logger="isda_p3.discovery.p3dh")
    yield caplog
    logger.removeHandler(caplog.handler)


def _parse(source=None):
    return parse_km1_xbrl_csv(
        source if source is not None else _FIXTURE,
        bank=_BANK,
        period=_PERIOD,
        source_url=_SOURCE_URL,
    )


# --- structured ingest → canonical FieldValues ---------------------------------


def test_fixture_yields_seven_fieldvalues():
    values = _parse()
    assert len(values) == 7
    assert {fv.field_code for fv in values} == {f"KM1.{i}" for i in range(1, 8)}


def test_every_value_carries_p3dh_provenance():
    for fv in _parse():
        assert fv.provenance.engine is Engine.P3DH
        assert fv.provenance.source_kind is SourceKind.XBRL_CSV
        assert fv.provenance.bbox is None  # no page/bbox for a structured source
        assert fv.provenance.source_url == _SOURCE_URL
        assert fv.provenance.bank_id == "deutsche-bank"
        assert fv.provenance.period == _PERIOD
        # the map is a deterministic RULE, never an LLM judgment
        assert fv.mapping.method is MappingMethod.RULE
        assert fv.mapping.confidence == Decimal("1")
        assert fv.engine_values == {Engine.P3DH: fv.value}
        assert isinstance(fv.value, Decimal)


def test_matched_alias_is_the_datapoint_id():
    by = {fv.field_code: fv for fv in _parse()}
    assert by["KM1.1"].mapping.matched_alias == "eba_dpm:km1.r0010"
    assert by["KM1.5"].mapping.matched_alias == "eba_dpm:km1.r0050"


def test_amount_passes_through_in_canonical_millions():
    by = {fv.field_code: fv for fv in _parse()}
    assert by["KM1.1"].value == Decimal("50000")
    assert by["KM1.4"].value == Decimal("368000")
    assert by["KM1.1"].unit is Unit.EUR_M  # MONETARY resolved to the bank currency
    assert by["KM1.1"].raw_text == "50000"  # verbatim cell retained for audit


def test_fraction_ratio_is_converted_to_canonical_percent():
    by = {fv.field_code: fv for fv in _parse()}
    # stored as FRACTION 0.136 → canonical PERCENT 13.6
    assert by["KM1.5"].value == Decimal("13.6")
    assert by["KM1.5"].unit is Unit.PERCENT
    assert by["KM1.5"].raw_text == "0.136"  # verbatim, pre-conversion


def test_percent_ratio_passes_through():
    by = {fv.field_code: fv for fv in _parse()}
    assert by["KM1.7"].value == Decimal("17.7")
    assert by["KM1.7"].unit is Unit.PERCENT


def test_basis_axes_come_from_the_km1_template_spec():
    from isda_p3.models import EclBasis, FloorBasis

    by = {fv.field_code: fv for fv in _parse()}
    # KM1.1 capital: TRANSITIONAL / NA ; KM1.5 ratio: NA / FINAL (matches km1.yaml)
    assert by["KM1.1"].ecl_basis is EclBasis.TRANSITIONAL
    assert by["KM1.1"].floor_basis is FloorBasis.NA
    assert by["KM1.5"].ecl_basis is EclBasis.NA
    assert by["KM1.5"].floor_basis is FloorBasis.FINAL


def test_accepts_bytes_as_well_as_path():
    from_bytes = _parse(_FIXTURE.read_bytes())
    from_path = _parse(_FIXTURE)
    assert {fv.field_code: fv.value for fv in from_bytes} == {
        fv.field_code: fv.value for fv in from_path
    }


# --- THE HYBRID PROOF: same canonical result clears the same gate ---------------


def test_parsed_values_all_auto_pass_reconciliation():
    values = {fv.field_code: fv for fv in _parse()}
    results = reconcile_template(values, Template.KM1, tolerances=_TOLS, weights=_WEIGHTS)
    assert len(results) == 7
    assert all(r.status is ValidationStatus.AUTO_PASSED for r in results)


def test_two_engine_skips_but_identity_fires_so_auto_passed():
    # Single-engine (P3DH) → TWO_ENGINE has nothing to cross-check → SKIP; but the ratio
    # identity fires and PASSes, so the field still AUTO_PASSEs (consistent with 1.9).
    values = {fv.field_code: fv for fv in _parse()}
    results = reconcile_template(values, Template.KM1, tolerances=_TOLS, weights=_WEIGHTS)
    km15 = next(r for r in results if r.field_value.field_code == "KM1.5")

    outcomes = {c.check_type: c.outcome for c in km15.checks}
    assert outcomes[CheckType.TWO_ENGINE] is CheckOutcome.SKIP
    assert outcomes[CheckType.RATIO_IDENTITY] is CheckOutcome.PASS
    assert km15.validation_basis == (CheckType.RATIO_IDENTITY,)  # SKIP excluded
    assert km15.status is ValidationStatus.AUTO_PASSED


# --- never fabricate: unmapped ignored, junk absent, malformed raises -----------


def test_unmapped_datapoint_is_ignored_not_fabricated(p3dh_caplog):
    csv_bytes = (
        "datapoint,value\n"
        "eba_dpm:km1.r0010,50000\n"
        "eba_dpm:km1.r9999,123456\n"  # not in the map
    ).encode("utf-8")
    values = _parse(csv_bytes)
    assert {fv.field_code for fv in values} == {"KM1.1"}  # the unmapped row never appears
    assert "eba_dpm:km1.r9999" in p3dh_caplog.text  # logged, not silently dropped


def test_mapped_datapoint_with_junk_value_is_absent_not_zero(p3dh_caplog):
    csv_bytes = (
        "datapoint,value\n"
        "eba_dpm:km1.r0010,50000\n"
        "eba_dpm:km1.r0040,not-a-number\n"  # mapped, but unparseable
    ).encode("utf-8")
    values = _parse(csv_bytes)
    by = {fv.field_code: fv for fv in values}
    assert "KM1.4" not in by  # absent — never coerced to 0
    assert by["KM1.1"].value == Decimal("50000")
    assert "KM1.4" in p3dh_caplog.text


def test_empty_value_is_absent_not_zero(p3dh_caplog):
    csv_bytes = b"datapoint,value\neba_dpm:km1.r0010,\n"
    assert _parse(csv_bytes) == []  # blank cell → absent, nothing emitted
    assert "KM1.1" in p3dh_caplog.text  # the "logged" half of "logged, never 0"


def test_malformed_csv_missing_required_column_raises():
    csv_bytes = b"concept,amount\neba_dpm:km1.r0010,50000\n"  # no datapoint/value columns
    with pytest.raises(ValueError, match="datapoint"):
        _parse(csv_bytes)


def test_blank_datapoint_cell_raises():
    # A structurally present but empty datapoint is malformed, not "unmapped" → raise.
    csv_bytes = b"datapoint,value\n  ,50000\n"
    with pytest.raises(ValueError, match="empty datapoint"):
        _parse(csv_bytes)


def test_empty_csv_raises():
    with pytest.raises(ValueError, match="empty file"):
        _parse(b"")


def test_undecodable_bytes_raise_value_error():
    # The contract is ValueError, not a raw UnicodeDecodeError leaking past the handler.
    with pytest.raises(ValueError, match="cannot decode"):
        _parse(b"\xff\xfe\x00bad")


def test_map_referencing_unknown_field_raises(monkeypatch):
    # A map entry pointing at a field the KM1 template does not declare is a config error.
    from isda_p3.discovery import p3dh

    bad = {
        "eba_dpm:km1.r0010": DatapointMapping("eba_dpm:km1.r0010", "KM1.99", StoredAs.AMOUNT)
    }
    monkeypatch.setattr(p3dh, "load_p3dh_km1_map", lambda: bad)
    with pytest.raises(ValueError, match="unknown KM1 field"):
        _parse(b"datapoint,value\neba_dpm:km1.r0010,50000\n")


# --- config map loader: fail-fast validation (no silent mis-mapping) ------------


def _write_map(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "p3dh_km1_map.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_map_wrong_template_tag_raises(tmp_path):
    path = _write_map(tmp_path, "template: OV1\ndatapoints:\n  - {datapoint: x, field_code: KM1.1, stored_as: AMOUNT}\n")
    with pytest.raises(ValueError, match="template"):
        load_p3dh_km1_map(path)


def test_load_map_duplicate_datapoint_raises(tmp_path):
    path = _write_map(
        tmp_path,
        "template: KM1\ndatapoints:\n"
        "  - {datapoint: dp1, field_code: KM1.1, stored_as: AMOUNT}\n"
        "  - {datapoint: dp1, field_code: KM1.2, stored_as: AMOUNT}\n",
    )
    with pytest.raises(ValueError, match="duplicate datapoint"):
        load_p3dh_km1_map(path)


def test_load_map_bad_stored_as_raises(tmp_path):
    path = _write_map(tmp_path, "template: KM1\ndatapoints:\n  - {datapoint: dp1, field_code: KM1.1, stored_as: WAT}\n")
    with pytest.raises(ValueError, match="stored_as"):
        load_p3dh_km1_map(path)


def test_load_map_missing_stored_as_raises(tmp_path):
    path = _write_map(tmp_path, "template: KM1\ndatapoints:\n  - {datapoint: dp1, field_code: KM1.1}\n")
    with pytest.raises(ValueError, match="stored_as"):
        load_p3dh_km1_map(path)
