# TOOLING.md — ISDA Pillar 3 Disclosure Extraction & Benchmarking

Operating manual for this project. What we are building, the architecture, and the
exact tooling (skills, agents, MCP, reusable code) wired to each component. Read this
before touching code; update it when the design moves.

> Note: this repo has no Karpathy/CLAUDE.md to inherit from. Global working rules live in
> the user's `angel4angelov-glitch/claude-config` repo under `rules/*.md` (immutability-first,
> small files, no silent failures, validate at boundaries, 80% test coverage). They apply here.

---

## 0. The problem (one paragraph)

Banks across jurisdictions publish periodic Basel **Pillar 3** public disclosures — dispersed
across bank websites, varying in format, structure, and language. We build an AI-enabled system
that (1) auto-discovers the *latest* disclosure report per bank, (2) extracts selected
quantitative data points from specified regulatory tables (KM1, OV1, CCR, MR, CVA, LR…),
(3) validates/reconciles every figure, and (4) emits a structured, auditable dataset for peer
comparison, trend analysis, and benchmarking. **Accuracy, auditability, scalability are the
graded criteria** — everything below serves those three words.

---

## 1. The one law that decides the architecture

**The LLM is judgment glue, never the ledger.**

- Digits come from **deterministic extraction** (pdfplumber/Camelot cell strings) + **validation**.
- The LLM does what it is good at: classify a table as KM1 vs OV1, map messy rows to canonical
  fields, normalise units/scale, disambiguate footnotes. It must **never be the source of a number.**
- Every extracted value carries provenance to its source cell and passes a reconciliation check
  before it is allowed into the dataset. No citation + no check = not emitted.

Anyone who proposes "dump the PDF into an LLM and ask for the numbers" has failed. This law is the
headline of the interview slide.

---

## 1b. Research-locked decisions (cited, 2026-06-17)

Two findings reshape the build. Both are evidenced in `research/` briefings.

**D1 — Structured-first, PDF-fallback. (Don't parse PDFs you don't have to.)**
- **EBA Pillar 3 Data Hub (P3DH)** is live **Jan 2026**: EU large & other institutions' disclosures centralised as **machine-readable XBRL-CSV**, publicly downloadable. Source: bis/eba.
- **US FFIEC 101** (Schedules A–S) is a machine-readable regulatory filing carrying capital/RWA data.
- So: **EU + US = ingest structured data directly** (no extraction risk). **UK / Switzerland / Japan + historical periods = PDF-only → run the extraction pipeline.** A strong solution is a *hybrid*: structured connector where it exists, PDF extraction where it doesn't. Mention P3DH in the interview — it shows current awareness.

**D2 — Target FIXED templates; forward-map to the BCBS cell-ID scheme.**
- 82 Pillar 3 templates exist; the **fixed/flexible flag is in BCBS d604 Annex 2**. Fixed templates have stable, non-renumberable row/col refs = the machine-comparable leverage point.
- **Benchmarking sweet spot (all fixed):** `KM1`, `OV1`, `CMS1/CMS2` (modelled-vs-standardised RWA = output-floor bite), `CR6` (IRB PD/LGD/RWA-density), `CCR1/3/4/7/8`, `MR1/2/3`, `CVA1–4`, `OR1–3`, `LR1/2`, `LIQ1/2`. Exact KM1/OV1 row+column labels are captured in the taxonomy briefing.
- **BCBS d604 (Dec 2025)** proposes a global machine-readable taxonomy: a Data Structure Definition giving every cell a unique ID (e.g. `KM1.Capital.AvailableCapital.Current.CET1.T`). Map our canonical schema to this → forward-compatible. EU **EBA DPM/XBRL taxonomy** is the usable cell→ID map *today*.
- Caveat: CRR3 output-floor rows + per-tier cadence must be confirmed against the **2024/3172 EBA ITS** before coding (flagged, not assumed).

**D3 — Extraction stack (when PDF is unavoidable).**
- **Primary (native-text):** **Docling/TableFormer** (MIT; 96.8% FinTabNet TEDS, handles spanning headers) + **Camelot lattice** as a 2nd independent deterministic engine.
- **Fallback (scanned):** **Azure Document Intelligence** (best multilingual + merged-cell spans + per-cell polygons) if cloud allowed; **PaddleOCR PP-StructureV3 / Docling+RapidOCR** on-prem.
- **Avoid for core:** PyMuPDF (AGPL), Marker (GPL-3.0) — copyleft. AWS Textract — only 6 languages. Tesseract — weakest OCR. LLM-vision — hallucinates digits.
- **LLM is bounded:** classify template, map rows, normalise units/scale, associate footnotes. **Never emits a digit.**
- **Reconciliation gate:** arithmetic cross-foot + two-engine agreement + confidence ≥95% for auto-accept + per-cell bbox/page/engine/confidence persisted. Below threshold → human queue.
- **Mandatory:** benchmark Docling vs Camelot on *our own labelled* KM1/OV1 pages — in-domain TEDS (~98%) overstates real exact-match (~65–80% OOD); no public digit-CER benchmark exists.

