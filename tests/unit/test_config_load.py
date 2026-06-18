"""Tests for isda_p3.config_load — typed banks/template loaders (chunk 0.4).

TDD-first. The real ``config/banks.yaml`` and ``config/templates/{km1,ov1}.yaml``
are exercised through the shipped loaders; malformed inputs are written to temp
files to prove every validation path raises a ``ValueError`` naming the offender.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from isda_p3.config_load import (
    FieldSpec,
    TemplateSpec,
    load_banks,
    load_template,
)
from isda_p3.models import (
    Bank,
    EclBasis,
    FieldKind,
    FloorBasis,
    Jurisdiction,
    Template,
)

# --- a known-good single bank entry, mutated per-test for the failure cases ----
_GOOD_ENTRY = {
    "id": "barclays",
    "name": "Barclays",
    "jurisdiction": "UK",
    "ir_url": "https://home.barclays",
    "p3dh_lei": None,
    "number_locale": "en_GB",
    "reporting_currency": "GBP",
}


def _write_banks(tmp_path, entries):
    """Dump ``entries`` (a list of dicts) to a temp ``banks.yaml`` and return its path."""
    path = tmp_path / "banks.yaml"
    path.write_text(yaml.safe_dump({"banks": entries}, sort_keys=False), encoding="utf-8")
    return path


# --- load_banks: the real roster ----------------------------------------------


def test_load_banks_returns_full_roster():
    banks = load_banks()
    assert isinstance(banks, tuple)
    assert all(isinstance(b, Bank) for b in banks)
    assert len(banks) >= 25


def test_load_banks_ids_unique():
    banks = load_banks()
    ids = [b.id for b in banks]
    assert len(ids) == len(set(ids))


def test_load_banks_every_jurisdiction_parses_to_enum():
    banks = load_banks()
    assert all(isinstance(b.jurisdiction, Jurisdiction) for b in banks)


@pytest.mark.parametrize(
    "bank_id,locale,currency",
    [
        ("barclays", "en_GB", "GBP"),
        ("hsbc", "en_GB", "USD"),  # the trap: UK bank reporting in USD
        ("deutsche-bank", "de_DE", "EUR"),
        ("ubs", "de_CH", "USD"),  # CH bank reporting in USD
        ("standard-chartered", "en_GB", "USD"),
    ],
)
def test_load_banks_locale_and_currency_traps(bank_id, locale, currency):
    by_id = {b.id: b for b in load_banks()}
    assert bank_id in by_id, f"missing bank {bank_id!r}"
    bank = by_id[bank_id]
    assert bank.number_locale == locale
    assert bank.reporting_currency == currency


def test_load_banks_all_currencies_are_3_letter_upper():
    for b in load_banks():
        assert len(b.reporting_currency) == 3
        assert b.reporting_currency.isupper()


def test_load_banks_lei_is_none_until_populated():
    # Chunk 0.4 deliberately leaves every LEI null (no guessed LEIs).
    assert all(b.p3dh_lei is None for b in load_banks())


# --- load_banks: validation failure paths -------------------------------------


def test_load_banks_duplicate_id_raises(tmp_path):
    path = _write_banks(tmp_path, [dict(_GOOD_ENTRY), dict(_GOOD_ENTRY)])
    with pytest.raises(ValueError, match="barclays"):
        load_banks(path)


def test_load_banks_bad_jurisdiction_raises(tmp_path):
    bad = {**_GOOD_ENTRY, "id": "fakebank", "jurisdiction": "ZZ"}
    path = _write_banks(tmp_path, [bad])
    with pytest.raises(ValueError, match="fakebank"):
        load_banks(path)


def test_load_banks_missing_field_raises(tmp_path):
    bad = {k: v for k, v in _GOOD_ENTRY.items() if k != "reporting_currency"}
    bad["id"] = "nocurrency"
    path = _write_banks(tmp_path, [bad])
    with pytest.raises(ValueError, match="nocurrency"):
        load_banks(path)


def test_load_banks_two_letter_currency_raises(tmp_path):
    bad = {**_GOOD_ENTRY, "id": "shortccy", "reporting_currency": "GB"}
    path = _write_banks(tmp_path, [bad])
    with pytest.raises(ValueError, match="shortccy"):
        load_banks(path)


def test_load_banks_empty_name_raises(tmp_path):
    bad = {**_GOOD_ENTRY, "id": "noname", "name": "   "}
    path = _write_banks(tmp_path, [bad])
    with pytest.raises(ValueError, match="noname"):
        load_banks(path)


# --- load_template -------------------------------------------------------------


def test_load_template_km1():
    spec = load_template(Template.KM1)
    assert isinstance(spec, TemplateSpec)
    assert spec.template is Template.KM1
    assert len(spec.fields) >= 2
    assert all(isinstance(f, FieldSpec) for f in spec.fields)


def test_load_template_field_codes_unique():
    for t in (Template.KM1, Template.OV1):
        codes = [f.code for f in load_template(t).fields]
        assert len(codes) == len(set(codes))


def test_load_template_by_code_hit_and_miss():
    spec = load_template(Template.KM1)
    hit = spec.by_code("KM1.4")
    assert hit is not None
    assert hit.code == "KM1.4"
    assert spec.by_code("nope") is None


def test_load_template_enums_are_typed():
    for f in load_template(Template.KM1).fields:
        assert isinstance(f.kind, FieldKind)
        assert isinstance(f.ecl_basis, EclBasis)
        assert isinstance(f.floor_basis, FloorBasis)
        assert isinstance(f.row_label_aliases, tuple)
        assert f.row_label_aliases  # non-empty


def test_load_template_missing_file_raises():
    # CVA1 has no shipped config file in chunk 0.4.
    with pytest.raises(FileNotFoundError):
        load_template(Template.CVA1)


# --- load_template: validation failure paths ----------------------------------


def _write_template(tmp_path, monkeypatch, body):
    """Write a temp template file and point Paths.TEMPLATES at it."""
    from isda_p3.config_load import Paths

    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "km1.yaml").write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.setattr(Paths, "TEMPLATES", tdir)


def test_load_template_duplicate_code_raises(tmp_path, monkeypatch):
    _write_template(
        tmp_path,
        monkeypatch,
        """
        template: KM1
        fields:
          - code: KM1.1
            row_label_aliases: ["CET1 capital"]
            kind: MONETARY
            ecl_basis: FULLY_LOADED
            floor_basis: NA
          - code: KM1.1
            row_label_aliases: ["CET1 capital again"]
            kind: MONETARY
            ecl_basis: FULLY_LOADED
            floor_basis: NA
        """,
    )
    with pytest.raises(ValueError, match="KM1.1"):
        load_template(Template.KM1)


def test_load_template_empty_aliases_raises(tmp_path, monkeypatch):
    _write_template(
        tmp_path,
        monkeypatch,
        """
        template: KM1
        fields:
          - code: KM1.1
            row_label_aliases: []
            kind: MONETARY
            ecl_basis: FULLY_LOADED
            floor_basis: NA
        """,
    )
    with pytest.raises(ValueError, match="KM1.1"):
        load_template(Template.KM1)


def test_load_template_bad_kind_raises(tmp_path, monkeypatch):
    _write_template(
        tmp_path,
        monkeypatch,
        """
        template: KM1
        fields:
          - code: KM1.1
            row_label_aliases: ["CET1 capital"]
            kind: BANANAS
            ecl_basis: FULLY_LOADED
            floor_basis: NA
        """,
    )
    with pytest.raises(ValueError, match="KM1.1"):
        load_template(Template.KM1)
