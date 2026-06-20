"""Golden-set accuracy comparator + frozen extracted-cells fixture (chunk 5.1).

Two jobs, both serving the one number that matters — *measured* extraction accuracy:

1. :func:`compare_to_golden` — measure the deterministic stack against a
   human-verified golden, INDEPENDENT of the extraction (CLAUDE.md §A.1/§A.3). The
   comparison is **Decimal-exact**: a cell is CORRECT iff its extracted Decimal
   *numerically equals* the golden Decimal. Trailing zeros are equal (``15 == 15.0``),
   but ``13.6 != 13.60001`` — a misread, however small, is a miss, never absorbed by
   a tolerance. No float ever touches a figure. The empty-extraction trap is loud:
   0 extracted of N golden is ``0/N``, never "100% of 0" (an empty golden raises).

2. :class:`Fixture` (+ :func:`fixture_from_fieldvalues` / :func:`load_fixture`) — the
   small, legal, PDF-free artifact committed so an integration test reproduces both
   the accuracy comparison *and* the reconciliation on the real extracted digits
   without the copyrighted PDF or any API key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Iterable, Mapping

import yaml

from .models import (
    BBox,
    EclBasis,
    Engine,
    FieldValue,
    FloorBasis,
    MappingDecision,
    MappingMethod,
    Provenance,
    ReportingPeriod,
    SourceKind,
    Template,
    Unit,
)

# --- comparator ------------------------------------------------------------------


class CellOutcome(StrEnum):
    CORRECT = "CORRECT"  # both present, Decimal-equal
    MISMATCH = "MISMATCH"  # both present, values differ
    MISSING = "MISSING"  # in golden, not extracted (counts against accuracy)
    EXTRA = "EXTRA"  # extracted, not in golden (reported, not in denominator)


@dataclass(frozen=True)
class CellComparison:
    field_code: str
    outcome: CellOutcome
    golden: Decimal | None
    extracted: Decimal | None


@dataclass(frozen=True)
class AccuracyReport:
    """One measured comparison. ``accuracy`` is a Decimal — correct / golden-cells."""

    comparisons: tuple[CellComparison, ...]

    def _by(self, outcome: CellOutcome) -> tuple[CellComparison, ...]:
        return tuple(c for c in self.comparisons if c.outcome is outcome)

    @property
    def correct(self) -> tuple[CellComparison, ...]:
        return self._by(CellOutcome.CORRECT)

    @property
    def mismatches(self) -> tuple[CellComparison, ...]:
        return self._by(CellOutcome.MISMATCH)

    @property
    def missing(self) -> tuple[CellComparison, ...]:
        return self._by(CellOutcome.MISSING)

    @property
    def extras(self) -> tuple[CellComparison, ...]:
        return self._by(CellOutcome.EXTRA)

    @property
    def n_correct(self) -> int:
        return len(self.correct)

    @property
    def n_golden(self) -> int:
        """Golden-cell count = correct + mismatch + missing (extras are not golden)."""
        return len(self.correct) + len(self.mismatches) + len(self.missing)

    @property
    def accuracy(self) -> Decimal:
        """``n_correct / n_golden`` as a Decimal. ``n_golden`` is always > 0 here
        (an empty golden is rejected in :func:`compare_to_golden`), so this never
        divides by zero and can never report "100% of 0"."""
        return Decimal(self.n_correct) / Decimal(self.n_golden)


def compare_to_golden(
    golden: Mapping[str, Decimal], extracted: Mapping[str, Decimal]
) -> AccuracyReport:
    """Compare extracted Decimals to a golden set, Decimal-exact, per ``field_code``.

    Raises ``ValueError`` if ``golden`` is empty — measuring accuracy against nothing
    would yield a meaningless "100% of 0" (CLAUDE.md §A.2, fail loud on the empty trap).
    Every golden code becomes CORRECT / MISMATCH / MISSING; every extracted code not in
    golden becomes EXTRA (surfaced, never silently dropped).
    """
    if not golden:
        raise ValueError("golden set is empty — cannot measure accuracy (would be 100% of 0)")

    comparisons: list[CellComparison] = []
    for code in golden:
        g = golden[code]
        if code not in extracted:
            comparisons.append(CellComparison(code, CellOutcome.MISSING, g, None))
        elif extracted[code] == g:  # Decimal numeric equality: 15 == 15.0, 13.6 != 13.60001
            comparisons.append(CellComparison(code, CellOutcome.CORRECT, g, extracted[code]))
        else:
            comparisons.append(CellComparison(code, CellOutcome.MISMATCH, g, extracted[code]))

    for code in extracted:
        if code not in golden:
            comparisons.append(CellComparison(code, CellOutcome.EXTRA, None, extracted[code]))

    return AccuracyReport(tuple(comparisons))


# --- golden loader ---------------------------------------------------------------


@dataclass(frozen=True)
class Golden:
    """A parsed golden file: identity + the Decimal values the comparator measures."""

    bank: str
    period: str
    template: str
    values: Mapping[str, Decimal]


def load_golden(path: Path) -> Golden:
    """Parse a golden YAML (SCHEMA.md shape) into exact Decimals.

    Each ``values[code].value`` is a quoted string fed straight to ``Decimal`` (never a
    bare YAML float, which would drift). Fails loud on an empty ``values`` block or a
    value that is not a parseable Decimal — a malformed golden must never silently
    become a flattering measurement.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a top-level mapping")
    values_raw = raw.get("values")
    if not isinstance(values_raw, dict) or not values_raw:
        raise ValueError(f"{path}: 'values' must be a non-empty mapping")

    values: dict[str, Decimal] = {}
    for code, entry in values_raw.items():
        if not isinstance(entry, dict) or "value" not in entry:
            raise ValueError(f"{path}: {code} is missing 'value'")
        val = entry["value"]
        # Enforce the SCHEMA.md contract: a quoted string, never a bare YAML number.
        # yaml.safe_load turns an unquoted 13.60 into a float that has ALREADY dropped
        # its trailing zero — refuse it rather than admit a silently-rounded figure (§A.2).
        if not isinstance(val, str):
            raise ValueError(
                f"{path}: {code} value {val!r} must be a quoted string, not a bare YAML "
                f"{type(val).__name__} (bare numbers lose precision through float)"
            )
        try:
            values[code] = Decimal(val)
        except Exception as exc:  # noqa: BLE001 — any unparseable value is fatal, named
            raise ValueError(f"{path}: {code} value {val!r} is not a Decimal") from exc

    # Identity header fields must be real strings: ``str(None)`` would silently stamp a
    # malformed golden as bank/period/template "None" and corrupt the frozen fixture (§A.2).
    bank, period, template = raw.get("bank"), raw.get("period"), raw.get("template")
    for name, field in (("bank", bank), ("period", period), ("template", template)):
        if not isinstance(field, str) or not field.strip():
            raise ValueError(f"{path}: missing or non-string {name!r}")

    return Golden(bank=bank, period=period, template=template, values=values)


