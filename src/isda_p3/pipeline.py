"""DI orchestrator wiring boxes 3 -> 4 -> 5 (chunk 1.8).

The seam that makes the whole slice stub-testable: the extraction engine and the
(optional) bounded LLM mapper are *injected*, so the unit gate runs with zero
network, zero key, and a synthetic in-memory table. The pipeline performs no I/O
of its own — every byte read comes through ``engine`` (CLAUDE.md §A.3, four-eyes by
construction; the validator/reconciler never sees the extractor's reasoning).

Flow for one (bank, period, template, pdf):
  1. extract every table, then pick the one that *is* this template (fail loud if
     none — never map an arbitrary grid, §A.2);
  2. rule-first map its rows to canonical :class:`FieldValue`s;
  3. bounded LLM fallback on the rows the rules missed (only if a mapper is given);
  4. reconcile the mapped values and route each (AUTO_PASSED / FLAGGED).

Persistence (box 7) lives in the CLI entrypoint, not here: the pipeline returns the
:class:`ReconciliationResult`s and the caller decides whether to store them.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

from .config_load import TemplateSpec, load_template
from .extraction.engine import ExtractionEngine
from .mapping.classify import select_template_table
from .mapping.llm import StructuredMapper, map_unmatched
from .mapping.map_fields import map_fields
from .mapping.merge import merge_engine_values
from .models import (
    Bank,
    FieldValue,
    ReconciliationResult,
    ReportingPeriod,
    SourceKind,
    Template,
)
from .reconcile.engine import reconcile_template

log = logging.getLogger(__name__)


class NoTemplateTableError(RuntimeError):
    """No usable template data was produced — refuse to emit empty/fabricated output.

    Raised when no extracted table matches the template, *or* when the selected table
    maps to zero parseable fields: in both cases there is nothing real to reconcile, so
    we fail loud rather than return a clean-looking empty result (CLAUDE.md §A.2 — no
    number without a real source; an empty success is a silent failure).
    """


def _map_engine(
    pdf_path: Path,
    engine: ExtractionEngine,
    spec: TemplateSpec,
    *,
    bank: Bank,
    period: ReportingPeriod,
    source_url: str,
    source_kind: SourceKind,
    mapper: StructuredMapper | None,
) -> list[FieldValue] | None:
    """Extract + select + map one engine's view of ``template`` -> ``list[FieldValue]``.

    Returns ``None`` if no extracted table matches the template (so callers can decide
    whether that is fatal — a missing PRIMARY table is, a missing SECONDARY one is only
    a coverage gap). Rule-first mapping; the bounded LLM fallback runs only on unmatched
    labels and only when a ``mapper`` is injected.
    """
    groups = engine.extract_tables(pdf_path)
    cells = select_template_table(groups, spec)
    if cells is None:
        return None
    mapped, unmatched = map_fields(
        cells, spec, bank, period, source_url, source_kind, engine.engine
    )
    if mapper is not None and unmatched:
        mapped += map_unmatched(
            unmatched, cells, spec, bank, period, source_url, source_kind, engine.engine, mapper
        )
    return mapped


def extract_template(
    pdf_path: Path,
    *,
    bank: Bank,
    period: ReportingPeriod,
    template: Template,
    source_url: str,
    source_kind: SourceKind,
    engine: ExtractionEngine,
    tolerances: Mapping[str, Mapping[str, Decimal]],
    weights: Mapping[str, Decimal],
    mapper: StructuredMapper | None = None,
    secondary_engine: ExtractionEngine | None = None,
) -> list[ReconciliationResult]:
    """Run boxes 3->4->5 for one template and return the reconciled results.

    Deterministic given the same ``engine`` output: extraction emits verbatim cell
    strings, mapping is rule-first, and the only non-deterministic seam (the LLM
    fallback) is bounded to *row choice* and skipped entirely when ``mapper`` is
    ``None`` (the M1 default). Raises :class:`NoTemplateTableError` if no extracted
    table looks like ``template``.

    If ``secondary_engine`` is given (the chunk-2.2 wiring, now live), it is run and
    mapped independently, its numbers folded into the primary FieldValues by
    :func:`merge_engine_values` (post-mapping, by ``field_code`` — never by raw cell),
    so ``reconcile_template``'s two-engine agreement check fires live. The primary
    (Docling) stays canonical throughout; a secondary that finds no template table is a
    logged coverage gap (two-engine SKIPs), not a failure. With ``secondary_engine``
    ``None`` the behaviour is unchanged from M1 (two-engine SKIPs; identities still gate).
    """
    spec = load_template(template)

    groups = engine.extract_tables(pdf_path)
    cells = select_template_table(groups, spec)
    if cells is None:
        raise NoTemplateTableError(
            f"no {template.value} table found in {pdf_path} "
            f"({len(groups)} table(s) extracted, none matched the template's row labels)"
        )

    mapped, unmatched = map_fields(
        cells, spec, bank, period, source_url, source_kind, engine.engine
    )
    if mapper is not None and unmatched:
        mapped += map_unmatched(
            unmatched, cells, spec, bank, period, source_url, source_kind, engine.engine, mapper
        )

    if secondary_engine is not None:
        secondary = _map_engine(
            pdf_path,
            secondary_engine,
            spec,
            bank=bank,
            period=period,
            source_url=source_url,
            source_kind=source_kind,
            mapper=mapper,
        )
        if secondary is None:
            log.warning(
                "extract_template(%s): secondary engine %s found no template table in %s "
                "— two-engine cross-check will SKIP",
                template.value,
                secondary_engine.engine.value,
                pdf_path,
            )
            secondary = []
        mapped = merge_engine_values(mapped, secondary)

    # Build the code->value map, refusing to silently drop a collision: two values
    # for one field code (possible if the LLM fallback grounds the same code twice)
    # would otherwise vanish under last-wins, losing an audited number (§A.2).
    values: dict[str, FieldValue] = {}
    for fv in mapped:
        if fv.field_code in values:
            log.warning(
                "extract_template(%s): duplicate field_code %r from mapping — keeping the "
                "first, discarding the rest",
                template.value,
                fv.field_code,
            )
            continue
        values[fv.field_code] = fv

    if not values:
        raise NoTemplateTableError(
            f"{template.value} table selected in {pdf_path} but no field mapped to a parseable "
            f"value — refusing to emit empty output (CLAUDE.md §A.2)"
        )

    return reconcile_template(values, template, tolerances=tolerances, weights=weights)
