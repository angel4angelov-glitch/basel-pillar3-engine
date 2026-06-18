"""Docling/TableFormer extraction engine (chunk 1.1, TOOLING.md box 3).

Docling 2.x real API (verified against installed ``docling==2.103.0`` /
``docling-core``; do not guess these):

* ``DocumentConverter().convert(source).document`` -> ``DoclingDocument``.
* ``document.tables`` -> ``list[TableItem]``.
* ``TableItem.prov`` -> ``list[ProvenanceItem]``; each carries ``page_no``
  (1-based) and a page ``bbox``. We take the table's page from ``prov[0]``.
* ``TableItem.data.table_cells`` -> ``list[TableCell]``; each cell exposes
  ``start_row_offset_idx`` / ``start_col_offset_idx`` (the cell's top-left grid
  offsets), verbatim ``text``, and an optional ``bbox``.
* The cell ``bbox`` is a docling-core ``BoundingBox`` with ``l, t, r, b`` and a
  ``coord_origin`` that defaults to ``TOPLEFT``.

**Coordinate convention adopted here (state it for M2 two-engine alignment):**
we store the cell bbox verbatim in Docling's native page space -- ``x0=l``,
``y0=t``, ``x1=r``, ``y1=b``, **top-left origin**, units = PDF points (1/72").
y increases downward. Camelot/pdfplumber use a *bottom-left* origin, so M2 must
flip the y-axis (``y' = page_height - y``) before comparing boxes across engines.
No transform is applied here -- the raw Docling coordinates are preserved so the
provenance is exactly what the model reported.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Sequence

from isda_p3.models import BBox, Engine, RawCell


class ExtractionError(RuntimeError):
    """A parse failure that must surface, not be hidden behind an empty result."""


def _coords(bbox: Any) -> tuple[float, float, float, float] | None:
    """Pull ``(x0, y0, x1, y1)`` from a Docling-style (l/t/r/b) or fake (x0..y1) bbox.

    Returns ``None`` (⇒ caller raises) for a missing bbox *or* one whose
    coordinates are not all present and finite — fabricating null/NaN provenance
    would be worse than failing (§A.2).
    """
    if bbox is None:
        return None
    if all(hasattr(bbox, a) for a in ("l", "t", "r", "b")):
        coords = (bbox.l, bbox.t, bbox.r, bbox.b)
    elif all(hasattr(bbox, a) for a in ("x0", "y0", "x1", "y1")):
        coords = (bbox.x0, bbox.y0, bbox.x1, bbox.y1)
    else:
        return None
    if any(c is None or not math.isfinite(c) for c in coords):
        return None
    return coords


def cells_from_docling_table(table: Any, page_no: int) -> list[RawCell]:
    """Transform one Docling table into ``RawCell``s -- the model-free, testable core.

    Duck-typed: ``table`` need only expose ``.data.table_cells``, each cell exposing
    ``start_row_offset_idx``, ``start_col_offset_idx``, ``text`` and a ``bbox`` (with
    ``l/t/r/b`` or ``x0..y1``). ``text`` is carried through **verbatim and unparsed**
    (CLAUDE.md §A.1 -- the extractor is never the source of a number). Row/column
    labels are left ``None``; assigning them is a mapping concern (box 4).

    An empty table legitimately returns ``[]``. A cell with no bbox raises
    ``ValueError`` rather than fabricating provenance (§A.2).
    """
    cells: list[RawCell] = []
    for cell in table.data.table_cells:
        coords = _coords(cell.bbox)
        if coords is None:
            raise ValueError(
                f"Docling cell at row {cell.start_row_offset_idx}, "
                f"col {cell.start_col_offset_idx} has no usable bbox; refusing to "
                "emit a RawCell with missing or non-finite provenance."
            )
        x0, y0, x1, y1 = coords
        cells.append(
            RawCell(
                row_idx=cell.start_row_offset_idx,
                col_idx=cell.start_col_offset_idx,
                text=cell.text,
                bbox=BBox(page=page_no, x0=x0, y0=y0, x1=x1, y1=y1),
                engine=Engine.DOCLING,
                row_label=None,
                col_label=None,
            )
        )
    return cells


class DoclingEngine:
    """:class:`~isda_p3.extraction.engine.ExtractionEngine` backed by Docling/TableFormer."""

    engine = Engine.DOCLING

    def __init__(self) -> None:
        self._converter = None  # lazily built; the model/weights load on first use

    def _get_converter(self) -> Any:
        if self._converter is None:
            from docling.document_converter import DocumentConverter

            self._converter = DocumentConverter()
        return self._converter

    def extract(self, pdf_path: Path, pages: Sequence[int] | None = None) -> list[RawCell]:
        if not pdf_path.exists():
            raise ExtractionError(f"PDF not found: {pdf_path}")

        result = self._get_converter().convert(str(pdf_path))
        tables = result.document.tables
        if not tables:
            raise ExtractionError(f"Docling found no tables in {pdf_path}")

        wanted = set(pages) if pages is not None else None
        cells: list[RawCell] = []
        for table in tables:
            if not table.prov:
                raise ExtractionError(f"Docling table without provenance in {pdf_path}")
            page_no = table.prov[0].page_no
            if wanted is not None and page_no not in wanted:
                continue
            cells.extend(cells_from_docling_table(table, page_no))

        # A non-empty PDF whose tables all fall outside the requested pages must not
        # masquerade as a clean empty extraction (silent-failure guard, §A.2).
        if wanted is not None and not cells:
            raise ExtractionError(
                f"Docling found tables in {pdf_path} but none on pages {sorted(wanted)}"
            )
        return cells
