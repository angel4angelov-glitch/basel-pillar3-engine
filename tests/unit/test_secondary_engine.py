"""Fast, model-free tests for the second extraction engine (chunk 2.1).

These exercise the pure cores (``cells_from_camelot_table`` /
``cells_from_pdfplumber_table``) and the ``select_secondary_engine`` chooser
with lightweight duck-typed fakes — no real PDF, no Ghostscript, no key.

The load-bearing test is :func:`test_camelot_yflip_exact`: chunk 1.1 stored
Docling bbox as TOP-LEFT origin, and 2.2 compares bboxes across engines, so the
Camelot y-flip (bottom-left → top-left) must be exact.
"""

from types import SimpleNamespace

import pytest

from isda_p3.extraction.camelot_engine import (
    CamelotEngine,
    cells_from_camelot_table,
)
from isda_p3.extraction.docling_engine import ExtractionError
from isda_p3.extraction.pdfplumber_bbox import (
    PdfplumberEngine,
    cells_from_pdfplumber_table,
)
from isda_p3.extraction import secondary
from isda_p3.models import Engine, RawCell


# --- Camelot fakes (PDFMiner bottom-left origin: x1,y1=left-bottom; x2,y2=right-top) ---


def _cam_cell(text, *, x1, y1, x2, y2):
    """A minimal duck-typed camelot ``Cell`` (bottom-left origin coords)."""
    return SimpleNamespace(text=text, x1=x1, y1=y1, x2=x2, y2=y2)


def _cam_table(rows_of_cells, page):
    """A minimal duck-typed camelot ``Table`` exposing ``.cells`` + ``.page``."""
    return SimpleNamespace(cells=rows_of_cells, page=page)


# --- pdfplumber fakes (native top-left: cell bbox = (x0, top, x1, bottom)) ----------


def _pp_table(cell_grid, text_grid):
    """A duck-typed pdfplumber ``Table``: ``.rows[i].cells[j]`` bbox + ``.extract()``."""
    rows = [SimpleNamespace(cells=row) for row in cell_grid]
    return SimpleNamespace(rows=rows, extract=lambda: text_grid)


# --- y-flip correctness (the load-bearing test for 2.2) -----------------------------


def test_camelot_yflip_exact():
    """A camelot cell at a known PDFMiner bbox maps to the expected TOP-LEFT BBox.

    page_height=800; visual box left=10 right=100, top=20 bottom=40 (from page top).
    In bottom-left coords that is y2(top)=780, y1(bottom)=760.
    """
    cell = _cam_cell("v", x1=10.0, y1=760.0, x2=100.0, y2=780.0)
    [rc] = cells_from_camelot_table(_cam_table([[cell]], page=4), page_height=800.0)
    assert (rc.bbox.x0, rc.bbox.y0, rc.bbox.x1, rc.bbox.y1) == (10.0, 20.0, 100.0, 40.0)
    assert rc.bbox.page == 4
    assert rc.engine is Engine.CAMELOT


# --- 2x2 grids: 4 RawCells, verbatim text, correct indices, engine tag --------------


@pytest.fixture
def cam_2x2():
    """2x2 camelot grid (bottom-left coords), page_height=100 for easy flips."""
    return _cam_table(
        [
            [
                _cam_cell("Metric", x1=10, y1=70, x2=60, y2=80),
                _cam_cell("Value", x1=60, y1=70, x2=100, y2=80),
            ],
            [
                _cam_cell("CET1 ratio", x1=10, y1=60, x2=60, y2=70),
                _cam_cell("13.6¹", x1=60, y1=60, x2=100, y2=70),
            ],
        ],
        page=4,
    )


def test_camelot_2x2_four_cells_verbatim(cam_2x2):
    cells = cells_from_camelot_table(cam_2x2, page_height=100.0)
    assert len(cells) == 4
    assert all(isinstance(c, RawCell) for c in cells)
    by_pos = {(c.row_idx, c.col_idx): c.text for c in cells}
    assert by_pos == {
        (0, 0): "Metric",
        (0, 1): "Value",
        (1, 0): "CET1 ratio",
        (1, 1): "13.6¹",  # fused footnote superscript passes through unparsed (§A.1)
    }
    assert all(c.engine is Engine.CAMELOT for c in cells)
    assert all(c.row_label is None and c.col_label is None for c in cells)


