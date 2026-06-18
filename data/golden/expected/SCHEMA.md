# Golden expected-values schema

Hand-verified ground truth for the golden-regression tests. One YAML file per
(bank, period, template). Extraction is deterministic, so these are a **regression
baseline**, not a `pass^k` set (per the plan: `pass^k=100%` applies only to the
LLM-mapping step). The synthetic file in this directory drives the key-free e2e
gate; real-bank files (dropped in beside a PDF under `../pdfs/`) drive the opt-in
`-m integration` test.

## File shape

```yaml
bank: barclays            # bank id (must exist in config/banks.yaml for real banks)
period: 2025Q4            # ReportingPeriod label: <YYY>Q<1-4> or <YYYY>FY
template: KM1             # Template code
values:
  KM1.1:                  # canonical field code (from config/templates/<t>.yaml)
    value: "48000"        # EXACT Decimal as a STRING (never a YAML float — see below)
    unit: GBP_M           # models.Unit member
    ecl_basis: TRANSITIONAL   # models.EclBasis: TRANSITIONAL | FULLY_LOADED | NA
    floor_basis: NA           # models.FloorBasis: FINAL | PRE_FLOOR | NA
  KM1.5:
    value: "15.0"
    unit: PERCENT
    ecl_basis: NA
    floor_basis: FINAL
  # ... one entry per field the golden asserts
```

### Why `value` is a quoted string

Decimal exactness is the whole point of this project (CLAUDE.md §A — the LLM is
never the ledger; no number without provenance + a passed check). A bare YAML
`13.6` is parsed as a binary **float** and can drift to `13.600000000001`; quoting
it (`"13.6"`) keeps it a string that the test feeds straight to `Decimal(...)`. The
parquet store persists values as `decimal128`, so the golden must compare as
`Decimal`, never float.

## REQUIRED before a value becomes "truth": the two-pass basis check (plan m5)

A KM1 row exists on **two orthogonal basis axes**, and a value extracted under the
wrong basis will *silently satisfy* a ratio identity (numerator and denominator
move together). So a human MUST verify, twice, before committing a golden value:

1. **ECL phase-in axis (`ecl_basis`).** Is the capital figure **transitional**
   (IFRS 9 relief still phasing in — KM1 rows 1/2/3) or **fully-loaded** (row
   1a/2a/3a)? Confirm against the row label in the source PDF, not the position.
2. **Output-floor axis (`floor_basis`).** Is RWA / the ratio **final**
   (post-output-floor — rows 4/5) or **pre-floor** (rows 4a/5b)? CRR3 phases the
   floor in; mixing a pre-floor RWA into a final ratio is the classic cross-basis
   trap (CLAUDE.md §A C1).

Record the basis you verified in `ecl_basis` / `floor_basis`. The reconciliation
engine refuses to pair operands across bases (`CrossBasisError`), so a mis-tagged
golden fails loud at check time rather than passing on wrong inputs — but the
human pass is the first line of defence. Note both axes in the commit message when
adding a real bank.
