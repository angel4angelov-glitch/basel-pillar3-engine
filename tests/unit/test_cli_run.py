"""Tests for the ``isda-p3 run`` command (chunk 1.8) — stub engine, no store.

Exercises ``run_command`` with an injected stub engine and ``store=False`` so there
is no PDF, no LLM, no network and no parquet write. Asserts the formatted audit line
carries the graded essentials: field code, value+unit, fired check + outcome, source
page, mapping method, and status (CLAUDE.md §A — every number is defensible in plain
English with provenance).
"""

from __future__ import annotations

from decimal import Decimal


import pytest

from isda_p3.cli import _parse_period, _parse_template, format_result, run_command
from isda_p3.models import (
    BBox,
    Bank,
    Engine,
    Jurisdiction,
    RawCell,
    ReportingPeriod,
    Template,
    ValidationStatus,
)
from isda_p3.reconcile.identities import load_tolerances, load_weights

_SYNTHETIC_KM1 = [
    ("Common Equity Tier 1 (CET1) capital", "48,000", "45,000"),
    ("Tier 1 capital", "56,000", "52,000"),
    ("Total capital", "64,000", "60,000"),
    ("Total risk-weighted assets (RWA)", "320,000", "310,000"),
    ("Common Equity Tier 1 ratio (%)", "15.0", "14.5"),
    ("Tier 1 ratio (%)", "17.5", "17.0"),
    ("Total capital ratio (%)", "20.0", "19.5"),
]

_BANK = Bank(
    id="synthetic",
    name="Synthetic Bank",
    jurisdiction=Jurisdiction.UK,
    ir_url="https://example.test/pillar3.pdf",
    p3dh_lei=None,
    number_locale="en_GB",
    reporting_currency="GBP",
)


def _grid(rows: list[tuple[str, ...]]) -> list[RawCell]:
    cells: list[RawCell] = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cells.append(
                RawCell(
                    row_idx=r,
                    col_idx=c,
                    text=text,
                    bbox=BBox(page=4, x0=float(c), y0=float(r), x1=float(c) + 1, y1=float(r) + 1),
                    engine=Engine.DOCLING,
                    row_label=None,
                    col_label=None,
                )
            )
    return cells


class _StubEngine:
    engine = Engine.DOCLING

    def __init__(self, groups: list[list[RawCell]]) -> None:
        self._groups = groups

    def extract_tables(self, pdf_path, pages=None) -> list[list[RawCell]]:
        return self._groups

    def extract(self, pdf_path, pages=None) -> list[RawCell]:
        return [c for g in self._groups for c in g]


def _run(capsys):
    results = run_command(
        bank=_BANK,
        template=Template.KM1,
        period=ReportingPeriod(2025, 4),
        pdf_path=None,  # ignored by the stub engine
        engine=_StubEngine([_grid(_SYNTHETIC_KM1)]),
        store=False,
        tolerances=load_tolerances(),
        weights=load_weights(),
    )
    return results, capsys.readouterr().out


# --- run_command: prints one line per field, returns results ----------------------


def test_run_prints_one_line_per_field_and_returns_results(capsys):
    results, out = _run(capsys)
    assert len(results) == 7
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 7


def test_run_no_store_writes_nothing(capsys, monkeypatch):
    # If --no-store leaked into a write, append_rows would mkdir/write the store.
    # Patch the name the CLI actually calls (it imports append_rows by value).
    import isda_p3.cli as cli

    def _boom(*a, **k):  # pragma: no cover - asserted not called
        raise AssertionError("append_rows must not be called when store=False")

    monkeypatch.setattr(cli, "append_rows", _boom)
    results, _ = _run(capsys)
    assert all(r.status is ValidationStatus.AUTO_PASSED for r in results)


def test_formatted_line_contains_field_value_check_page_map_status(capsys):
    _, out = _run(capsys)
    cet1_line = next(ln for ln in out.splitlines() if ln.startswith("KM1.5"))
    assert "KM1.5" in cet1_line  # field
    assert "15.0%" in cet1_line  # value + unit
    assert "RATIO_IDENTITY PASS" in cet1_line  # fired check + outcome
    assert "page 4" in cet1_line  # source provenance
    assert "map=RULE" in cet1_line  # mapping method
    assert "AUTO_PASSED" in cet1_line  # status


# --- format_result: the formatter in isolation ------------------------------------


def test_format_result_monetary_and_basis_axes(capsys):
    results, _ = _run(capsys)
    by = {r.field_value.field_code: r for r in results}
    line = format_result(by["KM1.4"])
    assert line.startswith("KM1.4")
    assert "320000 GBP_M" in line  # monetary value carries its unit
    assert "[ecl=NA floor=FINAL]" in line  # both basis axes shown
    assert "map=RULE" in line


def test_format_result_value_is_exact_not_float(capsys):
    results, _ = _run(capsys)
    by = {r.field_value.field_code: r for r in results}
    assert by["KM1.5"].field_value.value == Decimal("15.0")
    assert "15.0%" in format_result(by["KM1.5"])
    assert "15.000000001" not in format_result(by["KM1.5"])


# --- arg parsing: bad input exits cleanly, never a raw traceback -------------------


def test_parse_template_good_and_bad():
    assert _parse_template("KM1") is Template.KM1
    with pytest.raises(SystemExit, match="unknown template"):
        _parse_template("BADCODE")


def test_parse_period_good_and_bad():
    assert _parse_period("2025Q4") == ReportingPeriod(2025, 4)
    with pytest.raises(SystemExit, match="invalid period"):
        _parse_period("2025-Q4")