def test_camelot_2x2_flips_every_row(cam_2x2):
    """Header (visual top) gets the SMALLER y0; data row sits below it."""
    by_pos = {(c.row_idx, c.col_idx): c.bbox for c in cells_from_camelot_table(cam_2x2, 100.0)}
    assert (by_pos[(0, 0)].y0, by_pos[(0, 0)].y1) == (20.0, 30.0)  # 100-80, 100-70
    assert (by_pos[(1, 0)].y0, by_pos[(1, 0)].y1) == (30.0, 40.0)  # 100-70, 100-60
    assert by_pos[(0, 0)].y0 < by_pos[(1, 0)].y0  # header above data in top-left convention


@pytest.fixture
def pp_2x2():
    """2x2 pdfplumber grid; cell bbox already top-left = (x0, top, x1, bottom)."""
    return _pp_table(
        cell_grid=[
            [(10, 20, 60, 30), (60, 20, 100, 30)],
            [(10, 30, 60, 40), (60, 30, 100, 40)],
        ],
        text_grid=[["Metric", "Value"], ["CET1 ratio", "13.6¹"]],
    )


def test_pdfplumber_2x2_four_cells_verbatim(pp_2x2):
    cells = cells_from_pdfplumber_table(pp_2x2, page_no=4)
    assert len(cells) == 4
    by_pos = {(c.row_idx, c.col_idx): c.text for c in cells}
    assert by_pos == {
        (0, 0): "Metric",
        (0, 1): "Value",
        (1, 0): "CET1 ratio",
        (1, 1): "13.6¹",  # verbatim passthrough, no flip needed (already top-left)
    }
    assert all(c.engine is Engine.PDFPLUMBER for c in cells)
    assert all(c.bbox.page == 4 for c in cells)
    assert all(c.row_label is None and c.col_label is None for c in cells)


def test_pdfplumber_no_flip(pp_2x2):
    """pdfplumber top/bottom are already top-left → coords pass through untouched."""
    by_pos = {(c.row_idx, c.col_idx): c.bbox for c in cells_from_pdfplumber_table(pp_2x2, 4)}
    assert (by_pos[(0, 0)].x0, by_pos[(0, 0)].y0, by_pos[(0, 0)].x1, by_pos[(0, 0)].y1) == (
        10.0,
        20.0,
        60.0,
        30.0,
    )


# --- coordinate-convention parity: same visual cell, both engines, same BBox --------


def test_engines_agree_on_toplevel_bbox():
    """A cell at the same visual position via Camelot and pdfplumber yields the SAME
    top-left BBox — proving Docling/Camelot/pdfplumber boxes are comparable (2.2)."""
    page_height = 792.0  # US Letter
    # visual box: left=72, right=200, top=100, bottom=130 (measured from page top)
    pp_table = _pp_table(
        cell_grid=[[(72.0, 100.0, 200.0, 130.0)]],
        text_grid=[["x"]],
    )
    cam_cell = _cam_cell(
        "x", x1=72.0, y1=page_height - 130.0, x2=200.0, y2=page_height - 100.0
    )
    [pp] = cells_from_pdfplumber_table(pp_table, page_no=1)
    [cam] = cells_from_camelot_table(_cam_table([[cam_cell]], page=1), page_height=page_height)
    for a, b in zip(
        (cam.bbox.x0, cam.bbox.y0, cam.bbox.x1, cam.bbox.y1),
        (pp.bbox.x0, pp.bbox.y0, pp.bbox.x1, pp.bbox.y1),
    ):
        assert a == pytest.approx(b, abs=1e-9)


# --- missing / non-finite cell bbox → raises (no silent drop) -----------------------


def test_camelot_missing_coord_raises():
    cell = _cam_cell("x", x1=10.0, y1=None, x2=100.0, y2=20.0)
    with pytest.raises(ValueError, match="bbox"):
        cells_from_camelot_table(_cam_table([[cell]], page=1), page_height=100.0)


