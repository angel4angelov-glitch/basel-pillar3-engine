"""Extraction-environment smoke test (chunk 0.5).

Asserts the three deterministic extraction engines import. Uses ``importlib.importorskip``
so CI without the heavy deps stays green (clear skip reason). In a venv with the deps
installed this MUST pass, not skip.
"""

import pytest


def test_extraction_engines_import():
    pytest.importorskip("docling", reason="docling not installed (heavy: pulls torch)")
    pytest.importorskip("camelot", reason="camelot-py not installed")
    pytest.importorskip("pdfplumber", reason="pdfplumber not installed")

    from docling.document_converter import DocumentConverter  # noqa: F401
    import camelot  # noqa: F401
    import pdfplumber  # noqa: F401
