"""Box-7 analytics (chunk 4.1) — peer comparison + trend over the dataset.

Two read-only benchmarking views over ``data/dataset/values.parquet``:

- :func:`peer_compare` — all banks' value for one field in one period.
- :func:`trend` — one bank's value for one field across periods.

Both read the dataset once via :func:`store.dataset.read_dataset_decimals` (which
decodes ``decimal128`` columns straight back to :class:`~decimal.Decimal` — no
float is ever introduced) and filter in memory. Every returned row carries its
provenance (``source_url`` + ``page``) and its :class:`ValidationStatus`, so a
FLAGGED figure can never be silently mistaken for a validated one (CLAUDE.md §A).
A missing dataset raises (via ``read_dataset_decimals``) rather than returning a
misleading empty list — "no file" and "no matching rows" are distinct outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..models import ReportingPeriod, Unit, ValidationStatus
from .dataset import read_dataset_decimals

# Human-signed-off + auto-passed statuses; FLAGGED is EXCLUDED by default. Callers
# may opt FLAGGED in, and because ``status`` rides on every returned row it stays
# visible and is never mistaken for a validated figure (CLAUDE.md §A).
_VALIDATED: tuple[ValidationStatus, ...] = (
    ValidationStatus.AUTO_PASSED,
    ValidationStatus.HUMAN_CONFIRMED,
    ValidationStatus.HUMAN_CORRECTED,
)


@dataclass(frozen=True)
class PeerRow:
    """One bank's value for a field in a single period, with provenance + status."""

    bank: str
    value: Decimal
    unit: Unit
    source_url: str
    page: int | None
    confidence: Decimal
    status: ValidationStatus


@dataclass(frozen=True)
class TrendPoint:
    """One period's value for a bank+field, with provenance + status.

    ``delta`` is the period-over-period change (this value minus the previous
    point's value, same unit), exact :class:`~decimal.Decimal`; ``None`` on the
    first point (no prior period to difference against).
    """

    period: str
    value: Decimal
    unit: Unit
    source_url: str
    page: int | None
    confidence: Decimal
    status: ValidationStatus
    delta: Decimal | None


def _selected(records: list[dict], statuses: tuple[ValidationStatus, ...]) -> list[dict]:
    """Rows whose stored status string is one of ``statuses`` (compared as strings).

    Stored ``status`` is a plain string (parquet); comparing against the StrEnum
    *values* keeps the filter explicit and order-independent. An *unrecognised*
    status (not a known :class:`ValidationStatus` at all) is a corrupt audit ledger,
    not a row to be silently dropped — it raises, so it can never masquerade as
    "no matching rows" (CLAUDE.md §A — no silent failures).
    """
    known = {str(s) for s in ValidationStatus}
    for r in records:
        if r["status"] not in known:
            raise ValueError(
                f"Unrecognised status {r['status']!r} in dataset row "
                f"bank={r['bank']!r} field={r['field']!r} period={r['period']!r}; "
                f"the audit ledger is corrupt — refusing to silently drop it."
            )
    wanted = {str(s) for s in statuses}
    return [r for r in records if r["status"] in wanted]


def peer_compare(
    field_code: str,
    period: str,
    *,
    statuses: tuple[ValidationStatus, ...] = _VALIDATED,
) -> list[PeerRow]:
    """All banks' value for ``field_code`` in ``period``, sorted by value descending.

    Ties break on ``bank`` id ascending (a documented, stable order). FLAGGED rows
    are excluded unless explicitly included via ``statuses``; when included, each
    row's ``status`` marks it so. Returns an empty list when no row matches (a
    clear, non-error outcome); raises if the dataset file is absent.
    """
    records = read_dataset_decimals()
    matched = [
        r
        for r in _selected(records, statuses)
        if r["field"] == field_code and r["period"] == period
    ]
    rows = [
        PeerRow(
            bank=r["bank"],
            value=r["value"],
            unit=Unit(r["unit"]),
            source_url=r["source_url"],
            page=r["page"],
            confidence=r["confidence"],
            status=ValidationStatus(r["status"]),
        )
        for r in matched
    ]
    # value descending, bank id ascending tie-break. ``-Decimal`` is exact (no float).
    rows.sort(key=lambda row: (-row.value, row.bank))
    return rows


def trend(
    bank_id: str,
    field_code: str,
    *,
    statuses: tuple[ValidationStatus, ...] = _VALIDATED,
) -> list[TrendPoint]:
    """One bank's ``field_code`` across periods, ordered by reporting period ascending.

    Period order comes from :attr:`ReportingPeriod.sort_key` (parsed from the stored
    label), so ``2025Q1 < … < 2025Q4 < 2025FY`` regardless of insertion order. Each
    point after the first carries ``delta`` = value − previous value (exact Decimal).
    Returns an empty list when no row matches; raises if the dataset file is absent.
    """
    records = read_dataset_decimals()
    matched = [
        r
        for r in _selected(records, statuses)
        if r["bank"] == bank_id and r["field"] == field_code
    ]
    matched.sort(key=lambda r: ReportingPeriod.parse(r["period"]).sort_key)

    points: list[TrendPoint] = []
    prev: Decimal | None = None
    for r in matched:
        value = r["value"]
        delta = None if prev is None else value - prev
        points.append(
            TrendPoint(
                period=r["period"],
                value=value,
                unit=Unit(r["unit"]),
                source_url=r["source_url"],
                page=r["page"],
                confidence=r["confidence"],
                status=ValidationStatus(r["status"]),
                delta=delta,
            )
        )
        prev = value
    return points
