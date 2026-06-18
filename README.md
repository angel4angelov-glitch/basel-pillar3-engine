# isda-p3 — Basel Pillar 3 disclosure extraction & benchmarking

Auto-discovers the latest Basel Pillar 3 public disclosures per bank, extracts
specified regulatory-table figures (KM1/OV1/CCR/MR/CVA/LR), validates and
reconciles every figure, and emits a structured, auditable dataset for peer
comparison and trend analysis. The graded criteria are **accuracy,
auditability, scalability**.

> **The one law:** the LLM is judgment glue, never the ledger. Digits come from
> deterministic extraction (pdfplumber/Camelot cell strings) + reconciliation;
> the LLM may classify a template, map a row, or normalise a unit — it may never
> *be the source of a number*. Every emitted value carries its provenance
> (bank → period → version → page → bbox → url) and must pass a reconciliation
> check before entering the dataset.

See [TOOLING.md](TOOLING.md) for the architecture and the build plan.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # add your ANTHROPIC_API_KEY

ruff check .                  # lint
pytest -q                     # tests (integration tests are opt-in: -m integration)
isda-p3                       # CLI entry point
```
