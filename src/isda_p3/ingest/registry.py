"""Box-2 content-addressed, idempotent document registry (chunk 3.1).

Every ingested document is stored under ``data/raw/<sha256>.<ext>`` (immutable) and
recorded as one :class:`~isda_p3.models.ManifestRow` in ``data/registry/manifest.csv``
(the audit ledger). The dedup key is the **content hash**, never the logical
(bank, period, template): re-ingesting identical bytes is a no-op (``skipped_dup`` — no
duplicate line, no raw rewrite), while a *restatement* (same bank/period, different bytes)
hashes to a new sha and is retained alongside the original. Both versions survive — nothing
is overwritten (CLAUDE.md §A: immutable, auditable, idempotent).

The manifest is a CSV (``csv`` module, fully quoted — urls contain commas). Enums serialise
as their string ``value``; ``template=None`` ⇄ ``""``; a malformed line raises a clear error
rather than being silently dropped (CLAUDE.md §A: no silent failures).
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from ..config import Paths
from ..models import ManifestRow, ReportingPeriod, SourceKind, Template

# Column order of the manifest CSV. A header row makes the ledger self-describing
# and the loader order-independent.
_FIELDS = (
    "bank_id",
    "period",
    "template",
    "url",
    "sha256",
    "source_kind",
    "local_path",
    "status",
    "fetched_at",
)


def _sha256(content: bytes) -> str:
    """SHA-256 hex digest of the raw bytes — the content address / dedup key."""
    return hashlib.sha256(content).hexdigest()


def _ext_for(source_kind: SourceKind, url: str) -> str:
    """File extension for a stored document, driven by ``source_kind``.

    PDF → ``.pdf``, XBRL_CSV → ``.csv``. Any other (defensively — the enum is currently
    exhaustive) falls back to the url's path suffix so the stored file keeps a meaningful
    extension rather than a silent default.
    """
    if source_kind == SourceKind.PDF:
        return ".pdf"
    if source_kind == SourceKind.XBRL_CSV:
        return ".csv"
    return Path(urlparse(url).path).suffix or ".bin"


# --- manifest (de)serialisation ------------------------------------------------


def _row_to_record(row: ManifestRow) -> dict[str, str]:
    """``ManifestRow`` → CSV dict; enums as ``value``, ``template=None`` → ``""``."""
    return {
        "bank_id": row.bank_id,
        "period": row.period,
        "template": row.template.value if row.template is not None else "",
        "url": row.url,
        "sha256": row.sha256,
        "source_kind": row.source_kind.value,
        "local_path": row.local_path,
        "status": row.status,
        "fetched_at": row.fetched_at,
    }


def _record_to_row(record: dict[str, str], *, line_no: int) -> ManifestRow:
    """Inverse of :func:`_row_to_record`. A malformed cell raises a clear error.

    ``line_no`` is the 1-based data-row index (header excluded), surfaced in the error so a
    corrupt ledger can be located — never silently skipped (CLAUDE.md §A).
    """
    try:
        template_raw = record["template"]
        # Validate the period round-trips: a malformed period must fail HERE (with a line
        # number) rather than far downstream at ReportingPeriod.parse(row.period).
        ReportingPeriod.parse(record["period"])
        return ManifestRow(
            bank_id=record["bank_id"],
            period=record["period"],
            template=Template(template_raw) if template_raw else None,
            url=record["url"],
            sha256=record["sha256"],
            source_kind=SourceKind(record["source_kind"]),
            local_path=record["local_path"],
            status=record["status"],
            fetched_at=record["fetched_at"],
        )
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"malformed manifest row at data line {line_no} ({Paths.MANIFEST}): {exc}"
        ) from exc


def load_manifest() -> list[ManifestRow]:
    """Read the manifest CSV → ``ManifestRow``s. Returns ``[]`` if it does not exist yet."""
    path = Paths.MANIFEST
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return [
            _record_to_row(record, line_no=i) for i, record in enumerate(reader, start=1)
        ]


def _write_manifest(rows: list[ManifestRow]) -> None:
    """Atomically overwrite the manifest with ``rows`` (header + one fully-quoted line each).

    Writes a sibling temp file then ``os.replace``s it in, so an interrupted or failing
    write can never truncate the audit ledger to a partial/empty file (CLAUDE.md §A — no
    silent data loss; same guard as ``review/queue.py``).
    """
    Paths.ensure()
    path = Paths.MANIFEST
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=_FIELDS, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            for row in rows:
                writer.writerow(_row_to_record(row))
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def _append_manifest(row: ManifestRow) -> None:
    """Append one row, writing the header first if the manifest is empty.

    The header is written when the file is empty *at open time* (``fh.tell() == 0``) rather
    than via a separate ``exists()`` check — atomic with the open, so it can't double-write.
    """
    Paths.ensure()
    with Paths.MANIFEST.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_FIELDS, quoting=csv.QUOTE_ALL)
        if fh.tell() == 0:
            writer.writeheader()
        writer.writerow(_row_to_record(row))


# --- register ------------------------------------------------------------------


def register(
    content: bytes,
    *,
    bank_id: str,
    period: ReportingPeriod,
    template: Template | None,
    url: str,
    source_kind: SourceKind,
    fetched_at: str,
) -> ManifestRow:
    """Store ``content`` content-addressed and record it in the manifest (idempotent).

    Returns the persisted :class:`ManifestRow` (``status="fetched"``). If the content's
    sha already exists in the manifest, NOTHING is written — the raw file is not rewritten,
    no duplicate line is appended — and a row with ``status="skipped_dup"`` is returned.
    Dedup is by content hash, so a restatement (same bank/period, different bytes) yields a
    new sha and a new row; both versions are retained.
    """
    sha = _sha256(content)
    ext = _ext_for(source_kind, url)
    local_path = Paths.RAW / f"{sha}{ext}"

    row = ManifestRow(
        bank_id=bank_id,
        period=period.label,
        template=template,
        url=url,
        sha256=sha,
        source_kind=source_kind,
        local_path=str(local_path),
        status="fetched",
        fetched_at=fetched_at,
    )

    if any(existing.sha256 == sha for existing in load_manifest()):
        # Already ingested: do not rewrite the raw file or append a duplicate line.
        return replace(row, status="skipped_dup")

    Paths.ensure()
    # Immutable write: if the file somehow exists with this sha, its bytes are identical
    # (content-addressed), so re-writing is a harmless no-op.
    local_path.write_bytes(content)
    _append_manifest(row)
    return row


def register_file(
    path: Path,
    *,
    bank_id: str,
    period: ReportingPeriod,
    template: Template | None,
    url: str,
    source_kind: SourceKind,
    fetched_at: str,
) -> ManifestRow:
    """Read ``path`` and delegate to :func:`register` (convenience for on-disk inputs)."""
    return register(
        path.read_bytes(),
        bank_id=bank_id,
        period=period,
        template=template,
        url=url,
        source_kind=source_kind,
        fetched_at=fetched_at,
    )
