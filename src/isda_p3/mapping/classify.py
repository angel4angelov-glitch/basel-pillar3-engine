"""Template-table selection (chunk 1.8) — pick the right grid among many.

A Pillar 3 page often carries several tables (KM1 beside a buffer table, a decoy
narrative grid, …). :func:`select_template_table` chooses the group whose row
labels best match a template's aliases, so the mapper never runs against the wrong
table. It reuses the *exact* label-normalisation + alias-matching of
:mod:`isda_p3.mapping.map_fields` (no second, divergent matcher) and returns
``None`` if no group clears a small threshold — the caller then fails loud rather
than mapping an empty/irrelevant grid (CLAUDE.md §A.2 — no silent empty output).
"""

from __future__ import annotations

import logging

from ..config_load import TemplateSpec
from ..models import RawCell
from .map_fields import _build_label_index, _group_rows, _match_alias

log = logging.getLogger(__name__)

#: Minimum number of fields whose alias matches a row before a group is accepted as
#: *this* template's table. Small on purpose: a real KM1 grid matches all seven
#: headline rows, while a decoy/narrative table matches ~zero. Two guards against a
#: one-off coincidental label collision tipping the choice.
_MIN_TEMPLATE_MATCHES = 2


def _count_alias_matches(group: list[RawCell], spec: TemplateSpec) -> int:
    """How many of ``spec``'s fields have a row-label alias present in ``group``."""
    index = _build_label_index(_group_rows(group))
    return sum(1 for field in spec.fields if _match_alias(field, index) is not None)


def select_template_table(
    groups: list[list[RawCell]],
    spec: TemplateSpec,
    *,
    min_matches: int = _MIN_TEMPLATE_MATCHES,
) -> list[RawCell] | None:
    """Return the table group that best matches ``spec``, or ``None`` if none fits.

    Scores every group by the number of template fields whose alias matches one of
    its row labels and returns the highest-scoring group, provided that score is at
    least ``min_matches``. Ties keep the first (topmost) group — a stable,
    order-independent choice. ``None`` ⇒ no group looks like the template; the
    caller must fail loud, never map an arbitrary grid.
    """
    best_group: list[RawCell] | None = None
    best_score = -1
    for i, group in enumerate(groups):
        score = _count_alias_matches(group, spec)
        log.debug(
            "select_template_table(%s): group %d matched %d field(s)", spec.template, i, score
        )
        if score > best_score:
            best_score, best_group = score, group

    if best_group is None or best_score < min_matches:
        log.warning(
            "select_template_table(%s): best group matched %d field(s) < threshold %d — no table",
            spec.template,
            best_score,
            min_matches,
        )
        return None
    return best_group
