# isda-p3 — Basel Pillar 3 extraction & benchmarking engine

An **auditable, AI-enabled Python pipeline** that discovers banks' Basel **Pillar 3**
regulatory-capital disclosures, extracts the quantitative regulatory tables, **reconciles every
figure against arithmetic identities**, and emits a structured, fully-lineaged dataset for peer
comparison and trend analysis. The graded criteria are **accuracy, auditability, scalability**.

> **The one law:** the LLM is judgment glue, never the ledger. Numbers come from deterministic
> extraction + arithmetic reconciliation; the LLM is restricted to classification, row-label mapping,
> and unit normalisation — it *never* produces a value that enters the dataset. Every emitted figure
> carries provenance (bank → period → page → cell bbox → url) and must pass a reconciliation check
> before storage. Every LLM mapping decision is logged (model, prompt hash, version, confidence).

Status: working **proof-of-concept** (KM1 + OV1 templates, both ingestion paths). Architecture is
extensible to the full Basel disclosure framework. See [TOOLING.md](TOOLING.md) for the design.

---

## Architecture — a 7-stage pipeline

| Stage | Function |
|------|----------|
| **1 · Discovery** | Structured-first (EU EBA Pillar 3 Data Hub XBRL-CSV, US FFIEC 101); content-based period resolver picks the *latest* report from document content, not filename/URL. |
| **2 · Ingest / registry** | Content-addressed (SHA-256), immutable raw store, idempotent — restatements kept as new versions; manifest audit ledger. |
| **3 · Extraction** | Two independent deterministic engines — **Docling/TableFormer** + adaptive **Camelot (lattice→stream)→pdfplumber** — each emitting cells with top-left bounding-box provenance. |
| **4 · Mapping** | Rule-first row-label matching to a canonical schema; bounded LLM fallback (chooses the *row*, never the digit); currency-agnostic field kinds; locale-aware number normalisation. |
| **5 · Reconciliation** | Basis-pinned ratio identities (CET1 % = CET1 / RWA), basis-aware cross-foots, unit/period sanity, two-engine agreement → weighted-product confidence → auto-accept (≥0.95) or human queue. |
| **6 · Human review** | Low-confidence figures triaged *beside their verbatim source cell*, both engines' values + the failing check; confirm/correct with full audit trail. |
| **7 · Store & analytics** | Long-format dataset (pyarrow `Decimal128`, no float coercion); peer comparison, trend analysis, Excel export, Docling-vs-Camelot accuracy benchmark with bootstrap CI. |

### The accuracy firewall
- **No silently-wrong numbers.** Locale parsing is hard-tested (`368.000` DE = 368,000; `(1,234)` =
  −1,234; `13.6¹` → 13.6 but `13.61` stays 13.61). A ×1000 misread that could *satisfy* a ratio is the
  top silent-error vector — so it fails loud, never guesses.
- **Consistency ≠ correctness, stated honestly.** Reconciliation proves internal consistency; external
  accuracy is asserted only against a hand-verified golden set. An unvalidated figure is never
  auto-accepted — it routes to review.
- **Basis discipline.** Transitional vs fully-loaded (ECL) and final vs pre-floor (output floor) are
  modelled as orthogonal axes; identities refuse to pair across bases.

---

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # add your ANTHROPIC_API_KEY (only needed for the bounded LLM mapper)

ruff check .                  # lint
pytest -q                     # 492 tests; integration tests are opt-in: -m integration
```

### CLI
```bash
# extract one bank's KM1 from a PDF → reconcile → auditable dataset row
isda-p3 run --bank barclays --template KM1 --period 2025Q4 --pdf path/to/disclosure.pdf

# batch across banks (config-driven: new bank = config, not code)
isda-p3 run-all --template KM1 --period 2025Q4 --pdf-dir path/to/pdfs/

# triage low-confidence figures beside their source cell
isda-p3 review list
isda-p3 review resolve --run <id> --field KM1.5 --correct 13.8
```

---

## Engineering

Test-driven, delivered in 22 vertical milestones (failing test → minimal code → independent
quality + silent-failure review → printed verification gate → commit). Immutability-first (frozen
dataclasses, `StrEnum`s, pure functions, dependency injection — the core suite runs with no PDF, LLM,
or API key). Fail-loud at every boundary; reconciliation-core coverage gated ≥90% in CI.

**Stack:** Python 3.11 · Docling/TableFormer · Camelot · pdfplumber · pyarrow/Parquet · pydantic ·
Anthropic Claude (bounded mapping only) · pytest · ruff · GitHub Actions.

## License

MIT — see [LICENSE](LICENSE).
