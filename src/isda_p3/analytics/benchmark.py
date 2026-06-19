"""Engine-accuracy benchmark (chunk 4.2) — exact-match + seeded bootstrap CI.

Two pure statistics plus an integration runner that turns the hand-verified golden
set into the slide's headline accuracy number:

- :func:`exact_match` — deterministic scorer; a field counts iff present AND
  Decimal-equal (never a float tolerance). This is the regression metric for a
  deterministic extractor, not ``pass^k`` (per the plan).
- :func:`bootstrap_ci` — seeded percentile bootstrap CI on the match rate, so the
  reported interval is reproducible run-to-run.
- :func:`benchmark_engine` / :func:`compare_engines` — run the real pipeline per
  engine over the golden PDFs and report rate + CI.

Audit honesty (CLAUDE.md §A / plan C3): consistency is not correctness, so accuracy
is asserted only on this golden set. A wide CI from a small golden N must never be
silently presented as precise — :func:`compare_engines` LOGS N for exactly that.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from ..models import Template

if TYPE_CHECKING:  # avoid importing the heavy extraction stack at module load
    from ..extraction.engine import ExtractionEngine

log = logging.getLogger(__name__)

#: Below this many golden docs the CI is too wide to present as precise (plan: grow
#: the golden set to >=5 banks). :func:`compare_engines` warns when N falls short.
_CREDIBLE_GOLDEN_N = 5


def exact_match(
    extracted: Mapping[str, Decimal], expected: Mapping[str, Decimal]
) -> tuple[int, int]:
    """Score one document: ``(matches, total_expected)``.

    Rule: a field counts as a match **iff** it is present in ``extracted`` AND its
    value equals the expected value *exactly* (``Decimal ==`` — note ``15.00`` and
    ``15.0`` are numerically equal Decimals and so match). ``total_expected`` is
    ``len(expected)``. A field in ``expected`` but missing from ``extracted`` is a
    non-match (no credit). A field in ``extracted`` but not in ``expected`` is also
    a non-match: it earns no credit and does not change the denominator — extra
    output cannot inflate the score.
    """
    total = len(expected)
    matches = sum(1 for code, exp in expected.items() if extracted.get(code) == exp)
    return matches, total


def bootstrap_ci(
    hits: int,
    n: int,
    *,
    iters: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float]:
    """Seeded percentile bootstrap CI on the match rate ``hits / n``.

    Treats the ``n`` scored fields as Bernoulli trials with ``hits`` successes,
    resamples them with replacement ``iters`` times, and returns the
    ``[alpha/2, 1 - alpha/2]`` percentiles of the resampled mean. The RNG is seeded
    (``numpy.random.default_rng(seed)``) so the interval is reproducible.

    ``n == 0`` (no trials -> no information) returns ``(0.0, 0.0)`` by convention.
    Raises ``ValueError`` if ``hits`` is outside ``[0, n]`` — a nonsensical count
    must fail loud, never yield a quietly wrong interval (CLAUDE.md §A).
    """
    # Range-check hits BEFORE the n==0 shortcut, else a negative hits slips through
    # the early return and silently yields a (0.0, 0.0) interval.
    if hits < 0 or hits > n:
        raise ValueError(f"hits={hits} out of range [0, {n}]")
    if n == 0:
        return (0.0, 0.0)

    rng = np.random.default_rng(seed)
    sample = np.zeros(n, dtype=float)
    sample[:hits] = 1.0  # order is irrelevant to the resampled mean
    draws = rng.integers(0, n, size=(iters, n))
    means = sample[draws].mean(axis=1)
    lo = float(np.percentile(means, 100 * alpha / 2))
    hi = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return lo, hi


def _expected_path(golden_expected_dir: Path, pdf: Path, template: Template) -> Path:
    """Golden YAML paired with ``pdf`` for ``template`` (``<stem>_<tmpl>.yaml``)."""
    return golden_expected_dir / f"{pdf.stem}_{template.value.lower()}.yaml"


def benchmark_engine(
    engine: ExtractionEngine,
    golden_pdf_dir: Path,
    golden_expected_dir: Path,
    template: Template,
) -> dict:
    """Run the full pipeline with ``engine`` over every paired golden PDF (INTEGRATION).

    For each ``*.pdf`` in ``golden_pdf_dir`` with a matching expected YAML, runs
    extract -> map -> reconcile (no LLM fallback) and scores it with
    :func:`exact_match`. Returns ``{engine, n_docs, n_fields, hits, rate, ci_lo,
    ci_hi}`` where ``rate = hits / n_fields`` and the CI is the seeded bootstrap.

    Raises ``ValueError`` if no golden pair is found (or no expected field exists):
    a fake ``rate=0`` over zero data would be a silent failure — better to fail loud
    so the caller knows nothing was actually benchmarked (CLAUDE.md §A).
    """
    # Heavy / config imports kept local so the pure stats above stay import-light.
    import yaml

    from ..cli import load_bank
    from ..models import ReportingPeriod, SourceKind
    from ..pipeline import extract_template
    from ..reconcile.identities import load_tolerances, load_weights

    tolerances = load_tolerances()
    weights = load_weights()

    n_docs = 0
    hits = 0
    n_fields = 0
    for pdf in sorted(golden_pdf_dir.glob("*.pdf")):
        expected_path = _expected_path(golden_expected_dir, pdf, template)
        if not expected_path.exists():
            # A PDF with no paired golden is a data-setup gap, not a routine event:
            # warn so a wholesale mis-naming is visible before the final raise.
            log.warning("no golden YAML for %s (%s); skipping", pdf.name, expected_path.name)
            continue

        golden = yaml.safe_load(expected_path.read_text(encoding="utf-8"))
        bank = load_bank(golden["bank"])
        period = ReportingPeriod.parse(golden["period"])
        expected = {code: Decimal(str(v["value"])) for code, v in golden["values"].items()}

        results = extract_template(
            pdf,
            bank=bank,
            period=period,
            template=template,
            source_url=bank.ir_url,
            source_kind=SourceKind.PDF,
            engine=engine,
            tolerances=tolerances,
            weights=weights,
            mapper=None,
        )
        extracted = {r.field_value.field_code: r.field_value.value for r in results}

        doc_hits, doc_total = exact_match(extracted, expected)
        hits += doc_hits
        n_fields += doc_total
        n_docs += 1

    if n_fields == 0:
        raise ValueError(
            f"no golden pair scored for {template.value} under {golden_pdf_dir} / "
            f"{golden_expected_dir} (need <stem>_{template.value.lower()}.yaml beside each PDF)"
        )

    rate = hits / n_fields
    ci_lo, ci_hi = bootstrap_ci(hits, n_fields)
    return {
        "engine": str(engine.engine),
        "n_docs": n_docs,
        "n_fields": n_fields,
        "hits": hits,
        "rate": rate,
        "ci_lo": ci_lo,
        "ci_hi": ci_hi,
    }


def compare_engines(
    engines: Sequence[ExtractionEngine],
    golden_pdf_dir: Path,
    golden_expected_dir: Path,
    template: Template,
) -> list[dict]:
    """Benchmark each engine over the golden set; return one rate+CI row per engine.

    LOGS the golden N (``n_docs`` / ``n_fields``) for every engine, and emits a
    WARNING when ``n_docs`` is below :data:`_CREDIBLE_GOLDEN_N`, so a wide CI from a
    small golden set is never silently presented as precise (CLAUDE.md §A / plan C3).
    """
    rows: list[dict] = []
    for engine in engines:
        row = benchmark_engine(engine, golden_pdf_dir, golden_expected_dir, template)
        rows.append(row)
        log.info(
            "%s %s: golden N = %d docs / %d fields -> rate %.3f (CI %.3f-%.3f)",
            template.value,
            row["engine"],
            row["n_docs"],
            row["n_fields"],
            row["rate"],
            row["ci_lo"],
            row["ci_hi"],
        )
        if row["n_docs"] < _CREDIBLE_GOLDEN_N:
            log.warning(
                "golden N small (%d docs < %d): the CI is wide — do not present this "
                "rate as precise (plan C3)",
                row["n_docs"],
                _CREDIBLE_GOLDEN_N,
            )
    return rows
