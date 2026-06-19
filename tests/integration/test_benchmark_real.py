"""Opt-in Docling-vs-Camelot benchmark on the real golden set (chunk 4.2).

Skips cleanly unless ``data/golden/pdfs`` holds at least one PDF with a matching
expected YAML. When populated it runs the full extract -> map -> reconcile pipeline
with each engine and reports per-engine exact-match rate + bootstrap CI. Marked
``integration`` so the default suite stays fast, key-free and weight-free.

Honesty (CLAUDE.md §A / plan C3): ``compare_engines`` LOGS the golden N, so a wide
CI from a small N is never silently presented as precise.
"""

from __future__ import annotations

import pytest

from isda_p3.config import Paths
from isda_p3.models import Template


def _golden_pdfs() -> list:
    if not Paths.GOLDEN_PDFS.exists():
        return []
    return sorted(Paths.GOLDEN_PDFS.glob("*.pdf"))


@pytest.mark.integration
def test_compare_engines_on_golden_if_present(caplog):
    if not _golden_pdfs():
        pytest.skip("populate data/golden to enable")

    # Imported lazily so the skip path needs no extraction-engine dependencies.
    import logging

    from isda_p3.analytics.benchmark import compare_engines
    from isda_p3.extraction.docling_engine import DoclingEngine
    from isda_p3.extraction.pdfplumber_bbox import PdfplumberEngine

    with caplog.at_level(logging.INFO, logger="isda_p3.analytics.benchmark"):
        table = compare_engines(
            [DoclingEngine(), PdfplumberEngine()],
            Paths.GOLDEN_PDFS,
            Paths.GOLDEN_EXPECTED,
            Template.KM1,
        )

    assert table  # one row per engine
    for row in table:
        assert {"engine", "n_docs", "n_fields", "hits", "rate", "ci_lo", "ci_hi"} <= row.keys()
        assert 0.0 <= row["ci_lo"] <= row["rate"] <= row["ci_hi"] <= 1.0
    # N is logged for audit honesty (wide CI from small N never presented as precise)
    assert any("golden N" in r.message for r in caplog.records)