# --- frozen extracted-cells fixture (PDF-free CI reproduction) -------------------


@dataclass(frozen=True)
class ExtractedCell:
    """One extracted figure, flattened for JSON with its real per-cell provenance."""

    field_code: str
    value: Decimal
    unit: Unit
    ecl_basis: EclBasis
    floor_basis: FloorBasis
    raw_text: str
    page: int
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class Fixture:
    """The committed extracted-cells artifact: identity + the cells, engine-stamped."""

    bank: str
    period: str
    template: str
    source_url: str
    sha256: str
    engine: Engine
    cells: tuple[ExtractedCell, ...]

    @property
    def values(self) -> dict[str, Decimal]:
        """``{field_code: Decimal}`` — the comparator's view of this extraction."""
        return {c.field_code: c.value for c in self.cells}

    def to_fieldvalues(self, *, bank_id: str) -> dict[str, FieldValue]:
        """Rebuild reconcile-ready :class:`FieldValue`s from the frozen cells.

        Faithful to what the extractor produced (value, unit, both basis axes, real
        page+bbox, engine), so re-running ``reconcile_template`` on the result re-checks
        the identities on the *real* digits with no PDF and no API key.
        """
        period = ReportingPeriod.parse(self.period)
        template = Template(self.template)
        engine = self.engine
        out: dict[str, FieldValue] = {}
        for c in self.cells:
            x0, y0, x1, y1 = c.bbox
            out[c.field_code] = FieldValue(
                template=template,
                field_code=c.field_code,
                value=c.value,
                unit=c.unit,
                ecl_basis=c.ecl_basis,
                floor_basis=c.floor_basis,
                provenance=Provenance(
                    bank_id=bank_id,
                    period=period,
                    source_url=self.source_url,
                    source_kind=SourceKind.PDF,
                    engine=engine,
                    bbox=BBox(page=c.page, x0=x0, y0=y0, x1=x1, y1=y1),
                ),
                mapping=MappingDecision(
                    method=MappingMethod.RULE,
                    model=None,
                    prompt_sha=None,
                    prompt_version=None,
                    matched_alias=None,
                    confidence=Decimal("1"),
                ),
                raw_text=c.raw_text,
                engine_values={engine: c.value},
            )
        return out


