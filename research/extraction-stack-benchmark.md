# PDF Table Extraction Stack — Benchmark Briefing

Research date: 2026-06-17. For extracting numeric data from Basel Pillar 3 PDF tables.

## Bottom line
**Deterministic extraction owns the digits; the LLM owns structure/semantics; a reconciliation
gate owns sign-off.** Docling/TableFormer or Camelot-lattice (native) → Azure DI or PaddleOCR
(scanned) → LLM only for classification/mapping/unit-normalisation, never digits → arithmetic +
two-engine reconciliation gate with bbox provenance persisted per cell.

## 1. Tool landscape (2025-2026)
| Tool | Type | Native/OCR | Merged cells | Cell bbox | Licence |
|---|---|---|---|---|---|
| pdfplumber | OSS | Native | none (no model) | yes (cell+word+char) | MIT |
| Camelot (lattice/stream) | OSS | Native | partial (lattice best) | cell yes, word no | MIT |
| Tabula | OSS | Native | weak | limited | OSS |
| PyMuPDF find_tables | OSS | Native | none | yes | **AGPL** |
| **Docling (TableFormer)** | OSS | Both | good (model) | item; per-cell available | **MIT** |
| Unstructured.io | OSS+API | Both | via TATR, mixed | per-element only (no cell) | OSS/paid |
| Marker | OSS | Both | weak on dense | yes | **GPL-3.0** |
| LlamaParse | API | Both | inconsistent on dense | yes | credits |
| AWS Textract | API | Both | spans yes; rough on extreme | yes | $15/1k; **6 langs only** |
| **Azure Doc Intelligence** | API | Both | **rowSpan/colSpan + polygons** | yes | ~$10/1k |
| Google Document AI | API | Both | Layout/Gemini yes (Form Parser no) | yes | $0.65–30/1k |
| Claude/GPT-4o vision | API | Vision | flexible but **hallucinates digits** | no | tokens |
| MS Table Transformer (TATR) | OSS model | Image | yes (canonicalised headers) | yes | MIT |

Notes: merged/spanning headers best from Azure DI / Gemini Layout / TATR. **Multi-page continuity
weak across ALL tools — stitch in post via header-matching.** Copyleft flags: PyMuPDF (AGPL),
Marker (GPL) — avoid for a proprietary core; prefer MIT (pdfplumber/Camelot/Docling/TATR).

## 2. Benchmark evidence
- TableFormer (Docling) — PubTabNet TEDS 98.5 simple / 95.0 complex; **FinTabNet TEDS 96.8** (most relevant); content TEDS 93.6 vs OCR tools 65–80.
- TATR on PubTables-1M — GriTS ~0.985.
- **Caveat (arXiv:2303.00716):** in-domain overstates. TATR exact-match drops to **65% / 42%** on ICDAR-2013 OOD. Expect ~98% TEDS → ~65–80% real exact-match.
- OmniDocBench (CVPR 2025) table-crop TEDS: RapidTable 82.5, PaddleOCR 73.6, GPT-4o 71.8 EN/58.8 ZH, Marker 52.5.
- Cloud APIs (independent signal across two vendor benches): **Azure DI ~0.76–0.78 > AWS Textract ~0.60–0.67 > Google DocAI/Unstructured ~0.36–0.46**; GPT-4o mid-pack. Vendor self-benches discounted.
- **Leaders on structure:** TATR + TableFormer (academic); Azure DI (credible cloud).

## 3. No-hallucinated-digits — consensus
Deterministic owns digits; LLM never the system-of-record number; vision behind a verification gate.
- GFT Engineering: compute `Z = X*Y` in code, never ask the model — risk of hallucinated result. LLMs for semantic tasks, not numeric/structural.
- Parseur: LLMs probabilistic — same doc twice can differ; unreliable for auditable financial processes.
- Independent test (Kramer, 12 tools): pdfplumber "perfect" cell detection; TableFormer 93.6 vs Tabula 67.9 / Camelot 73.0; LLM/vision models had merged-cell/alignment/incomplete-data issues.
- FAITH (ICAIF 2025): SOTA models struggle with numerical scale in financial reasoning. KIE-HVQA: tuned 7B beats GPT-4o by 22pts hallucination-free.
- "99% trap": 99% accuracy = 0% trust if the 1% is a sign inversion. Use deterministic fact ledger, LLM gated behind.
- Deterministic weakness = *layout, not fidelity* — returns true chars, mangles merged structure. That's the LLM's job (mapping), and exactly why it never touches digit values.
- Pattern: rule-based layer → LLM verify/repair (guardrails) → human review high-impact (~95%, ~10% manual).

## 4. Provenance (cell bbox for audit)
- pdfplumber — finest (table/row/col/cell/word/char). Camelot — per-cell corners, no per-word.
- Textract — cell+word polygons. **Azure DI — per-cell boundingRegions + rowIndex/colIndex + word polygons (best merged + provenance combo).** Docling — item-level always, per-cell available.
- Google DocAI / PyMuPDF / LlamaParse — cell+word. **Unstructured — per-element only (audit limitation).**
- Persist per number: page + cell bbox + source span + engine + confidence.

## 5. Scanned / OCR + multilingual
- Best cloud: **Azure DI Read+Layout** (~299 langs, CER ~0.9%, per-token confidence). Google DocAI co-finalist.
- Disqualifier: **AWS Textract = 6 langs only**. Tesseract weakest on noisy scans (fallback only).
- Best on-prem: **PaddleOCR PP-OCRv5 + PP-StructureV3** (100+ langs, table pipeline, confidence). RapidOCR repackages Paddle → **Docling-TableFormer + RapidOCR** = Paddle-grade on-prem.
- Pattern: structure model puts numbers in right cell; OCR gets digits right; validate both independently. Wrong column = regulatory error.
- **Evidence gap:** no public benchmark isolates digit/numeric CER. All engines confuse 0/O,1/l/I,5/S,8/B. **Benchmark on our own numeric Basel pages.** Mandatory digit-verification (confidence thresholds, numeric regex, dual-engine agreement, cross-foots).

## 6. Recommended stack
- **Primary (native PDF):** Docling/TableFormer (MIT) + Camelot lattice (2nd deterministic engine).
- **Fallback (scanned):** Azure DI (cloud) or PaddleOCR PP-StructureV3 / Docling+RapidOCR (on-prem).
- **LLM (Claude/GPT-4o) bounded:** template classification, row/label mapping, merged-header disambiguation, unit/scale normalisation, footnote association. Never emits a value; require page+bbox grounding span per value, reject ungrounded.
- **Reconciliation gate:** (1) arithmetic cross-foot rows/cols/subtotals; (2) two-engine agreement → disagreement to human; (3) confidence ≥95% auto-accept else human; (4) persist page+bbox+span+engine+confidence per number.
- **Build-order:** benchmark Docling vs Camelot on labelled sample of *our actual* KM1/OV1/CCR/MR/CVA pages before committing (98%→65-80% OOD gap; no public digit-CER bench).

### Caveats
Reducto/Pulse = vendor self-benches (only the relative Azure>Textract>others signal used). Procycons Docling 97.9 = small-N blog. LlamaParse conflicting results → not recommended for core.

Key sources: TableFormer arXiv:2203.01017 · TATR github.com/microsoft/table-transformer · alignment arXiv:2303.00716 · OmniDocBench arXiv:2412.07626 · Docling arXiv:2501.17887 · FAITH arXiv:2508.05201 · GFT medium.com/gft-engineering · Azure DI learn.microsoft.com · PaddleOCR arXiv:2507.05595
