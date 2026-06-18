"""Fast, model-free tests for the Docling cell→RawCell transform (chunk 1.1).

These exercise ``cells_from_docling_table`` with lightweight duck-typed fakes
(``types.SimpleNamespace``) so they run without loading TableFormer / torch.
The real model path is covered by ``tests/integration/test_docling_real.py``.
"""

from types import SimpleNamespace

import pytest

from isda_p3.extraction.docling_engine import (
    DoclingEngine,
    ExtractionError,
    cells_from_docling_table,
)
from isda_p3.models import Engine, RawCell


def _cell(row, col, text, *, l, t, r, b):  # noqa: E741 - mirror Docling's bbox field names
    """A minimal duck-typed Docling ``TableCell`` (l/t/r/b bbox, top-left origin)."""
    return SimpleNamespace(
        start_row_offset_idx=row,
        start_col_offset_idx=col,
        text=text,
        bbox=SimpleNamespace(l=l, t=t, r=r, b=b),
    )


def _table(cells):
    """A minimal duck-typed Docling ``TableItem`` exposing ``.data.table_cells``."""
    return SimpleNamespace(data=SimpleNamespace(table_cells=cells))


@pytest.fixture
def tiny_table():
    """2x2: header row (Metric, Value) + one data row (CET1 ratio, 13.6¹)."""
    return _table(
        [
            _cell(0, 0, "Metric", l=10.0, t=20.0, r=60.0, b=30.0),
            _cell(0, 1, "Value", l=60.0, t=20.0, r=100.0, b=30.0),
            _cell(1, 0, "CET1 ratio", l=10.0, t=30.0, r=60.0, b=40.0),
            _cell(1, 1, "13.6¹", l=60.0, t=30.0, r=100.0, b=40.0),
        ]
    )


def test_returns_one_rawcell_per_cell(tiny_table):
    cells = cells_from_docling_table(tiny_table, page_no=4)
    assert len(cells) == 4
    assert all(isinstance(c, RawCell) for c in cells)


def test_row_col_indices_carried_through(tiny_table):
    cells = cells_from_docling_table(tiny_table, page_no=4)
    indices = {(c.row_idx, c.col_idx) for c in cells}
    assert indices == {(0, 0), (0, 1), (1, 0), (1, 1)}


def test_text_is_verbatim_and_unparsed(tiny_table):
    """The fused-footnote value passes through UNPARSED (LLM != ledger; §A.1)."""
    by_pos = {(c.row_idx, c.col_idx): c.text for c in cells_from_docling_table(tiny_table, 4)}
    assert by_pos[(0, 0)] == "Metric"
    assert by_pos[(1, 0)] == "CET1 ratio"
    assert by_pos[(1, 1)] == "13.6¹"  # exact bytes, superscript retained, no float coercion


def test_bbox_populated_with_page_and_coords(tiny_table):
    cells = cells_from_docling_table(tiny_table, page_no=4)
    value_cell = next(c for c in cells if (c.row_idx, c.col_idx) == (1, 1))
    assert value_cell.bbox.page == 4
    assert (value_cell.bbox.x0, value_cell.bbox.y0) == (60.0, 30.0)
    assert (value_cell.bbox.x1, value_cell.bbox.y1) == (100.0, 40.0)


def test_engine_is_docling(tiny_table):
    cells = cells_from_docling_table(tiny_table, page_no=4)
    assert all(c.engine is Engine.DOCLING for c in cells)


def test_labels_unset_at_extraction_time(tiny_table):
    """Row/col labels are a mapping concern (box 4), not extraction."""
    cells = cells_from_docling_table(tiny_table, page_no=4)
    assert all(c.row_label is None and c.col_label is None for c in cells)


def test_empty_table_returns_empty_list():
    """An empty table legitimately yields []; the 'no tables' guard lives in extract()."""
    assert cells_from_docling_table(_table([]), page_no=1) == []


def test_accepts_x0y1_bbox_alias():
    """Fakes may expose x0..y1 instead of l/t/r/b; both map to the same BBox."""
    cell = SimpleNamespace(
        start_row_offset_idx=0,
        start_col_offset_idx=0,
        text="x",
        bbox=SimpleNamespace(x0=1.0, y0=2.0, x1=3.0, y1=4.0),
    )
    [rc] = cells_from_docling_table(_table([cell]), page_no=2)
    assert (rc.bbox.x0, rc.bbox.y0, rc.bbox.x1, rc.bbox.y1) == (1.0, 2.0, 3.0, 4.0)


def test_missing_bbox_raises_loudly():
    """A cell without coordinates must fail loud, never fabricate provenance (§A.2)."""
    cell = SimpleNamespace(start_row_offset_idx=0, start_col_offset_idx=0, text="x", bbox=None)
    with pytest.raises(ValueError, match="bbox"):
        cells_from_docling_table(_table([cell]), page_no=1)


def test_present_but_none_coords_raises():
    """A bbox whose coords are present-but-None must not become (None,None,...)."""
    cell = SimpleNamespace(
        start_row_offset_idx=0,
        start_col_offset_idx=0,
        text="x",
        bbox=SimpleNamespace(l=None, t=None, r=None, b=None),
    )
    with pytest.raises(ValueError, match="bbox"):
        cells_from_docling_table(_table([cell]), page_no=1)


# --- extract() I/O guards (model-free via a fake converter) -----------------------


class _FakeConverter:
    def __init__(self, tables):
        self._tables = tables

    def convert(self, source):
        return SimpleNamespace(document=SimpleNamespace(tables=self._tables))


def _provd_table(cells, page_no):
    return SimpleNamespace(
        prov=[SimpleNamespace(page_no=page_no)],
        data=SimpleNamespace(table_cells=cells),
    )


def _engine_with(tables, monkeypatch):
    eng = DoclingEngine()
    monkeypatch.setattr(eng, "_get_converter", lambda: _FakeConverter(tables))
    return eng


def test_extract_missing_file_raises(tmp_path):
    with pytest.raises(ExtractionError, match="not found"):
        DoclingEngine().extract(tmp_path / "nope.pdf")


def test_extract_no_tables_raises(tmp_path, monkeypatch):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    eng = _engine_with([], monkeypatch)
    with pytest.raises(ExtractionError, match="no tables"):
        eng.extract(pdf)


def test_extract_page_filter_miss_raises(tmp_path, monkeypatch):
    """All tables on page 1, caller asks for page 9 → loud error, not silent []."""
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    cell = _cell(0, 0, "v", l=1.0, t=2.0, r=3.0, b=4.0)
    eng = _engine_with([_provd_table([cell], page_no=1)], monkeypatch)
    with pytest.raises(ExtractionError, match="none on pages"):
        eng.extract(pdf, pages=[9])


def test_extract_page_filter_hit_returns_cells(tmp_path, monkeypatch):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    cell = _cell(0, 0, "v", l=1.0, t=2.0, r=3.0, b=4.0)
    eng = _engine_with([_provd_table([cell], page_no=1)], monkeypatch)
    cells = eng.extract(pdf, pages=[1])
    assert [c.text for c in cells] == ["v"]
    assert cells[0].bbox.page == 1
