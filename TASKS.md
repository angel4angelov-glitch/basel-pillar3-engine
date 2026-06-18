# TASKS.md — ISDA Pillar 3 build, chunk by chunk

One tiny chunk per fresh Claude Code chat. Each chunk = one module + its tests + a passing `verify`.
Tick the box when its `verify` is green and committed. Plan detail lives in
`~/.claude/plans/fantastic-now-i-would-unified-gadget.md`; behaviour in `CLAUDE.md`; design in `TOOLING.md`.

## How to run a chunk
1. Open a **new** chat in this directory.
2. Paste the **bootstrap header** + the chunk block (I'll draft each for you).
3. Paste the chat's output back here; I draft the next chunk.

```
BOOTSTRAP HEADER (paste at the top of every chunk chat):
@TOOLING.md — read this + CLAUDE.md (auto-loaded) + the plan at
~/.claude/plans/fantastic-now-i-would-unified-gadget.md. Obey CLAUDE.md §A (LLM≠ledger, provenance,
no un-reconciled number). Do ONLY the chunk below — nothing else.
Workflow: /tdd (tests FIRST) → implement minimal → python-reviewer + silent-failure-hunter agents →
verification-before-completion (run the verify cmd, paste real output) → /commit (conventional msg). Stop.
```

---

## M0 — Skeleton
- [ ] **0.1 Repo + tooling** — git init; `pyproject.toml` (py≥3.11, ruff len100, pytest markers); `requirements*.txt`; `src/isda_p3/` dir tree (stubs); `.github/workflows/ci.yml`; `.env.example`. **Verify:** `ruff check . && pytest -q`. Skills: `python-patterns`.
- [ ] **0.2 config.py** — port boe-rag pattern: `Paths`, module constants, `setup_logging()`. **Verify:** `pytest tests/unit/test_config.py`. Skills: `python-reviewer`.
- [ ] **0.3 models.py** — all frozen dataclasses + StrEnums (incl. `ecl_basis`/`floor_basis`, `MappingDecision`, `Provenance`, `FieldValue`, `RawCell`, `CheckResult`, `ReconciliationResult`, `DatasetRow`, `ManifestRow`). **Verify:** `pytest tests/unit/test_models.py` (frozen + `ReportingPeriod("2025Q4").label`). Skills: `python-reviewer`, `numerical-finance-stability`.
- [ ] **0.4 config loaders** — `banks.yaml` (full G-SIB roster + `number_locale`, `reporting_currency`) + `templates/{km1,ov1}.yaml` loaders → typed objects. **Verify:** `pytest tests/unit/test_config_load.py`.
- [ ] **0.5 extraction env smoke** — install Docling + Camelot. **Verify:** `python -c "from docling.document_converter import DocumentConverter; print('ok')"`.

## M1 — Vertical slice (KM1, one UK bank, end-to-end)
- [ ] **1.1 Extraction protocol + Docling engine** — `extraction/engine.py` protocol; `docling_engine.py` → `list[RawCell]` w/ bbox. **Verify:** unit test on tiny bundled PDF returns ≥1 cell w/ bbox. Skills: `pdf`, `silent-failure-hunter`.
- [ ] **1.2 normalise.py (C2 firewall — critical)** — locale-aware: `368.000`/`368 000`/`368,000`, `(1,234)`→-1234, `13.6¹`→13.6, %↔ratio, €k/€m/€bn. **Verify:** `pytest tests/unit/test_normalise.py` green on all adversarial fixtures. Skills: `numerical-finance-stability`, `silent-failure-hunter`.
- [ ] **1.3 km1.yaml + rule-first map_fields** — map rows 1–7 by alias → `FieldValue` w/ basis axes + `MappingDecision(method=RULE)`. **Verify:** `pytest tests/unit/test_map_fields.py`. Skills: `python-reviewer`.
- [ ] **1.4 LLM fallback mapper** — `mapping/llm.py` `with_structured_output`; only on unmatched labels; logs full `MappingDecision`. **Verify:** stub-LLM test, no network. Skills: `python-reviewer`.
- [ ] **1.5 reconcile checks** — `checks.py` cross_foot + **basis-pinned** ratio_identity + `identities.yaml`. **Verify:** `pytest tests/unit/test_checks.py` (PASS/FAIL/SKIP each; cross-basis raises); reconcile cov ≥90%.
- [ ] **1.6 confidence + engine** — weighted-product `confidence.py`; `engine.py` ≥0.95 routing + `validation_basis`. **Verify:** `pytest tests/unit/test_reconcile_engine.py`. Skills: `silent-failure-hunter`.
- [ ] **1.7 dataset store** — `store/dataset.py` append → parquet as **Decimal128**. **Verify:** round-trip test asserts no float coercion. Skills: `python-reviewer`.
- [ ] **1.8 pipeline + CLI + golden + integration** — `pipeline.py` DI 3→4→5→7; `cli.py run`; golden YAML (two-pass basis check). **Verify:** M1 command + `pytest -m integration` on real PDF. Skills: `verification-before-completion`.

## M2 — OV1 + two-engine + review
- [ ] **2.1 Adaptive 2nd engine** — lattice→stream→pdfplumber selector → `RawCell`. **Verify:** borderless-PDF test falls back.
- [ ] **2.2 two_engine_agreement** — post-mapping at FieldValue level. **Verify:** disagreement → confidence drop test.
- [ ] **2.3 ov1.yaml + OV1 mapping + cross-foot** — top-level rows only (no "of which"). **Verify:** `pytest tests/unit/test_ov1.py`.
- [ ] **2.4 review/queue.py + `cli review`** — value-beside-cell render. **Verify:** low-conf row appears with both engine values.

## M3 — Structured ingest + registry
- [ ] **3.1 registry.py** — ManifestRow + SHA-256 idempotent dedupe. **Verify:** re-run → skipped_dup; manifest unchanged.
- [ ] **3.2 p3dh.py** — XBRL-CSV parser → FieldValues (fetch stubbed). **Verify:** same canonical KM1 as PDF path, engine=P3DH bbox=null.
- [ ] **3.3 period resolver** — date from content, rank candidates, log why. **Verify:** picks latest, records reason.

## M4 — Widen + analytics + benchmark
- [ ] **4.1 analytics.py** — `peer_compare` + `trend`. **Verify:** peer table links source_url+page.
- [ ] **4.2 export_xlsx + benchmark harness** — Docling vs Camelot on golden, bootstrap CI (port boe-rag `metrics.py`). **Verify:** exact-match % per engine + CI.
- [ ] **4.3 widen** — add banks (config only) + grow golden ≥5; report honest CI width.

---

**Cadence rule:** never start a chunk whose inputs aren't green. If a chunk balloons, split it — the
unit of work is "one module + its tests + a passing verify," not a milestone.
