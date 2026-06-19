"""Box-6 human-review queue (chunk 2.4) — triage low-confidence figures.

A FLAGGED :class:`~isda_p3.models.ReconciliationResult` must be adjudicable by a
human WITHOUT reopening the PDF (TOOLING.md box 6): the value sits beside its
verbatim source cell (``raw_text``), both engine values are shown when they
disagree, and the checks that fired are listed. The audit invariants (CLAUDE.md §A):
``Decimal`` values serialise as **strings** (never JSON floats) and parse back to
exact ``Decimal``; AUTO_PASSED results never enter the queue.

Storage is one JSON object per line under ``data/review_queue/<run_id>.jsonl`` —
append-only on write, read-modify-write on :func:`resolve`.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from ..config import Paths
from ..models import (
    BBox,
    Bank,
    CheckOutcome,
    CheckType,
    Engine,
    ReconciliationResult,
    Template,
    Unit,
    ValidationStatus,
)

# --- value object --------------------------------------------------------------


@dataclass(frozen=True)
class ReviewItem:
    """Everything a human needs to adjudicate a flagged figure (no PDF reopen).

    ``status`` starts :attr:`ValidationStatus.FLAGGED`; :func:`resolve` produces a
    new item with ``HUMAN_CONFIRMED``/``HUMAN_CORRECTED``. ``checks`` are flattened
    to ``(check_type, outcome, detail)`` triples — the verdict, not the engine
    internals. ``note`` records a human's free-text adjudication comment.
    """

    bank: str
    period: str
    template: Template
    field_code: str
    value: Decimal
    unit: Unit
    raw_text: str  # verbatim source cell, e.g. "13.6¹"
    engine_values: dict[Engine, Decimal]
    source_url: str
    page: int | None
    bbox: BBox | None
    confidence: Decimal
    checks: tuple[tuple[CheckType, CheckOutcome, str], ...]
    status: ValidationStatus
    run_id: str
    note: str = ""


# --- (de)serialisation ---------------------------------------------------------


def _decimal_from_str(raw: object, field: str) -> Decimal:
    """Parse a Decimal from its STRING form, refusing a JSON float.

    A hand-edited queue line with a bare number (``"value": 13.6``) deserialises to
    a Python ``float``; ``Decimal(13.6)`` would then silently store
    ``13.5999999999999996...`` — the exact audit corruption the string invariant
    exists to prevent (CLAUDE.md §A). So a non-string is a hard error, never coerced.
    """
    if not isinstance(raw, str):
        raise ValueError(
            f"review queue {field!r} must be a JSON string (Decimal), got {type(raw).__name__}: "
            f"{raw!r} — refusing to coerce a float."
        )
    return Decimal(raw)


def _bbox_to_dict(bbox: BBox | None) -> dict | None:
    if bbox is None:
        return None
    return {"page": bbox.page, "x0": bbox.x0, "y0": bbox.y0, "x1": bbox.x1, "y1": bbox.y1}


def _bbox_from_dict(d: dict | None) -> BBox | None:
    if d is None:
        return None
    return BBox(page=d["page"], x0=d["x0"], y0=d["y0"], x1=d["x1"], y1=d["y1"])


def _item_to_dict(item: ReviewItem) -> dict:
    """JSON-ready dict — every Decimal is a STRING so no float ever touches disk."""
    return {
        "bank": item.bank,
        "period": item.period,
        "template": item.template.value,
        "field_code": item.field_code,
        "value": str(item.value),
        "unit": item.unit.value,
        "raw_text": item.raw_text,
        "engine_values": {e.value: str(v) for e, v in item.engine_values.items()},
        "source_url": item.source_url,
        "page": item.page,
        "bbox": _bbox_to_dict(item.bbox),
        "confidence": str(item.confidence),
        "checks": [[ct.value, oc.value, detail] for ct, oc, detail in item.checks],
        "status": item.status.value,
        "run_id": item.run_id,
        "note": item.note,
    }


def _item_from_dict(d: dict) -> ReviewItem:
    """Inverse of :func:`_item_to_dict`; strings parse back to exact ``Decimal``."""
    return ReviewItem(
        bank=d["bank"],
        period=d["period"],
        template=Template(d["template"]),
        field_code=d["field_code"],
        value=_decimal_from_str(d["value"], "value"),
        unit=Unit(d["unit"]),
        raw_text=d["raw_text"],
        engine_values={
            Engine(e): _decimal_from_str(v, f"engine_values[{e}]")
            for e, v in d["engine_values"].items()
        },
        source_url=d["source_url"],
        page=d["page"],
        bbox=_bbox_from_dict(d["bbox"]),
        confidence=_decimal_from_str(d["confidence"], "confidence"),
        checks=tuple((CheckType(ct), CheckOutcome(oc), detail) for ct, oc, detail in d["checks"]),
        status=ValidationStatus(d["status"]),
        run_id=d["run_id"],
        note=d.get("note", ""),
    )


def _result_to_item(result: ReconciliationResult, *, bank: Bank, run_id: str) -> ReviewItem:
    fv = result.field_value
    prov = fv.provenance
    bbox = prov.bbox
    return ReviewItem(
        bank=bank.id,
        period=prov.period.label,
        template=fv.template,
        field_code=fv.field_code,
        value=fv.value,
        unit=fv.unit,
        raw_text=fv.raw_text,
        engine_values=dict(fv.engine_values),
        source_url=prov.source_url,
        page=bbox.page if bbox is not None else None,
        bbox=bbox,
        confidence=result.confidence,
        checks=tuple((c.check_type, c.outcome, c.detail) for c in result.checks),
        status=ValidationStatus.FLAGGED,
        run_id=run_id,
    )


# --- enqueue / load ------------------------------------------------------------


def enqueue_flagged(
    results: Sequence[ReconciliationResult], *, bank: Bank, run_id: str
) -> int:
    """Append every FLAGGED result to ``<run_id>.jsonl``; AUTO_PASSED is skipped.

    Returns the number of items written. Decimals are serialised as strings;
    ``ensure_ascii=False`` keeps footnote superscripts (e.g. ``¹``) human-readable
    for audit. A run with no flagged results writes nothing (returns 0).
    """
    flagged = [r for r in results if r.status is ValidationStatus.FLAGGED]
    if not flagged:
        return 0
    Paths.ensure()
    path = Paths.REVIEW_QUEUE / f"{run_id}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for result in flagged:
            item = _result_to_item(result, bank=bank, run_id=run_id)
            fh.write(json.dumps(_item_to_dict(item), ensure_ascii=False) + "\n")
    return len(flagged)


def load_queue() -> list[ReviewItem]:
    """Read every ``*.jsonl`` in the queue dir → ``ReviewItem``s, newest run first.

    Ordering is by file mtime (descending) so the most recent run's items lead.
    Returns ``[]`` when the queue dir is absent (nothing flagged yet).
    """
    if not Paths.REVIEW_QUEUE.exists():
        return []
    files = sorted(
        Paths.REVIEW_QUEUE.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    items: list[ReviewItem] = []
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                items.append(_item_from_dict(json.loads(line)))
    return items


# --- render --------------------------------------------------------------------


def _render_engines(item: ReviewItem) -> str:
    if not item.engine_values:
        return "(none)"
    body = "  ".join(f"{e.value}={v}" for e, v in item.engine_values.items())
    if len(set(item.engine_values.values())) > 1:
        body += "   ← disagreement"
    return body


def _render_checks(item: ReviewItem) -> str:
    if not item.checks:
        return "NO_CHECKS"
    parts = []
    for ct, oc, detail in item.checks:
        token = f"{ct.value} {oc.value}"
        if detail:
            token += f" ({detail})"
        parts.append(token)
    return "; ".join(parts)


def _render_source(item: ReviewItem) -> str:
    page = f"p{item.page}" if item.page is not None else "pNA"
    if item.bbox is None:
        bbox = "bbox(NA)"
    else:
        b = item.bbox
        bbox = f"bbox({b.x0:g},{b.y0:g},{b.x1:g},{b.y1:g})"
    return f"{item.source_url}  {page}  {bbox}"


def render_item(item: ReviewItem) -> str:
    """The value-beside-source-cell view for one queued item (TOOLING.md box 6)."""
    return "\n".join(
        [
            f"{item.status.value}  {item.bank} {item.period}  {item.field_code}",
            f'  extracted: {item.value} {item.unit.value}   (raw cell: "{item.raw_text}")',
            f"  engines:   {_render_engines(item)}",
            f"  checks:    {_render_checks(item)}",
            f"  source:    {_render_source(item)}",
            f"  confidence: {item.confidence}",
            f"  resolve:   isda-p3 review resolve --run {item.run_id} --field {item.field_code}",
        ]
    )


# --- resolve -------------------------------------------------------------------


def resolve(
    run_id: str,
    field_code: str,
    *,
    action: Literal["confirm", "correct"],
    corrected_value: Decimal | None = None,
    note: str = "",
) -> ReviewItem:
    """Adjudicate one queued item in place and return the updated :class:`ReviewItem`.

    ``confirm`` → :attr:`ValidationStatus.HUMAN_CONFIRMED` (value unchanged).
    ``correct`` → :attr:`ValidationStatus.HUMAN_CORRECTED` with ``value`` set to
    ``corrected_value`` (which is required — a correction with no number is a hard
    error, never a silent no-op, CLAUDE.md §A). The run's jsonl is rewritten line by
    line: only the matched line changes, the rest are byte-preserved. Raises
    ``FileNotFoundError`` if the run has no queue file and ``KeyError`` if the field
    is not in it.

    TODO (chunk 1.7 follow-up): re-sync the resolved value/status back into
    ``values.parquet``. Deferred deliberately — the dataset still holds the original
    FLAGGED row; a safe (atomic, non-clobbering) rewrite of the parquet store is a
    separate piece of work, not done here.
    """
    if action not in ("confirm", "correct"):
        raise ValueError(f"unknown action {action!r} (expected 'confirm' or 'correct')")
    if action == "correct" and corrected_value is None:
        raise ValueError("action 'correct' requires a Decimal corrected_value")

    path = Paths.REVIEW_QUEUE / f"{run_id}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no review queue file for run {run_id!r} at {path}")

    updated: ReviewItem | None = None
    out_lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if updated is None and record["field_code"] == field_code:
            item = _item_from_dict(record)
            if action == "confirm":
                updated = replace(item, status=ValidationStatus.HUMAN_CONFIRMED, note=note)
            else:  # correct
                assert corrected_value is not None  # guarded above; narrows for the type checker
                updated = replace(
                    item,
                    status=ValidationStatus.HUMAN_CORRECTED,
                    value=corrected_value,
                    note=note,
                )
            out_lines.append(json.dumps(_item_to_dict(updated), ensure_ascii=False))
        else:
            out_lines.append(line)  # byte-preserve unrelated lines

    if updated is None:
        raise KeyError(
            f"(run_id={run_id!r}, field_code={field_code!r}) not found in review queue"
        )
    # Atomic replace: write a sibling temp file then os.replace, so an interrupted
    # write can never truncate the audit queue (a partial overwrite would lose the
    # other flagged rows in this run — CLAUDE.md §A, no silent data loss).
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out_lines) + "\n")
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise
    return updated
