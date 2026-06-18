"""Adaptive second-engine selector (chunk 2.1, audit M2).

Picks the second deterministic engine *per PDF* (TOOLING.md Decisions): prefer
Camelot **lattice** (needs ruled lines + Ghostscript) → Camelot **stream** →
**pdfplumber**. Most KM1/OV1 tables are borderless, so lattice often returns
nothing; and the 0.5 audit found Ghostscript is not installed here, so lattice is
**skipped cleanly** (logged) when ``gs`` is absent rather than failing.

Selection probes how many tables each Camelot flavor yields and returns an engine
pinned to the first flavor that finds any; if neither does, it falls back to
pdfplumber. The probe and the Ghostscript check are module-level functions so
tests can stub them — no real PDF or ``gs`` needed.
"""

from __future__ import annotations

import logging
from pathlib import Path

from isda_p3.extraction.camelot_engine import CamelotEngine, ghostscript_available
from isda_p3.extraction.docling_engine import ExtractionError
from isda_p3.extraction.engine import ExtractionEngine
from isda_p3.extraction.pdfplumber_bbox import PdfplumberEngine

logger = logging.getLogger(__name__)


def _camelot_table_count(pdf_path: Path, flavor: str) -> int:
    """Number of tables Camelot extracts from the first page with ``flavor``.

    Probed on page 1 only — enough to decide whether a flavor is viable for this
    PDF without parsing the whole document.
    """
    import camelot

    return len(camelot.read_pdf(str(pdf_path), pages="1", flavor=flavor))


def select_secondary_engine(pdf_path: Path) -> ExtractionEngine:
    """Choose lattice → stream → pdfplumber for ``pdf_path`` and return the engine.

    Raises :class:`ExtractionError` if the file does not exist (an unreadable path
    must surface, not silently fall through to a fallback engine).
    """
    if not pdf_path.exists():
        raise ExtractionError(f"PDF not found: {pdf_path}")

    if ghostscript_available():
        if _camelot_table_count(pdf_path, "lattice") > 0:
            logger.info("Secondary engine: Camelot lattice for %s", pdf_path)
            return CamelotEngine(flavor="lattice")
        logger.info("Camelot lattice found no tables in %s; trying stream", pdf_path)
    else:
        logger.info("Ghostscript not installed; skipping Camelot lattice for %s", pdf_path)

    if _camelot_table_count(pdf_path, "stream") > 0:
        logger.info("Secondary engine: Camelot stream for %s", pdf_path)
        return CamelotEngine(flavor="stream")

    logger.info("Camelot found no tables in %s; falling back to pdfplumber", pdf_path)
    return PdfplumberEngine()