def fixture_from_fieldvalues(
    fvs: Iterable[FieldValue],
    *,
    bank: str,
    period: str,
    template: str,
    source_url: str,
    sha256: str,
) -> Fixture:
    """Freeze a run's :class:`FieldValue`s into a :class:`Fixture` (engine from the cells)."""
    cells: list[ExtractedCell] = []
    engine: Engine | None = None
    for fv in fvs:
        engine = fv.provenance.engine
        b = fv.provenance.bbox
        if b is None:
            raise ValueError(f"{fv.field_code}: cannot freeze a FieldValue without a bbox")
        cells.append(
            ExtractedCell(
                field_code=fv.field_code,
                value=fv.value,
                unit=fv.unit,
                ecl_basis=fv.ecl_basis,
                floor_basis=fv.floor_basis,
                raw_text=fv.raw_text,
                page=b.page,
                bbox=(b.x0, b.y0, b.x1, b.y1),
            )
        )
    if engine is None:
        raise ValueError("no FieldValues to freeze")
    return Fixture(bank, period, template, source_url, sha256, engine, tuple(cells))


def write_fixture(fixture: Fixture, path: Path) -> None:
    """Write a :class:`Fixture` to JSON — Decimals as strings (exactness preserved)."""
    payload = {
        "bank": fixture.bank,
        "period": fixture.period,
        "template": fixture.template,
        "source_url": fixture.source_url,
        "sha256": fixture.sha256,
        "engine": fixture.engine.value,
        "cells": [
            {
                "field_code": c.field_code,
                "value": str(c.value),
                "unit": c.unit.value,
                "ecl_basis": c.ecl_basis.value,
                "floor_basis": c.floor_basis.value,
                "raw_text": c.raw_text,
                "page": c.page,
                "bbox": [c.bbox[0], c.bbox[1], c.bbox[2], c.bbox[3]],
            }
            for c in fixture.cells
        ],
    }
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_fixture(path: Path) -> Fixture:
    """Inverse of :func:`write_fixture`; every numeric value re-parsed as Decimal."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    cells = tuple(
        ExtractedCell(
            field_code=c["field_code"],
            value=Decimal(c["value"]),
            unit=Unit(c["unit"]),
            ecl_basis=EclBasis(c["ecl_basis"]),
            floor_basis=FloorBasis(c["floor_basis"]),
            raw_text=c["raw_text"],
            page=int(c["page"]),
            bbox=(float(c["bbox"][0]), float(c["bbox"][1]), float(c["bbox"][2]), float(c["bbox"][3])),
        )
        for c in raw["cells"]
    )
    return Fixture(
        bank=raw["bank"],
        period=raw["period"],
        template=raw["template"],
        source_url=raw["source_url"],
        sha256=raw["sha256"],
        engine=Engine(raw["engine"]),
        cells=cells,
    )
