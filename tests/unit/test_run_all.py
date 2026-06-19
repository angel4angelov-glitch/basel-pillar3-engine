"""Tests for the config-driven ``isda-p3 run-all`` batch (chunk 4.3).

Proves "new bank = config, not code": a directory of ``<bank_id>_<period>_<template>.pdf``
files is swept with injected stub engines (no PDF / LLM / network). Banks resolve from
``banks.yaml``; an unknown bank is logged + summarised, never crashing the batch; the
dataset ends with rows for every processed bank.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from isda_p3.cli import BankRunResult, run_all_command
from isda_p3.config import Paths
from isda_p3.models import (
    BBox,
    Engine,
    RawCell,
    ReportingPeriod,
    Template,
)
from isda_p3.reconcile.identities import load_tolerances, load_weights
from isda_p3.store.dataset import read_dataset_decimals

_SYNTHETIC_KM1 = [
    ("Common Equity Tier 1 (CET1) capital", "48,000", "45,000"),
    ("Tier 1 capital", "56,000", "52,000"),
    ("Total capital", "64,000", "60,000"),
    ("Total risk-weighted assets (RWA)", "320,000", "310,000"),
    ("Common Equity Tier 1 ratio (%)", "15.0", "14.5"),
    ("Tier 1 ratio (%)", "17.5", "17.0"),
    ("Total capital ratio (%)", "20.0", "19.5"),
]

# Two banks that EXIST in config/banks.yaml (UK + US) prove cross-jurisdiction config.
# Both use a comma-thousands locale so the shared en-formatted synthetic grid parses.
_KNOWN_A = "barclays"
_KNOWN_B = "jpmorgan"
_UNKNOWN = "notabank"
_PERIOD = ReportingPeriod(2025, 4)


def _grid(rows: list[tuple[str, ...]], engine: Engine) -> list[RawCell]:
    cells: list[RawCell] = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cells.append(
                RawCell(
                    row_idx=r,
                    col_idx=c,
                    text=text,
                    bbox=BBox(page=4, x0=float(c), y0=float(r), x1=float(c) + 1, y1=float(r) + 1),
                    engine=engine,
                    row_label=None,
                    col_label=None,
                )
            )
    return cells


class _StubEngine:
    def __init__(self, engine: Engine = Engine.DOCLING) -> None:
        self.engine = engine
        self._groups = [_grid(_SYNTHETIC_KM1, engine)]

    def extract_tables(self, pdf_path, pages=None) -> list[list[RawCell]]:
        return self._groups

    def extract(self, pdf_path, pages=None) -> list[RawCell]:
        return [c for g in self._groups for c in g]


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    """Isolate the parquet store + review queue under tmp (mirrors test_pipeline_e2e)."""
    dataset_dir = tmp_path / "dataset"
    queue_dir = tmp_path / "review_queue"
    monkeypatch.setattr(Paths, "DATASET_DIR", dataset_dir)
    monkeypatch.setattr(Paths, "DATASET", dataset_dir / "values.parquet")
    monkeypatch.setattr(Paths, "REVIEW_QUEUE", queue_dir)
    monkeypatch.setattr(
        Paths,
        "ensure",
        classmethod(
            lambda cls: (
                dataset_dir.mkdir(parents=True, exist_ok=True),
                queue_dir.mkdir(parents=True, exist_ok=True),
            )
        ),
    )
    return tmp_path


@pytest.fixture
def cli_caplog(caplog):
    """caplog that captures ``isda_p3.cli`` directly.

    ``setup_logging`` sets ``propagate=False`` on the ``isda_p3`` package logger, so
    caplog's root handler never sees the cli logs. Attach caplog's handler straight to
    the cli logger so the unknown-bank assertion is robust regardless of suite order.
    """
    logger = logging.getLogger("isda_p3.cli")
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.WARNING, logger="isda_p3.cli")
    yield caplog
    logger.removeHandler(caplog.handler)


def _touch(pdf_dir: Path, name: str) -> None:
    pdf_dir.mkdir(parents=True, exist_ok=True)
    (pdf_dir / name).write_bytes(b"%PDF-1.4 stub")  # never parsed; the stub ignores it


# --- the batch: two banks processed, both land in the dataset --------------------


def test_run_all_two_banks_both_processed_and_stored(tmp_store, capsys):
    pdf_dir = tmp_store / "pdfs"
    _touch(pdf_dir, f"{_KNOWN_A}_2025Q4_KM1.pdf")
    _touch(pdf_dir, f"{_KNOWN_B}_2025Q4_KM1.pdf")

    summary = run_all_command(
        template=Template.KM1,
        period=_PERIOD,
        pdf_dir=pdf_dir,
        engine=_StubEngine(),
        store=True,
        tolerances=load_tolerances(),
        weights=load_weights(),
    )

    by_bank = {s.bank_id: s for s in summary}
    assert set(by_bank) == {_KNOWN_A, _KNOWN_B}
    for bank_id in (_KNOWN_A, _KNOWN_B):
        assert by_bank[bank_id].error is None
        assert by_bank[bank_id].n_auto_passed == 7
        assert by_bank[bank_id].n_flagged == 0

    banks_in_store = {rec["bank"] for rec in read_dataset_decimals()}
    assert banks_in_store == {_KNOWN_A, _KNOWN_B}


# --- an unknown bank is logged + summarised; the batch still completes ------------


def test_run_all_unknown_bank_skipped_not_fatal(tmp_store, cli_caplog):
    pdf_dir = tmp_store / "pdfs"
    _touch(pdf_dir, f"{_KNOWN_A}_2025Q4_KM1.pdf")
    _touch(pdf_dir, f"{_UNKNOWN}_2025Q4_KM1.pdf")

    summary = run_all_command(
        template=Template.KM1,
        period=_PERIOD,
        pdf_dir=pdf_dir,
        engine=_StubEngine(),
        store=True,
        tolerances=load_tolerances(),
        weights=load_weights(),
    )

    by_bank = {s.bank_id: s for s in summary}
    # the known bank completed
    assert by_bank[_KNOWN_A].error is None
    assert by_bank[_KNOWN_A].n_auto_passed == 7
    # the unknown bank is summarised with an error, never crashing the run
    assert isinstance(by_bank[_UNKNOWN], BankRunResult)
    assert by_bank[_UNKNOWN].error is not None
    assert by_bank[_UNKNOWN].n_auto_passed == 0
    assert _UNKNOWN in cli_caplog.text

    # only the known bank reached the dataset
    assert {rec["bank"] for rec in read_dataset_decimals()} == {_KNOWN_A}


# --- files for a different sweep (period/template) are skipped, not errored -------


def test_run_all_skips_other_sweeps_and_malformed(tmp_store, caplog):
    pdf_dir = tmp_store / "pdfs"
    _touch(pdf_dir, f"{_KNOWN_A}_2025Q4_KM1.pdf")
    _touch(pdf_dir, f"{_KNOWN_B}_2025Q3_KM1.pdf")  # different period -> not this sweep
    _touch(pdf_dir, f"{_KNOWN_A}_2025Q4_OV1.pdf")  # different template -> not this sweep
    _touch(pdf_dir, "garbage.pdf")  # malformed name -> skipped, not an error

    summary = run_all_command(
        template=Template.KM1,
        period=_PERIOD,
        pdf_dir=pdf_dir,
        engine=_StubEngine(),
        store=True,
        tolerances=load_tolerances(),
        weights=load_weights(),
    )

    # only the one matching file (barclays 2025Q4 KM1) was processed
    assert [s.bank_id for s in summary] == [_KNOWN_A]
    assert summary[0].error is None


# --- one bad PDF isolates to its bank; the batch still completes ------------------


def test_run_all_failing_file_isolated_not_fatal(tmp_store, cli_caplog):
    pdf_dir = tmp_store / "pdfs"
    _touch(pdf_dir, f"{_KNOWN_A}_2025Q4_KM1.pdf")
    _touch(pdf_dir, f"{_KNOWN_B}_2025Q4_KM1.pdf")

    class _ExplodingEngine:
        """Stands in for a corrupt-PDF engine that raises mid-sweep."""

        engine = Engine.DOCLING

        def extract_tables(self, pdf_path, pages=None):
            if _KNOWN_B in str(pdf_path):
                raise RuntimeError("camelot blew up on this PDF")
            return [_grid(_SYNTHETIC_KM1, Engine.DOCLING)]

        def extract(self, pdf_path, pages=None):
            return []

    summary = run_all_command(
        template=Template.KM1,
        period=_PERIOD,
        pdf_dir=pdf_dir,
        engine=_ExplodingEngine(),
        store=True,
        tolerances=load_tolerances(),
        weights=load_weights(),
    )

    by_bank = {s.bank_id: s for s in summary}
    # the healthy bank completed and stored; the exploding one is contained, not fatal
    assert by_bank[_KNOWN_A].error is None and by_bank[_KNOWN_A].n_auto_passed == 7
    assert by_bank[_KNOWN_B].error is not None and by_bank[_KNOWN_B].n_auto_passed == 0
    assert "RuntimeError" in by_bank[_KNOWN_B].error
    assert {rec["bank"] for rec in read_dataset_decimals()} == {_KNOWN_A}


# --- secondary engine factory is honoured per file -------------------------------


def test_run_all_uses_secondary_engine_when_provided(tmp_store):
    pdf_dir = tmp_store / "pdfs"
    _touch(pdf_dir, f"{_KNOWN_A}_2025Q4_KM1.pdf")

    seen: list[Path] = []

    def _secondary_for(path: Path):
        seen.append(path)
        return _StubEngine(Engine.CAMELOT)

    summary = run_all_command(
        template=Template.KM1,
        period=_PERIOD,
        pdf_dir=pdf_dir,
        engine=_StubEngine(),
        store=False,
        secondary_for=_secondary_for,
        tolerances=load_tolerances(),
        weights=load_weights(),
    )

    assert len(seen) == 1  # the secondary builder was invoked for the one file
    assert summary[0].n_auto_passed == 7  # two agreeing engines -> all auto-passed
    assert summary[0].n_flagged == 0
