"""pdfplumber extraction engine — third deterministic engine (chunk 2.1, box 3).

pdfplumber is the borderless-table fallback when Camelot finds nothing (most
KM1/OV1 tables have no ruled lines, where lattice returns empty — see audit M2).

**Coordinate convention.** pdfplumber's native ``top``/``bottom`` already measure
distance from the **page top** (top-left origin, y increasing downward) — exactly
the frame Docling and the y-flipped Camelot engine use — so **no flip** is applied
here. Per-cell bbox comes from ``table.rows[i].cells[j]``, a ``(x0, top, x1,
bottom)`` 4-tuple (``None`` where a ragged grid has no cell); the matching cell
text comes from ``table.extract()`` (a list-of-rows-of-strings aligned to
``rows``×``cells``). ``text`` is carried through verbatim — the extractor is never
the source of a number (CLAUDE.md §A.1).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Sequence

from isda_p3.extraction.docling_engine import ExtractionError
from isda_p3.models import BBox, Engine, RawCell


def cells_from_pdfplumber_table(table: Any, page_no: int) -> list[RawCell]:
    """Transform one pdfplumber table into ``RawCell``s — the testable, model-free core.

    Duck-typed: ``table`` exposes ``.rows`` (each row a ``.cells`` list of
    ``(x0, top, x1, bottom)`` tuples or ``None``) and ``.extract()`` returning the
    text grid aligned to ``rows``×``cells``. Coordinates are top-left already (no
    flip). A ``None`` cell is a structural gap in a ragged grid (no cell present),
    so it is skipped — not a dropped value. A *present* cell with a missing or
    non-finite coordinate raises ``ValueError`` (§A.2). Labels are left ``None``
    (a mapping concern, box 4).
    """
    text_grid = table.extract()
    cells: list[RawCell] = []
    for row_idx, row in enumerate(table.rows):
        for col_idx, cell_bbox in enumerate(row.cells):
            if cell_bbox is None:
                continue  # ragged grid: no cell at this position
            coords = tuple(cell_bbox)
            if len(coords) != 4 or any(c is None or not math.isfinite(c) for c in coords):
                raise ValueError(
                    f"pdfplumber cell at row {row_idx}, col {col_idx} has a missing or "
                    f"non-finite bbox {coords!r}; refusing to emit unusable provenance."
                )
            x0, top, x1, bottom = (float(c) for c in coords)
            text = text_grid[row_idx][col_idx]
            cells.append(
                RawCell(
                    row_idx=row_idx,
                    col_idx=col_idx,
                    text=text if text is not None else "",
                    bbox=BBox(page=page_no, x0=x0, y0=top, x1=x1, y1=bottom),
                    engine=Engine.PDFPLUMBER,
                    row_label=None,
                    col_label=None,
                )
            )
    return cells


class PdfplumberEngine:
    """:class:`~isda_p3.extraction.engine.ExtractionEngine` backed by pdfplumber."""

    engine = Engine.PDFPLUMBER

    def extract_tables(
        self, pdf_path: Path, pages: Sequence[int] | None = None
    ) -> list[list[RawCell]]:
        """Extract cells grouped per table (row/col indices local to each table).

        A missing file or a PDF with no detectable tables raises
        :class:`ExtractionError` rather than a silent empty result (§A.2).
        ``pages`` (1-based) restricts which pages are scanned; ``None`` = all.
        """
        if not pdf_path.exists():
            raise ExtractionError(f"PDF not found: {pdf_path}")

        import pdfplumber

        wanted = set(pages) if pages is not None else None
        groups: list[list[RawCell]] = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                if wanted is not None and page.page_number not in wanted:
                    continue
                for table in page.find_tables():
                    groups.append(cells_from_pdfplumber_table(table, page.page_number))

        if not groups:
            where = f" on pages {sorted(wanted)}" if wanted is not None else ""
            raise ExtractionError(f"pdfplumber found no tables in {pdf_path}{where}")
        return groups

    def extract(self, pdf_path: Path, pages: Sequence[int] | None = None) -> list[RawCell]:
        """Flat view of :meth:`extract_tables` — every table's cells concatenated."""
        return [cell for group in self.extract_tables(pdf_path, pages) for cell in group]
