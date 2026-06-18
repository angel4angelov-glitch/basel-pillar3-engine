"""Opt-in integration test: the real second engine on a tiny committed PDF (chunk 2.1).

Runs ``select_secondary_engine`` + the chosen engine against the bundled fixture.
Ghostscript is not installed in this environment (the 0.5 audit), so the selector
must skip Camelot lattice and fall back to stream or pdfplumber — the test asserts
that, rather than failing on missing ``gs``. Marked ``integration`` so the default
suite stays fast.
"""

from pathlib import Path

import pytest

from isda_p3.extraction.camelot_engine import CamelotEngine, ghostscript_available
from isda_p3.extraction.pdfplumber_bbox import PdfplumberEngine
from isda_p3.extraction.secondary import select_secondary_engine

FIXTURE = Path(__file__).parent.parent / "fixtures" / "km1_tiny.pdf"


@pytest.mark.integration
def test_secondary_extracts_cells_with_toplevel_bbox():
    assert FIXTURE.exists(), f"missing fixture {FIXTURE}"
    engine = select_secondary_engine(FIXTURE)

    # No Ghostscript here → must NOT have chosen Camelot lattice.
    if not ghostscript_available():
        assert not (isinstance(engine, CamelotEngine) and engine.flavor == "lattice")
        assert isinstance(engine, (CamelotEngine, PdfplumberEngine))

    cells = engine.extract(FIXTURE)
    assert cells, "expected at least one RawCell from the tiny table"

    # Every cell carries real, on-page, top-left provenance (x0<=x1, y0<=y1).
    for c in cells:
        assert c.bbox.page >= 1
        assert c.bbox.x0 <= c.bbox.x1
        assert c.bbox.y0 <= c.bbox.y1