---

## 2. Pipeline — 7 boxes

Build each box behind a clean interface; wire later. Discovery and extraction are *separate systems*.

| # | Box | Does | Output |
|---|-----|------|--------|
| 1 | **Discovery** | Structured-first: pull EU from **P3DH** (XBRL-CSV), US from **FFIEC 101**. PDF-fallback for UK/CH/JP + history: crawl IR/disclosure pages, handle URL drift, detect reporting period (not file date). | Doc candidates / structured records + metadata |
| 2 | **Ingest + registry** | Download, SHA-256 hash (dedupe/idempotency), store raw PDF immutable, version by period. | Doc registry: bank→period→version→url→timestamp |
| 3 | **Extraction** (PDF path only) | Docling/TableFormer + Camelot lattice (native); Azure DI / PaddleOCR (scanned). Two-engine deterministic. Multi-page tables stitched in post (no tool does it reliably). | Raw grids + per-cell bbox provenance |
| 4 | **Template mapping** | Classify template (KM1/OV1/CCR/MR/CVA); map row/col → canonical field; normalise unit/scale. | Field–value pairs, each with source cell ref |
| 5 | **Validation + reconcile** ⭐ | The accuracy gate. Cross-foot (components sum to total), ratio identities (CET1 = CET1cap/RWA ± tol), period-over-period sanity, unit sanity. Confidence score → low-conf to human queue. | Validated values + confidence + flags |
| 6 | **Human-in-loop review** | Triage low-confidence: show value beside source cell, approve/correct, feedback loop. | Signed-off values |
| 7 | **Store + analytics** | Long-format auditable store; peer comparison, trend, export. | Benchmarking dataset |

**Cross-cutting:** lineage everywhere · config-driven (new bank = config, not code) · reproducible
runs (seeded, versioned prompts) · immutable cost/audit ledger.

### Canonical output schema (box 7)
Long format, one row per extracted figure:
```
bank | period | jurisdiction | template | field | value | unit | source_url | page | bbox | confidence | status | extracted_at | run_id
```
Per-cell provenance (`page`, `bbox`, `source_url`) is what makes it auditable. `status` ∈
{auto_passed, human_confirmed, human_corrected, flagged}.

---

## 3. Tooling map — what to LOAD per box

Legend: **[own]** already in claude-config · **[load]** install/pull in · **[build]** must author · **[port]** copy code from a reviewed repo.

| Box | Tooling | Source |
|-----|---------|--------|
| 1 Discovery | `deep-research` skill · **exa** MCP (`web_search_exa`, `web_fetch_exa`) · `recursive-research` 4-tier source-weighting pattern | own / session / [load] Anjos2/recursive-research |
| 2 Ingest | `content-hash-cache-pattern` (SHA-256 `{hash}.json`) · boe-rag `scraper/base.py::fetch_page` (cached, rate-limited, retry) | [port] ECC · [port] boe-rag |
| 3 Extraction | `pdf` skill (pdfplumber `extract_tables`, pytesseract OCR) | [own] |
| 4 Template map | `cost-aware-llm-pipeline` (`select_model` Haiku/Sonnet router, prompt-cache static schema) · boe-rag `nodes.py::QueryFilters` Pydantic `with_structured_output` | [port] ECC · [port] boe-rag |
| 5 Validation ⭐ | `numerical-finance-stability` (NaN/inf, float-compare, currency precision) · `silent-failure-hunter` agent · `verification-before-completion` gate · boe-rag `check_hallucination` + retry loop (adapt: "is this number verbatim in source?") | [own] / [port] boe-rag |
| 6 Human review | eval-harness grader tiers (code/model/human) · `pass^k = 100%` regression framing | [load] ECC pattern |
| 7 Store/analytics | `postgres` skill (read-only guardrails) · `xlsx` skill · D3.js viz skill | [load] / [own] |
| Orchestration | `subagent-driven-development` · `dispatching-parallel-agents` · `writing-plans` | [load] superpowers |
| Build/QA | `python-reviewer` · `python-testing` · `/tdd` · `/test-coverage` · `data-throughput-accelerator` · `parallel-execution-optimizer` | [own] |
| Design | `architect` / `planner` / `/plan` | [own] |
| Meta | `skill-creator` · `mcp-builder` | [own] |
| Deliverable | `pptx` (slide) · `docx` (write-up) | [own] |

---

## 4. Reuse from `boe-rag-project` (your prior MSc work, 90/100)

~30% of scaffolding ports cleanly; **0% of the PDF/numeric core exists** (it was HTML-only by design).

