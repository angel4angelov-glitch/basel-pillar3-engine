"""Extraction-engine protocol (chunk 1.1, TOOLING.md box 3).

A deterministic table extractor turns a PDF into verbatim :class:`RawCell`s with
per-cell ``bbox`` provenance. The LLM is never an extractor (CLAUDE.md §A.1):
every engine here emits source strings only, never parsed numbers. Concrete
engines (Docling, Camelot, pdfplumber) implement this so the pipeline can swap
or compare them at the cell level.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

from isda_p3.models import Engine, RawCell


@runtime_checkable
class ExtractionEngine(Protocol):
    """A deterministic PDF table extractor."""

    engine: Engine

    def extract(self, pdf_path: Path, pages: Sequence[int] | None = None) -> list[RawCell]:
        """Extract table cells from ``pdf_path`` as one flat list.

        ``pages`` (1-based) restricts extraction to those pages; ``None`` = all.
        Returns one :class:`RawCell` per table cell, with bbox provenance.
        """
        ...

    def extract_tables(
        self, pdf_path: Path, pages: Sequence[int] | None = None
    ) -> list[list[RawCell]]:
        """Extract table cells grouped *per table*.

        Same inputs as :meth:`extract`, but the cells of each table stay in their
        own list (row/col indices are local to each table). The pipeline needs the
        per-table grouping to pick the *right* table when a page holds several
        (:func:`isda_p3.mapping.classify.select_template_table`).
        """
        ...
