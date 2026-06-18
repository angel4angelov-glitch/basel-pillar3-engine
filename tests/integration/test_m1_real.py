"""Opt-in M1 golden regression on a REAL bank KM1 PDF (chunk 1.8).

Skips cleanly unless a hand-verified PDF + golden pair is present:

    data/golden/pdfs/barclays_2025Q4.pdf
    data/golden/expected/barclays_2025Q4_km1.yaml   (SCHEMA.md shape)

When both exist, it runs the actual DoclingEngine through the full pipeline and
asserts every golden value matches Decimal-exactly (extraction is deterministic, so
this is a regression baseline, not pass^k). Marked ``integration`` so the default
suite stays fast, key-free and weight-free.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import yaml

from isda_p3.config import Paths
from isda_p3.extraction.docling_engine import DoclingEngine
from isda_p3.models import EclBasis, FloorBasis, ReportingPeriod, SourceKind, Template, Unit
from isda_p3.pipeline import extract_template
from isda_p3.reconcile.identities import load_tolerances, load_weights

_PDF = Paths.GOLDEN_PDFS / "barclays_2025Q4.pdf"
_GOLDEN = Paths.GOLDEN_EXPECTED / "barclays_2025Q4_km1.yaml"


@pytest.mark.integration
def test_m1_real_barclays_km1_matches_golden():
    if not (_PDF.exists() and _GOLDEN.exists()):
        pytest.skip("drop a real KM1 PDF + golden to enable")

    # Imported lazily so the skip path needs no CLI/config dependencies.
    from isda_p3.cli import load_bank

    golden = yaml.safe_load(_GOLDEN.read_text(encoding="utf-8"))
    bank = load_bank(golden["bank"])
    period = ReportingPeriod.parse(golden["period"])
    template = Template(golden["template"])

    results = extract_template(
        _PDF,
        bank=bank,
        period=period,
        template=template,
        source_url=bank.ir_url,
        source_kind=SourceKind.PDF,
        engine=DoclingEngine(),
        tolerances=load_tolerances(),
        weights=load_weights(),
        mapper=None,
    )
    by = {r.field_value.field_code: r.field_value for r in results}

    for code, exp in golden["values"].items():
        assert code in by, f"{code} missing from extraction"
        fv = by[code]
        assert fv.value == Decimal(exp["value"]), code
        assert fv.unit is Unit(exp["unit"]), code
        assert fv.ecl_basis is EclBasis(exp["ecl_basis"]), code
        assert fv.floor_basis is FloorBasis(exp["floor_basis"]), code
        assert fv.provenance.bbox is not None and fv.provenance.bbox.page >= 1, code
