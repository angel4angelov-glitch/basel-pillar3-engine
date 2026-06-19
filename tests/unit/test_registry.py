"""Tests for isda_p3.ingest.registry (chunk 3.1) — content-addressed registry.

Box-2 audit invariants (TOOLING.md §2 / CLAUDE.md §A): ingest is **idempotent** and
**content-addressed**. The dedup key is the SHA-256 of the bytes, NOT the logical
(bank, period, template) — so a restatement (same bank/period, different bytes) gets a
NEW sha and is retained alongside the old one (immutable, both versions kept). Re-ingesting
identical bytes is a no-op (``skipped_dup``): no duplicate manifest line, no raw rewrite.
The manifest round-trips every ``ManifestRow`` exactly (enums as their string value,
``template=None`` ⇄ ``""``), and urls — which contain commas — survive CSV quoting.
"""

from __future__ import annotations

import hashlib

import pytest

from isda_p3.config import Paths
from isda_p3.ingest.registry import (
    load_manifest,
    register,
    register_file,
)
from isda_p3.models import (
    ManifestRow,
    ReportingPeriod,
    SourceKind,
    Template,
)

_PERIOD = ReportingPeriod(2025, 4)
_FETCHED_AT = "2026-06-19T12:00:00Z"


@pytest.fixture
def tmp_registry(tmp_path, monkeypatch):
    """Point RAW / REGISTRY / MANIFEST at an isolated tmp tree (no real ``data/`` writes)."""
    raw = tmp_path / "raw"
    reg = tmp_path / "registry"
    monkeypatch.setattr(Paths, "RAW", raw)
    monkeypatch.setattr(Paths, "REGISTRY", reg)
    monkeypatch.setattr(Paths, "MANIFEST", reg / "manifest.csv")

    def _ensure(cls):
        raw.mkdir(parents=True, exist_ok=True)
        reg.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(Paths, "ensure", classmethod(_ensure))
    return tmp_path


def _register_pdf(content: bytes, *, url: str = "https://bank.com/p3.pdf") -> ManifestRow:
    return register(
        content,
        bank_id="barclays",
        period=_PERIOD,
        template=Template.KM1,
        url=url,
        source_kind=SourceKind.PDF,
        fetched_at=_FETCHED_AT,
    )


# --- register: first write -----------------------------------------------------


def test_register_writes_row_and_raw_file(tmp_registry):
    content = b"%PDF-1.4 fake km1 page"
    row = _register_pdf(content)

    expected_sha = hashlib.sha256(content).hexdigest()
    assert row.sha256 == expected_sha
    assert row.status == "fetched"

    manifest = load_manifest()
    assert len(manifest) == 1
    assert manifest[0].status == "fetched"
    assert manifest[0].sha256 == expected_sha

    raw_file = Paths.RAW / f"{expected_sha}.pdf"
    assert raw_file.exists()
    assert raw_file.read_bytes() == content
    assert row.local_path == str(raw_file)


# --- register: idempotent on identical bytes -----------------------------------


def test_reregister_same_bytes_is_skipped_dup(tmp_registry):
    content = b"%PDF-1.4 fake km1 page"
    first = _register_pdf(content)
    raw_file = Paths.RAW / f"{first.sha256}.pdf"
    mtime_before = raw_file.stat().st_mtime_ns

    second = _register_pdf(content)
    assert second.status == "skipped_dup"

    manifest = load_manifest()
    assert len(manifest) == 1  # no duplicate line appended
    assert manifest[0].status == "fetched"  # the original row is unchanged
    assert raw_file.stat().st_mtime_ns == mtime_before  # raw file not rewritten


# --- register: restatement (same logical key, different bytes) -----------------


def test_restatement_different_bytes_retains_both(tmp_registry):
    v1 = b"%PDF original km1"
    v2 = b"%PDF restated km1 with corrected figure"
    row1 = _register_pdf(v1)
    row2 = _register_pdf(v2)

    assert row1.sha256 != row2.sha256
    manifest = load_manifest()
    assert len(manifest) == 2  # both versions retained
    assert {r.sha256 for r in manifest} == {row1.sha256, row2.sha256}

    # both raw files exist, with their own bytes
    assert (Paths.RAW / f"{row1.sha256}.pdf").read_bytes() == v1
    assert (Paths.RAW / f"{row2.sha256}.pdf").read_bytes() == v2


# --- manifest round-trips exactly ----------------------------------------------


def test_load_manifest_round_trips_template_none_and_enum(tmp_registry):
    # template=None and an XBRL_CSV source must survive the CSV round-trip exactly.
    register(
        b"col,val\nKM1.5,13.6\n",
        bank_id="deutsche-bank",
        period=ReportingPeriod(2025, None),
        template=None,
        url="https://edap.eu/db.csv",
        source_kind=SourceKind.XBRL_CSV,
        fetched_at=_FETCHED_AT,
    )
    (row,) = load_manifest()
    assert row.template is None
    assert row.source_kind is SourceKind.XBRL_CSV
    assert isinstance(row.source_kind, SourceKind)
    assert row.bank_id == "deutsche-bank"
    assert row.period == "2025FY"
    assert row.fetched_at == _FETCHED_AT


def test_url_with_commas_round_trips(tmp_registry):
    url = "https://bank.com/p3.pdf?cols=a,b,c&period=2025,Q4"
    _register_pdf(b"%PDF", url=url)
    (row,) = load_manifest()
    assert row.url == url  # commas preserved through CSV quoting


# --- ext is driven by source_kind ----------------------------------------------


def test_xbrl_csv_source_kind_yields_csv_local_path(tmp_registry):
    row = register(
        b"col,val\n",
        bank_id="deutsche-bank",
        period=_PERIOD,
        template=Template.KM1,
        url="https://edap.eu/db.csv",
        source_kind=SourceKind.XBRL_CSV,
        fetched_at=_FETCHED_AT,
    )
    assert row.local_path.endswith(f"{row.sha256}.csv")
    assert (Paths.RAW / f"{row.sha256}.csv").exists()


# --- register_file convenience -------------------------------------------------


def test_register_file_reads_bytes_and_delegates(tmp_registry, tmp_path):
    src = tmp_path / "download.pdf"
    content = b"%PDF from disk"
    src.write_bytes(content)

    row = register_file(
        src,
        bank_id="barclays",
        period=_PERIOD,
        template=Template.KM1,
        url="https://bank.com/p3.pdf",
        source_kind=SourceKind.PDF,
        fetched_at=_FETCHED_AT,
    )
    assert row.sha256 == hashlib.sha256(content).hexdigest()
    assert row.status == "fetched"
    assert (Paths.RAW / f"{row.sha256}.pdf").read_bytes() == content


# --- malformed manifest fails loud ---------------------------------------------


def test_malformed_manifest_line_raises(tmp_registry):
    _register_pdf(b"%PDF")
    # Corrupt the source_kind of the persisted row → load must fail loudly, not drop it.
    path = Paths.MANIFEST
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("PDF", "NOT_A_KIND"), encoding="utf-8")
    with pytest.raises(ValueError):
        load_manifest()
