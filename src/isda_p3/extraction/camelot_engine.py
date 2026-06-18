"""Camelot extraction engine — the second deterministic engine (chunk 2.1, box 3).

Camelot is the independent cross-check for Docling (TOOLING.md §1b D3): two
engines that segment the same PDF differently, so 2.2 can compare their numbers.
For that comparison to be meaningful the bboxes must share a coordinate frame.

**Coordinate convention (the load-bearing rule).** Chunk 1.1 stored Docling
bbox as **TOP-LEFT origin**, PDF points: ``x0``=left, ``y0``=top, ``x1``=right,
``y1``=bottom, y increasing *downward*. Camelot inherits PDFMiner's **BOTTOM-LEFT**
origin: a camelot ``Cell`` carries ``x1,y1`` (left-**bottom**) and ``x2,y2``
(right-**top**), with y increasing *upward* from the page bottom. To match Docling
we y-flip using the page height ``H`` (``table.pdf_size[1]``)::

    x0 = cell.x1                 # left   — x needs no flip
    x1 = cell.x2                 # right
    y0 = H - cell.y2            # top edge:    bottom-left top-y → distance from page top
    y1 = H - cell.y1            # bottom edge: bottom-left bottom-y → distance from page top

A cell whose visual top sits near the page top has ``cell.y2 ≈ H`` ⇒ ``y0 ≈ 0``
(top-left), as required. ``text`` is carried through verbatim and unparsed — the
extractor is never the source of a number (CLAUDE.md §A.1).

``CamelotEngine`` is internally adaptive: ``flavor="lattice"`` needs ruled lines
*and* Ghostscript, so it is tried first only when ``gs`` is on PATH, then
``flavor="stream"``. A pinned ``flavor`` (set by :mod:`isda_p3.extraction.secondary`)
overrides the adaptive probe.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path
from typing import Any, Sequence

from isda_p3.extraction.docling_engine import ExtractionError
from isda_p3.models import BBox, Engine, RawCell


def ghostscript_available() -> bool:
    """True iff the ``gs`` binary is on PATH (Camelot lattice needs it).

    The 0.5 environment audit found Ghostscript is not installed here, so lattice
    is skipped cleanly in that case (see :mod:`isda_p3.extraction.secondary`).
    """
    return shutil.which("gs") is not None


def _require_finite(value: Any, label: str) -> float:
    """Return ``value`` as a finite float or raise — never fabricate provenance (§A.2)."""
    if value is None or not math.isfinite(value):
        raise ValueError(
            f"Camelot cell has missing or non-finite {label} ({value!r}); refusing "
            "to emit a RawCell with unusable bbox provenance."
        )
    return float(value)


def cells_from_camelot_table(table: Any, page_height: float) -> list[RawCell]:
    """Transform one camelot table into ``RawCell``s — the testable, model-free core.

    Duck-typed: ``table`` exposes ``.cells`` (a list of rows, each a list of cells)
    and ``.page``; each cell exposes ``x1,y1,x2,y2`` (PDFMiner bottom-left) and
    ``text``. ``page_height`` is the page height in points (``table.pdf_size[1]``),
    used for the bottom-left → top-left y-flip documented in the module docstring.

    Row/column indices are the cell's position in ``table.cells``; ``text`` is
    verbatim (§A.1); labels are left ``None`` (a mapping concern, box 4). Any
    missing or non-finite coordinate (or page height) raises ``ValueError`` rather
    than emitting fabricated provenance (§A.2).
    """
    height = _require_finite(page_height, "page_height")
    cells: list[RawCell] = []
    for row_idx, row in enumerate(table.cells):
        for col_idx, cell in enumerate(row):
            x0 = _require_finite(cell.x1, "x1")
            x1 = _require_finite(cell.x2, "x2")
            y_bottom = _require_finite(cell.y1, "y1")
            y_top = _require_finite(cell.y2, "y2")
            cells.append(
                RawCell(
                    row_idx=row_idx,
                    col_idx=col_idx,
                    # camelot's Cell.text is a joined string; coerce a None double to
                    # "" so the RawCell.text:str contract holds (matches pdfplumber).
                    text=cell.text if cell.text is not None else "",
                    bbox=BBox(
                        page=table.page,
                        x0=x0,
                        y0=height - y_top,  # top edge, flipped to top-left
                        x1=x1,
                        y1=height - y_bottom,  # bottom edge, flipped to top-left
                    ),
                    engine=Engine.CAMELOT,
                    row_label=None,
                    col_label=None,
                )
            )
    return cells


class CamelotEngine:
    """:class:`~isda_p3.extraction.engine.ExtractionEngine` backed by Camelot.

    ``flavor`` pins the parser flavor; ``None`` means adaptive — lattice first
    (only if Ghostscript is available), then stream.
    """

    engine = Engine.CAMELOT

    def __init__(self, flavor: str | None = None) -> None:
        self.flavor = flavor

    def _flavors(self) -> list[str]:
        if self.flavor is not None:
            return [self.flavor]
        return ["lattice", "stream"] if ghostscript_available() else ["stream"]

    def extract_tables(
        self, pdf_path: Path, pages: Sequence[int] | None = None
    ) -> list[list[RawCell]]:
        """Extract cells grouped per table (row/col indices local to each table).

        Tries each candidate flavor in turn and returns the first that yields
        tables. A missing file or a PDF that no flavor can parse raises
        :class:`ExtractionError` rather than returning a silent empty result (§A.2).
        """
        if not pdf_path.exists():
            raise ExtractionError(f"PDF not found: {pdf_path}")

        import camelot

        pages_arg = "all" if pages is None else ",".join(str(p) for p in pages)
        flavors = self._flavors()
        for flavor in flavors:
            tables = camelot.read_pdf(str(pdf_path), pages=pages_arg, flavor=flavor)
            if len(tables) == 0:
                continue
            return [
                cells_from_camelot_table(table, page_height=table.pdf_size[1])
                for table in tables
            ]

        raise ExtractionError(
            f"Camelot found no tables in {pdf_path} (flavors tried: {flavors})"
        )

    def extract(self, pdf_path: Path, pages: Sequence[int] | None = None) -> list[RawCell]:
        """Flat view of :meth:`extract_tables` — every table's cells concatenated."""
        return [cell for group in self.extract_tables(pdf_path, pages) for cell in group]