**Port these (named files):**
- `src/boe_rag/config.py`, `src/boe_rag/models.py` — frozen-dataclass / StrEnum domain-type pattern.
- `src/boe_rag/scraper/base.py::fetch_page` — robust cached fetch; trivially adapts to PDF bytes.
- `src/boe_rag/scraper/runner.py` — `ManifestRow` + idempotent fetch/skip + `scrape_all` = the auditable-ingestion pattern (box 2).
- `src/boe_rag/pipelines/nodes.py::QueryFilters` + `make_analyze_query_node` — **Pydantic `with_structured_output`** = exact pattern for typed KM1/OV1 field extraction (box 4).
- `make_check_hallucination_node` + `HALLUCINATION_CHECK_PROMPT` + retry — conceptual seed of numeric reconciliation (box 5); **must be hardened from prose-groundedness to exact-digit match.**
- `models.py::PipelineResult` / `service/schemas.py::SourceItem` — provenance-per-claim threading.
- `evaluation/metrics.py` — Wilcoxon / Holm / bootstrap CIs for benchmarking accuracy across banks/periods.
- LangGraph skeleton `pipelines/enhanced.py` — injectable-deps orchestration template.

**Carry-forward warning:** boe-rag flattened tables to *text* for embedding and dropped PDF-only docs.
For Pillar 3 that is a non-starter — numbers must be extracted as **structured cells with exact
fidelity**. Different problem. Do not reuse the chunk-and-embed approach for the numbers.

---

## 5. Patterns to steal (the methodology gold)

1. **superpowers `verification-before-completion`** → reshape the gate for *extraction* claims:
   "value extracted" requires a source page/cell citation; "reconciled" requires the cross-check
   actually run and its output printed. This is the anti-hallucination backbone.
2. **superpowers `subagent-driven-development`** → one fresh subagent per **bank × template**, no
   shared context (kills cross-bank digit bleed). Extractor + a *separate* validator that sees only
   the numbers + the PDF, never the extractor's reasoning = genuine four-eyes audit.
3. **ECC eval-harness `pass^k = 100%`** → a digit must be correct on *every* trial, not once.
   Maintain a golden set of hand-verified KM1/OV1 values per bank as the regression baseline.
4. **ECC `cost-aware-llm-pipeline` immutable `CostTracker`** → frozen-dataclass cost/audit ledger,
   model routed by table complexity, prompt-cache the static schema.

---

## 6. Must BUILD from scratch (no repo provides these — and they are the project's core)

1. **`basel-pillar3-extraction` skill** — KM1/OV1/CCR/MR/CVA row/column schemas, units (€m vs %),
   RWA/capital-ratio identities, cross-table reconciliation rules. Author via `skill-creator`.
   *Biggest content gap = biggest interview signal.* Verify template codes/cadence against real EBA
   ITS templates before writing (do not assume).
2. **Deterministic validation engine** (box 5) — cross-foot, ratio recon, period-over-period sanity,
   unit/scale handling, confidence scoring → human queue. Exact-digit, not LLM-prose.
3. **Auditable dataset + benchmarking layer** (box 7) — the schema in §2, versioned, with peer/trend
   analytics. Resist false composites — per-metric comparison with provenance, calibrated definitions
   across banks (banks compute the "same" ratio differently — a real Pillar 3 trap).

---

## 7. MCP servers

| Server | Use | Status |
|--------|-----|--------|
| **exa** | Discovery: `site:<bank>.com "Pillar 3" filetype:pdf`, latest-period search | live in session, NOT pinned in config repo |
| **context7** | Library docs (pdfplumber, camelot, langgraph, ragas) | live in session |
| **Gmail** | Draft availability reply to Aarti (later) | live in session |

`exa`/`context7` are account-level, not in the config repo → won't survive a `git clone`. If the
pipeline must be portable, pin them (or wrap a custom discovery MCP via `mcp-builder`).

---

## 8. Install

```bash
# superpowers methodology skills
/plugin marketplace add obra/superpowers
/plugin install superpowers
```
Pull as needed: `recursive-research` (Anjos2), `postgres` + `deep-research` (sanjay3290/ai-skills),
D3.js viz skill (chrisvoncsefalvay). `pdf`/`xlsx`/`pptx`/`docx`/`numerical-finance-stability`/
`skill-creator`/`mcp-builder` already in claude-config.

---

## 9. Status

1. ✅ **Basel template taxonomy** — resolved & cited (see §1b D2 + `research/`). Confirm CRR3 output-floor rows + per-tier cadence against EBA ITS 2024/3172 at coding time.
2. ✅ **Extraction stack** — resolved & cited (see §1b D3 + `research/`). Docling+Camelot primary; benchmark on our own labelled pages before committing.
3. **Scope locked:** working POC → as production-ready as possible.

**Approach/tooling confidence: 10/10** — design decisions are now evidence-backed, not assumed.
*Build* confidence is 0% until the POC runs and reconciles real KM1/OV1 figures with provenance — that is the next phase, and "done" requires fresh printed evidence per §A.4 of CLAUDE.md.
