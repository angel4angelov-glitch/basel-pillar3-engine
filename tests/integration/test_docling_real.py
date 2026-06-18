"""Opt-in integration test: real DoclingEngine on a tiny committed PDF (chunk 1.1).

Runs the actual TableFormer model (downloads weights on first run). Marked
``integration`` so the default suite stays fast and key/weight-free.
"""

from pathlib import Path

import pytest

from isda_p3.extraction.docling_engine import DoclingEngine
from isda_p3.models import Engine

FIXTURE = Path(__file__).parent.parent / "fixtures" / "km1_tiny.pdf"


@pytest.mark.integration
def test_docling_extracts_cells_with_bbox():
    assert FIXTURE.exists(), f"missing fixture {FIXTURE}"
    cells = DoclingEngine().extract(FIXTURE)

    assert cells, "expected at least one RawCell from the tiny table"
    assert all(c.engine is Engine.DOCLING for c in cells)

    # RawCell.bbox is non-optional; every cell must carry real, on-page provenance.
    assert all(c.bbox.page >= 1 for c in cells)
    assert cells[0].bbox.page >= 1

    # Regression anchor: the fixture's known cell strings must survive VERBATIM
    # (comma + decimal preserved, never parsed to a number — §A.1).
    texts = {c.text for c in cells}
    assert "CET1 ratio" in texts
    assert "13.6" in texts
    assert "1,234" in texts
