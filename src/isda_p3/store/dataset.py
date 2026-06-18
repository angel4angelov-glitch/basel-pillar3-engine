"""Box-7 long-format store (chunk 1.7) — write ReconciliationResults to parquet.

The storage boundary where the audit invariant lives or dies: monetary/ratio
figures and confidences persist as pyarrow **Decimal128**, never float (audit m2).
``rows_to_table`` hands the raw :class:`~decimal.Decimal` straight to a
``decimal128(38, 6)`` array, so no float ever touches a number. If a value carries
more fractional digits than the declared scale, pyarrow refuses to rescale and we
re-raise loud — a silent round would corrupt an audited digit (CLAUDE.md §A).

Precision/scale choice — ``decimal128(38, 6)``:
- 38 digits of total precision dwarf any Basel figure (a G-SIB's RWA is ~1e12 €,
  12 integer digits), leaving vast headroom and never overflowing on real data.
- scale 6 (6 dp) holds ratios/percentages and confidences exactly (KM1 ratios are
  1 dp; confidence is ≤4 dp here) while still forcing a hard failure on anything
  that would need *more* — the boundary is a tripwire, not a rounder.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq

from ..config import Paths
from ..models import (
    Bank,
    DatasetRow,
    ReconciliationResult,
)

# --- schema --------------------------------------------------------------------

# The one type that makes the store auditable: exact decimal, never float.
_DECIMAL = pa.decimal128(38, 6)

#: Explicit pyarrow schema for ``values.parquet``. Every column is declared (no
#: inferred float coercion); ``bbox`` is flattened to nullable float columns and
#: ``page`` to a nullable int so the structured (XBRL, bbox=None) path round-trips.
DATASET_SCHEMA = pa.schema(
    [
        ("bank", pa.string()),
        ("period", pa.string()),
        ("jurisdiction", pa.string()),
        ("template", pa.string()),
        ("field", pa.string()),
        ("value", _DECIMAL),
        ("unit", pa.string()),
        ("ecl_basis", pa.string()),
        ("floor_basis", pa.string()),
        ("source_url", pa.string()),
        ("page", pa.int32()),
        ("bbox_x0", pa.float64()),
        ("bbox_y0", pa.float64()),
        ("bbox_x1", pa.float64()),
        ("bbox_y1", pa.float64()),
        ("confidence", _DECIMAL),
        ("validation_basis", pa.list_(pa.string())),
        ("status", pa.string()),
        ("extracted_at", pa.string()),
        ("run_id", pa.string()),
    ]
)


# --- converter -----------------------------------------------------------------


def to_dataset_row(
    result: ReconciliationResult,
    *,
    bank: Bank,
    run_id: str,
    extracted_at: str,
) -> DatasetRow:
    """Flatten a :class:`ReconciliationResult` to a long-format :class:`DatasetRow`.

    Pure (no I/O). Provenance is read off ``result.field_value.provenance``; ``page``
    comes from the bbox (``None`` for structured/XBRL sources), and the bbox object
    is carried verbatim for column flattening downstream.
    """
    fv = result.field_value
    prov = fv.provenance
    bbox = prov.bbox
    return DatasetRow(
        bank=bank.id,
        period=prov.period.label,
        jurisdiction=bank.jurisdiction,
        template=fv.template,
        field=fv.field_code,
        value=fv.value,
        unit=fv.unit,
        ecl_basis=fv.ecl_basis,
        floor_basis=fv.floor_basis,
        source_url=prov.source_url,
        page=bbox.page if bbox is not None else None,
        bbox=bbox,
        confidence=result.confidence,
        validation_basis=result.validation_basis,
        status=result.status,
        extracted_at=extracted_at,
        run_id=run_id,
    )


# --- table builder -------------------------------------------------------------


def _too_many_dp(values: Sequence[Decimal]) -> list[Decimal]:
    """Decimals pyarrow would reject: non-finite, or >6 significant fractional digits.

    A non-finite Decimal (NaN/Inf/sNaN) is itself an offender (decimal128 has no
    such value); ``is_finite`` guards ``normalize`` so a signalling NaN cannot raise
    and mask the original ``ArrowInvalid``.
    """
    offenders = []
    for v in values:
        if not v.is_finite():
            offenders.append(v)
            continue
        exp = v.normalize().as_tuple().exponent  # normalize() drops trailing zeros
        if -exp > 6:  # finite Decimal => exponent is always int
            offenders.append(v)
    return offenders


def _decimal_column(values: Sequence[Decimal], field_name: str) -> pa.Array:
    """Build a ``decimal128(38, 6)`` array from raw Decimals — no float, no rounding.

    pyarrow raises ``ArrowInvalid`` if a value would lose data on rescale to scale
    6; we re-raise as a clear ``ValueError`` naming the column and the offending
    value(s). A silent quantize here would corrupt an audited digit (CLAUDE.md §A).
    """
    try:
        return pa.array(values, type=_DECIMAL)
    except pa.lib.ArrowInvalid as exc:
        offenders = _too_many_dp(values)
        raise ValueError(
            f"{field_name}: value(s) exceed the dataset scale (decimal128(38, 6)); "
            f"refusing to round silently. Offenders: {offenders or '<unknown>'} ({exc})"
        ) from exc


def rows_to_table(rows: Sequence[DatasetRow]) -> pa.Table:
    """Build a :data:`DATASET_SCHEMA` table from :class:`DatasetRow`s.

    Decimals go straight into ``decimal128`` arrays (never via float). A value
    needing more than scale 6 makes ``_decimal_column`` raise — no silent rounding.
    """
    cols = {
        "bank": [r.bank for r in rows],
        "period": [r.period for r in rows],
        "jurisdiction": [str(r.jurisdiction) for r in rows],
        "template": [str(r.template) for r in rows],
        "field": [r.field for r in rows],
        "value": _decimal_column([r.value for r in rows], "value"),
        "unit": [str(r.unit) for r in rows],
        "ecl_basis": [str(r.ecl_basis) for r in rows],
        "floor_basis": [str(r.floor_basis) for r in rows],
        "source_url": [r.source_url for r in rows],
        "page": [r.page for r in rows],
        "bbox_x0": [r.bbox.x0 if r.bbox is not None else None for r in rows],
        "bbox_y0": [r.bbox.y0 if r.bbox is not None else None for r in rows],
        "bbox_x1": [r.bbox.x1 if r.bbox is not None else None for r in rows],
        "bbox_y1": [r.bbox.y1 if r.bbox is not None else None for r in rows],
        "confidence": _decimal_column([r.confidence for r in rows], "confidence"),
        "validation_basis": [[str(ct) for ct in r.validation_basis] for r in rows],
        "status": [str(r.status) for r in rows],
        "extracted_at": [r.extracted_at for r in rows],
        "run_id": [r.run_id for r in rows],
    }
    # from_pydict assigns columns by NAME against DATASET_SCHEMA — a column added
    # to one but not the other fails loud rather than silently misaligning (the
    # string columns would otherwise swap undetected under positional assembly).
    return pa.Table.from_pydict(cols, schema=DATASET_SCHEMA)


# --- persistence ---------------------------------------------------------------


def append_rows(
    results: Sequence[ReconciliationResult],
    *,
    bank: Bank,
    run_id: str,
    extracted_at: str,
) -> int:
    """Convert ``results`` to rows and append them to ``Paths.DATASET``.

    Idempotent on directories (``Paths.ensure``); additive on rows (reads any
    existing table and concatenates under :data:`DATASET_SCHEMA`). Returns the
    number of rows written this call.
    """
    if not results:
        return 0
    Paths.ensure()
    rows = [
        to_dataset_row(r, bank=bank, run_id=run_id, extracted_at=extracted_at) for r in results
    ]
    table = rows_to_table(rows)
    if Paths.DATASET.exists():
        existing = pq.read_table(Paths.DATASET)
        # Guard, never cast: a cast would silently round a stray float64 column
        # into decimal128 (e.g. 1.1234567 -> 1.123457), the exact corruption this
        # store exists to prevent (CLAUDE.md §A). A schema mismatch is a hard error.
        if not existing.schema.equals(DATASET_SCHEMA):
            raise ValueError(
                f"Existing {Paths.DATASET} schema does not match DATASET_SCHEMA; "
                f"refusing to append (would risk silent type coercion).\n"
                f"on disk: {existing.schema}\nexpected: {DATASET_SCHEMA}"
            )
        table = pa.concat_tables([existing, table])
    pq.write_table(table, Paths.DATASET)
    return len(rows)


def read_dataset() -> pa.Table:
    """Read the full dataset table. Raises ``FileNotFoundError`` if none written yet."""
    if not Paths.DATASET.exists():
        raise FileNotFoundError(
            f"No dataset at {Paths.DATASET}; run the pipeline (append_rows) first."
        )
    return pq.read_table(Paths.DATASET)


def read_dataset_decimals() -> list[dict]:
    """Read the dataset as row dicts with ``value``/``confidence`` as Python ``Decimal``.

    ``Table.to_pylist`` already decodes ``decimal128`` columns to ``Decimal`` (no
    float), so consumers/assertions get exact values back.
    """
    return read_dataset().to_pylist()
