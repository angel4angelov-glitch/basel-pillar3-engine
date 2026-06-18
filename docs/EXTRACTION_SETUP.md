# Extraction environment setup

System prerequisites for the deterministic extraction engines (Docling/TableFormer,
Camelot, pdfplumber). Python deps are pinned in [pyproject.toml](../pyproject.toml);
this file documents the **non-pip** prerequisites so the M1/M2 extraction chunks don't
hit surprises.

> Scope: this is documentation only (chunk 0.5). Nothing here changes `pyproject.toml`.

## Python deps (already in `pyproject.toml`)

`docling`, `camelot-py[base]`, `pdfplumber` — install with:

```bash
pip install docling "camelot-py[base]" pdfplumber
```

Docling pulls **torch** and the **TableFormer** model layer — a large download, that's
expected. Model **weights are not fetched at import**; they download lazily on the first
`DocumentConverter().convert(...)` call. The chunk-0.5 smoke is import-only and therefore
does **not** trigger that download — deferred to the chunk 1.1 integration test.

## System prerequisites (NOT pip-installable)

### Camelot **lattice** flavour — needed in chunk 2.1 (M2)

Camelot's `lattice` flavour (used for ruled-line tables) has two non-pip dependencies:

| Dependency | Needed for | macOS install | Status in this venv |
|------------|-----------|---------------|---------------------|
| **Ghostscript** (system binary) | lattice rendering | `brew install ghostscript` | **ABSENT** — install before M2 |
| **OpenCV** (`cv2`) | lattice line detection | `pip install opencv-python` (or use `camelot-py[cv]`) | present (`cv2` 4.13.0) |

Important: **`camelot-py[base]` does NOT include OpenCV.** When lattice lands in chunk 2.1,
use the `camelot-py[cv]` extra (or `pip install opencv-python`) **and** install Ghostscript.
OpenCV happens to already be present in this venv, but Ghostscript is missing — `gs` is not
on PATH — so **lattice will fail until `brew install ghostscript` is run.** Do not change
`pyproject.toml` for this now; this is the heads-up for the M2 chunk.

Note (per plan M2): most KM1/OV1 tables are **borderless**, where lattice returns nothing —
the adaptive selector falls back lattice → stream → pdfplumber. So Ghostscript is required
only for the lattice path, not for the common borderless case.

### Docling / pdfplumber

No system binaries required for native-text extraction. (Scanned-PDF OCR — PaddleOCR or
Azure DI — is a later, out-of-scope path.)

## Verify

```bash
python -c "from docling.document_converter import DocumentConverter; print('ok')"
python scripts/smoke_extraction.py
```
