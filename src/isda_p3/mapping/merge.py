"""Two-engine value merge (chunk 2.2) — fold a secondary engine's numbers into the
primary FieldValues for post-mapping agreement.

The two extraction engines segment grids differently, so we cross-check the FINAL
mapped values by ``field_code`` (post-mapping), never by raw cell position. Docling
(the primary) stays canonical: its value, provenance, and basis axes are
authoritative; the secondary engine only contributes a cross-check number into
``engine_values`` so :func:`isda_p3.reconcile.checks.two_engine_agreement` can flag a
disagreement (audit M2).

Pure and deterministic — no I/O, no extraction. Coverage gaps (a ``field_code`` one
engine saw and the other missed) are LOGGED as a signal, never silently merged or
fabricated: the canonical set is exactly the primary (CLAUDE.md §A.2).
"""

from __future__ import annotations

import dataclasses
import logging

from ..models import FieldValue

log = logging.getLogger(__name__)


def merge_engine_values(
    primary: list[FieldValue], secondary: list[FieldValue]
) -> list[FieldValue]:
    """Augment each primary FieldValue with the secondary engine's value for the same code.

    For every primary field, if a secondary field shares its ``field_code``, add the
    secondary engine's number to ``engine_values``
    (``{**primary.engine_values, secondary.provenance.engine: secondary.value}``) via
    :func:`dataclasses.replace`. Canonical value/provenance/basis stay the primary's.

    Secondary-only ``field_code``s (the primary missed them) and primary-only codes
    (no secondary cross-check) are logged as a coverage signal; the returned list is
    one-to-one with ``primary`` — the secondary engine never extends the canonical set.
    """
    # last-wins on a duplicate code: both are the same engine's opinion of one field,
    # and this is a cross-check value, never a canonical number.
    secondary_by_code = {fv.field_code: fv for fv in secondary}

    primary_codes = {fv.field_code for fv in primary}
    secondary_only = sorted(secondary_by_code.keys() - primary_codes)
    primary_only = sorted(primary_codes - secondary_by_code.keys())
    if secondary_only:
        log.info(
            "merge_engine_values: %d field(s) seen only by the secondary engine "
            "(not merged — primary is canonical): %s",
            len(secondary_only),
            ", ".join(secondary_only),
        )
    # Only a coverage signal when a secondary engine actually ran: with no secondary at
    # all (the single-engine path) every field is trivially "primary-only", which is not
    # an anomaly — logging it would bury the real gap (secondary ran but missed a field).
    if secondary and primary_only:
        log.info(
            "merge_engine_values: %d primary field(s) the secondary engine missed "
            "(two-engine will SKIP): %s",
            len(primary_only),
            ", ".join(primary_only),
        )

    merged: list[FieldValue] = []
    for fv in primary:
        sec = secondary_by_code.get(fv.field_code)
        if sec is None:
            merged.append(fv)
            continue
        merged.append(
            dataclasses.replace(
                fv,
                engine_values={**fv.engine_values, sec.provenance.engine: sec.value},
            )
        )
    return merged
