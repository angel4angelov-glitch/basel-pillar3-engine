# Growing the golden set to ≥5 banks

The golden set is the project's only claim to **external accuracy**. Reconciliation
proves *internal consistency* (cross-foot, ratio identity, two-engine agreement) — it
does **not** prove a digit matches the bank's published figure (audit C3). Only a
human comparing the extracted value against the source PDF does that. The golden set is
that comparison, frozen as a regression baseline.

This is a **manual** process. It is the one place in the pipeline where a person, not
code, must do the work — and the honest framing for the interview is: the benchmark CI
width (chunk 4.2) is a function of how many banks are in this set. Two banks → a
uselessly wide bootstrap interval. **Do not fabricate golden data to narrow it.** Report
the real N and the real width.

## Why ≥5

The Docling-vs-Camelot exact-match benchmark (`isda-p3 benchmark`, chunk 4.2) bootstraps a
confidence interval over the per-field exact-match rate across the golden corpus. With
N=2 banks the CI spans almost the whole `[0, 1]` range and says nothing. Five hand-verified
banks across ≥2 jurisdictions is the minimum for a defensible slide number; more is better.
The corpus is the statistic's sample size — state it plainly wherever the number appears.

## The procedure (per bank)

For each bank you add (target: ≥5, spread across UK / EU / US so locale + layout vary):

### 1. Obtain the real PDF

Download the bank's latest Pillar 3 KM1/OV1 disclosure (native-text PDF, not a scan —
the scanned/OCR path is post-demo). Save it under:

```
data/golden/pdfs/<bank_id>_<period>.pdf      # e.g. barclays_2025Q4.pdf
```

`<bank_id>` MUST already exist in `config/banks.yaml` (a real bank is a config entry —
CLAUDE.md §A.5). If it is not there yet, add the entry first (`id, name, jurisdiction,
ir_url, p3dh_lei: null, number_locale, reporting_currency`). Mind the currency traps —
HSBC, Standard Chartered and UBS report in USD, not their home currency — and set
`number_locale` to the bank's actual format (e.g. `de_DE` for Deutsche Bank: a `48.000`
there is *48 thousand*, not 48.0). A wrong locale is the top silent-error vector (audit C2).

### 2. Hand-verify each value with the two-pass basis check (plan m5)

This is the load-bearing step. A KM1 row sits on **two orthogonal basis axes**, and a value
read under the wrong basis will *silently satisfy* a ratio identity (numerator and
denominator move together). For every value before you write it down, confirm **both** axes
against the row label in the PDF — never the row position:

1. **ECL phase-in (`ecl_basis`)** — is the capital figure **transitional** (IFRS 9 relief
   still phasing in; KM1 rows 1/2/3) or **fully-loaded** (rows 1a/2a/3a)?
2. **Output floor (`floor_basis`)** — is the RWA / ratio **final** (post-output-floor; rows
   4/5) or **pre-floor** (rows 4a/5b)? CRR3 phases the floor in; folding a pre-floor RWA into
   a final ratio is the classic cross-basis trap (audit C1).

The reconciliation engine refuses to pair operands across bases (`CrossBasisError`), so a
mis-tagged golden fails loud at check time rather than passing on wrong inputs — but the
human pass is the first line of defence, not the last. Note both axes you verified in the
commit message.

Read the exact `value` off the page as a string (keep every significant digit; do not round),
and record the `unit`, `ecl_basis`, and `floor_basis` you confirmed.

### 3. Write `data/golden/expected/<bank>_<period>.yaml`

One YAML file per (bank, period, template), in the shape pinned by
[data/golden/expected/SCHEMA.md](../data/golden/expected/SCHEMA.md). `value` is a **quoted
string** so it stays an exact `Decimal` and never a drifting YAML float:

```yaml
bank: barclays
period: 2025Q4
template: KM1
values:
  KM1.1:
    value: "48217"          # exact, off the page, as a string
    unit: GBP_M
    ecl_basis: TRANSITIONAL
    floor_basis: NA
  KM1.5:
    value: "13.6"
    unit: PERCENT
    ecl_basis: NA
    floor_basis: FINAL
  # ... one entry per field you hand-verified
```

### 4. Confirm it reconciles, then commit

Run the real-PDF path opt-in test and the benchmark over the grown corpus:

```bash
isda-p3 run --bank <bank_id> --template KM1 --period <period> \
  --pdf data/golden/pdfs/<bank_id>_<period>.pdf --no-store
pytest -q -m integration          # golden-regression on the real PDFs
isda-p3 benchmark --engines docling camelot --golden data/golden/
```

The `run` output's ratio-identity check must PASS against *your* hand-verified values, and
the benchmark's CI should narrow as N grows. Commit the PDF, the expected YAML, and any new
`banks.yaml` / row-label-alias entries together, noting the two-pass basis check in the message.

## What stays code, not config (audit M4)

Adding a bank is config **iff** its PDF parses with an existing engine and its row labels
match known aliases. A genuinely novel layout (a new extraction engine, a structurally
different table) is a **code** change, not a config entry — state that boundary, don't hide
it. The honest claim is "new bank = new `banks.yaml` entry + possibly new row-label aliases,"
not "new bank = zero code, always."
