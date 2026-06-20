"""Domain types: frozen dataclasses + StrEnums (Decimal-based).

The spine of the pipeline — pure types, no business logic, no I/O. Every value
object is immutable (``frozen=True``); monetary/ratio values are ``Decimal`` and
are never coerced to float here. Two *orthogonal* basis axes (ECL phase-in vs
output floor) ride on every :class:`FieldValue` so identities never cross bases
(CLAUDE.md §A C1). :class:`MappingDecision` makes the LLM seam auditable (§A.3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

# --- enumerations ----------------------------------------------------------------


class Jurisdiction(StrEnum):
    EU = "EU"
    UK = "UK"
    US = "US"
    CH = "CH"
    JP = "JP"
    CA = "CA"


class Template(StrEnum):
    """Basel Pillar 3 disclosure templates (extensible)."""

    KM1 = "KM1"
    OV1 = "OV1"
    CMS1 = "CMS1"
    CMS2 = "CMS2"
    CR6 = "CR6"
    CCR1 = "CCR1"
    MR1 = "MR1"
    CVA1 = "CVA1"
    OR1 = "OR1"
    LR1 = "LR1"
    LIQ1 = "LIQ1"


class Unit(StrEnum):
    EUR_M = "EUR_M"
    GBP_M = "GBP_M"
    USD_M = "USD_M"
    CHF_M = "CHF_M"
    JPY_M = "JPY_M"
    PERCENT = "PERCENT"
    RATIO = "RATIO"
    COUNT = "COUNT"
    NONE = "NONE"


class FieldKind(StrEnum):
    """Currency-agnostic field category declared in ``templates/*.yaml``.

    Templates carry a *kind*, not a hard-coded currency unit, so one YAML serves
    every bank; the concrete :class:`Unit` is resolved per bank at mapping time
    via :func:`unit_for` (a GBP filer's MONETARY field is GBP_M, a USD filer's is
    USD_M). PERCENT/RATIO/COUNT are currency-independent.
    """

    MONETARY = "MONETARY"
    PERCENT = "PERCENT"
    RATIO = "RATIO"
    COUNT = "COUNT"


class SourceKind(StrEnum):
    PDF = "PDF"
    XBRL_CSV = "XBRL_CSV"


class Engine(StrEnum):
    DOCLING = "DOCLING"
    CAMELOT = "CAMELOT"
    PDFPLUMBER = "PDFPLUMBER"
    P3DH = "P3DH"
    FFIEC101 = "FFIEC101"


class CheckType(StrEnum):
    CROSS_FOOT = "CROSS_FOOT"
    RATIO_IDENTITY = "RATIO_IDENTITY"
    PERIOD_SANITY = "PERIOD_SANITY"
    UNIT_SANITY = "UNIT_SANITY"
    TWO_ENGINE = "TWO_ENGINE"


class CheckOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


class ValidationStatus(StrEnum):
    AUTO_PASSED = "AUTO_PASSED"
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"
    HUMAN_CORRECTED = "HUMAN_CORRECTED"
    FLAGGED = "FLAGGED"


class EclBasis(StrEnum):
    """IFRS 9 ECL phase-in axis — orthogonal to :class:`FloorBasis`."""

    TRANSITIONAL = "TRANSITIONAL"
    FULLY_LOADED = "FULLY_LOADED"
    NA = "NA"


class FloorBasis(StrEnum):
    """CRR3 output-floor axis — orthogonal to :class:`EclBasis`."""

    FINAL = "FINAL"
    PRE_FLOOR = "PRE_FLOOR"
    NA = "NA"


class MappingMethod(StrEnum):
    RULE = "RULE"
    LLM = "LLM"


# --- kind -> unit resolution -----------------------------------------------------

# ISO 4217 -> the millions-denominated monetary Unit for that currency.
_CURRENCY_UNIT: dict[str, Unit] = {
    "EUR": Unit.EUR_M,
    "GBP": Unit.GBP_M,
    "USD": Unit.USD_M,
    "CHF": Unit.CHF_M,
    "JPY": Unit.JPY_M,
}


def unit_for(kind: FieldKind, currency: str) -> Unit:
    """Resolve a template's :class:`FieldKind` + a bank currency to a concrete :class:`Unit`.

    MONETARY depends on the bank's reporting currency (GBP -> GBP_M, USD -> USD_M);
    PERCENT/RATIO/COUNT are currency-independent. Raises ``ValueError`` on a currency
    with no known monetary unit — an unmapped currency must never silently yield a
    wrong unit (CLAUDE.md §A — no silent failures).
    """
    # ``==`` (not ``is``): FieldKind is a StrEnum, so a plain-string ``kind`` still
    # resolves to the right branch instead of silently falling through to monetary.
    if kind == FieldKind.PERCENT:
        return Unit.PERCENT
    if kind == FieldKind.RATIO:
        return Unit.RATIO
    if kind == FieldKind.COUNT:
        return Unit.COUNT
    try:
        return _CURRENCY_UNIT[currency]
    except KeyError:
        raise ValueError(
            f"no monetary Unit for currency {currency!r} "
            f"(known: {', '.join(sorted(_CURRENCY_UNIT))})"
        ) from None


# --- value objects ---------------------------------------------------------------

_PERIOD_RE = re.compile(r"(\d{4})(Q[1-4]|FY)")  # used with fullmatch: whole-string only


@dataclass(frozen=True)
class ReportingPeriod:
    """A disclosure reporting period. ``quarter=None`` means annual (FY)."""

    year: int
    quarter: int | None

    @property
    def label(self) -> str:
        return f"{self.year}Q{self.quarter}" if self.quarter is not None else f"{self.year}FY"

    @property
    def sort_key(self) -> tuple[int, int]:
        """Orderable key; annual sorts *after* Q4 of the same year."""
        return (self.year, self.quarter if self.quarter is not None else 5)

    @classmethod
    def parse(cls, label: str) -> ReportingPeriod:
        """Parse ``"2025Q4"`` / ``"2025FY"``; raise ``ValueError`` on anything else."""
        match = _PERIOD_RE.fullmatch(label)
        if match is None:
            raise ValueError(f"Invalid reporting period label: {label!r}")
        year, tail = int(match.group(1)), match.group(2)
        return cls(year, None if tail == "FY" else int(tail[1]))


@dataclass(frozen=True)
class Bank:
    id: str
    name: str
    jurisdiction: Jurisdiction
    ir_url: str
    p3dh_lei: str | None
    number_locale: str  # e.g. "en_GB", "de_DE" — drives locale-aware parsing
    reporting_currency: str  # ISO 4217, e.g. "GBP"
    # Stated scale of this filer's *bare* monetary cells ("millions"|"billions"|
    # "thousands"); resolved by mapping.normalise.scale_multiplier and applied so the
    # canonical Unit stays millions. Default "millions" keeps a £m/€m filer untouched;
    # a $bn filer (HSBC) overrides it in banks.yaml. A config dimension, not a code branch.
    monetary_scale: str = "millions"


@dataclass(frozen=True)
class BBox:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class Provenance:
    bank_id: str
    period: ReportingPeriod
    source_url: str
    source_kind: SourceKind
    engine: Engine
    bbox: BBox | None  # None for XBRL/structured sources
    # The disclosure monetary scale actually applied when normalising this figure
    # ("millions"|"billions"|"thousands"). Audit trail for the scale lift: a reader
    # sees raw_text "124.0" + value 124000 + this "billions" and can recompute it.
    # Default "millions" (the no-op lift), so existing provenance is unchanged.
    monetary_scale: str = "millions"


@dataclass(frozen=True)
class MappingDecision:
    """Audit record for *how* a cell became a field (CLAUDE.md §A.3 / M3).

    ``method=RULE`` ⇒ ``model``/``prompt_sha``/``prompt_version`` are ``None``;
    ``method=LLM`` ⇒ all are set so the LLM judgment is fully reproducible.
    """

    method: MappingMethod
    model: str | None
    prompt_sha: str | None
    prompt_version: str | None
    matched_alias: str | None
    confidence: Decimal


@dataclass(frozen=True)
class RawCell:
    """A verbatim extracted cell. ``text`` is never parsed to a number here."""

    row_idx: int
    col_idx: int
    text: str
    bbox: BBox
    engine: Engine
    row_label: str | None
    col_label: str | None


@dataclass(frozen=True)
class FieldValue:
    """A mapped, normalised figure with its provenance and audit trail.

    Carries two orthogonal basis axes; ``raw_text`` is retained for audit;
    ``engine_values`` holds per-engine numbers for post-mapping two-engine
    agreement. The ``dict`` field makes instances non-hashable — that is fine.
    """

    template: Template
    field_code: str
    value: Decimal
    unit: Unit
    ecl_basis: EclBasis
    floor_basis: FloorBasis
    provenance: Provenance
    mapping: MappingDecision
    raw_text: str
    engine_values: dict[Engine, Decimal]


@dataclass(frozen=True)
class CheckResult:
    check_type: CheckType
    outcome: CheckOutcome
    field_codes: tuple[str, ...]
    expected: Decimal | None
    actual: Decimal | None
    tolerance: Decimal | None
    detail: str


@dataclass(frozen=True)
class ReconciliationResult:
    field_value: FieldValue
    checks: tuple[CheckResult, ...]
    confidence: Decimal
    validation_basis: tuple[CheckType, ...]
    status: ValidationStatus


@dataclass(frozen=True)
class DatasetRow:
    """One row of the long-format auditable store (TOOLING.md §2 box 7)."""

    bank: str
    period: str
    jurisdiction: Jurisdiction
    template: Template
    field: str
    value: Decimal
    unit: Unit
    ecl_basis: EclBasis
    floor_basis: FloorBasis
    source_url: str
    page: int | None
    bbox: BBox | None
    confidence: Decimal
    validation_basis: tuple[CheckType, ...]
    status: ValidationStatus
    extracted_at: str
    run_id: str


@dataclass(frozen=True)
class ManifestRow:
    """One row of the ingest registry ledger (TOOLING.md §2 box 2)."""

    bank_id: str
    period: str
    template: Template | None
    url: str
    sha256: str
    source_kind: SourceKind
    local_path: str
    status: str
    fetched_at: str