def test_camelot_non_finite_coord_raises():
    cell = _cam_cell("x", x1=10.0, y1=float("nan"), x2=100.0, y2=20.0)
    with pytest.raises(ValueError, match="bbox"):
        cells_from_camelot_table(_cam_table([[cell]], page=1), page_height=100.0)


def test_camelot_non_finite_page_height_raises():
    cell = _cam_cell("x", x1=10.0, y1=10.0, x2=100.0, y2=20.0)
    with pytest.raises(ValueError, match="page_height"):
        cells_from_camelot_table(_cam_table([[cell]], page=1), page_height=float("inf"))


def test_pdfplumber_non_finite_coord_raises():
    table = _pp_table(cell_grid=[[(10.0, float("nan"), 60.0, 30.0)]], text_grid=[["x"]])
    with pytest.raises(ValueError, match="bbox"):
        cells_from_pdfplumber_table(table, page_no=1)


def test_pdfplumber_none_cell_is_structural_gap_not_a_drop():
    """A ``None`` entry in ``row.cells`` is an absent cell (ragged grid), not a
    dropped value — the surrounding real cells are still emitted."""
    table = _pp_table(
        cell_grid=[[(10.0, 20.0, 60.0, 30.0), None]],
        text_grid=[["Metric", None]],
    )
    cells = cells_from_pdfplumber_table(table, page_no=1)
    assert [(c.row_idx, c.col_idx, c.text) for c in cells] == [(0, 0, "Metric")]


# --- select_secondary_engine: lattice → stream → pdfplumber (stubbed probes) --------


@pytest.fixture
def pdf(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4")
    return p


def _stub_probes(monkeypatch, *, gs, counts):
    """Stub gs detection + per-flavor camelot table counts (no real PDF/gs)."""
    monkeypatch.setattr(secondary, "ghostscript_available", lambda: gs)
    monkeypatch.setattr(
        secondary, "_camelot_table_count", lambda path, flavor: counts.get(flavor, 0)
    )


def test_select_gs_present_lattice_hits_picks_lattice(pdf, monkeypatch):
    _stub_probes(monkeypatch, gs=True, counts={"lattice": 2, "stream": 5})
    eng = secondary.select_secondary_engine(pdf)
    assert isinstance(eng, CamelotEngine) and eng.flavor == "lattice"


def test_select_lattice_empty_falls_back_to_stream(pdf, monkeypatch):
    _stub_probes(monkeypatch, gs=True, counts={"lattice": 0, "stream": 3})
    eng = secondary.select_secondary_engine(pdf)
    assert isinstance(eng, CamelotEngine) and eng.flavor == "stream"


def test_select_both_camelot_empty_falls_back_to_pdfplumber(pdf, monkeypatch):
    _stub_probes(monkeypatch, gs=True, counts={"lattice": 0, "stream": 0})
    eng = secondary.select_secondary_engine(pdf)
    assert isinstance(eng, PdfplumberEngine)


def test_select_gs_absent_never_picks_lattice(pdf, monkeypatch):
    """Ghostscript missing (the 0.5 finding) → lattice is skipped, not attempted."""
    probed = []
    monkeypatch.setattr(secondary, "ghostscript_available", lambda: False)

    def _count(path, flavor):
        probed.append(flavor)
        return 4 if flavor == "stream" else 0

    monkeypatch.setattr(secondary, "_camelot_table_count", _count)
    eng = secondary.select_secondary_engine(pdf)
    assert "lattice" not in probed  # never even probed lattice
    assert isinstance(eng, CamelotEngine) and eng.flavor == "stream"


def test_select_gs_absent_stream_empty_falls_back_to_pdfplumber(pdf, monkeypatch):
    _stub_probes(monkeypatch, gs=False, counts={"stream": 0})
    eng = secondary.select_secondary_engine(pdf)
    assert isinstance(eng, PdfplumberEngine)


def test_select_missing_file_raises(tmp_path):
    with pytest.raises(ExtractionError, match="not found"):
        secondary.select_secondary_engine(tmp_path / "nope.pdf")
