# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

> Source: Andrej Karpathy's CLAUDE.md (multica-ai/andrej-karpathy-skills). §1–4 reproduced verbatim.
> §A is this project's addendum and takes precedence on conflict. See [TOOLING.md](TOOLING.md) for the build.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

## A. Project Addendum — ISDA Pillar 3 Extraction (overrides on conflict)

**Mission:** auto-discover the latest Basel Pillar 3 disclosures per bank → extract specified
regulatory-table figures (KM1/OV1/CCR/MR/CVA/LR) → validate → emit a structured, auditable
benchmarking dataset. The three graded words are **accuracy, auditability, scalability**.

**A.1 — The one law (non-negotiable).** The LLM is judgment glue, never the ledger. Digits come
from deterministic extraction (pdfplumber/Camelot cell strings) + reconciliation. The LLM may
classify a template, map a row, normalise a unit — it may never *be the source of a number.*

**A.2 — No number without provenance + a passed check.** Every emitted value carries its source
(bank→period→version→page→bbox→url) and must pass a reconciliation check (cross-foot, ratio
identity, period sanity) before entering the dataset. No citation or failed check ⇒ not emitted;
route to the human-review queue instead.

**A.3 — Extractor and validator are separate.** The validator sees only the number + the source
PDF, never the extractor's reasoning. Four-eyes by construction (per superpowers
`subagent-driven-development`). One fresh subagent per bank × template — no shared context.

**A.4 — Verify, don't assume (Karpathy §1, sharpened).** Don't assume Basel template codes,
cadences, units, or extraction-tool accuracy. Verify against real EBA ITS templates and real PDFs.
Claims of "done"/"extracted"/"reconciled" require fresh printed evidence
(per `verification-before-completion`). `pass^k = 100%` for any numeric reconciliation.

**A.5 — Scalable = config, not code.** Adding the Nth bank is a config entry, never a code change.
Reproducible runs: seeded, versioned prompts, immutable cost/audit ledger.

**A.6 — Explainable by design.** Every component must be defensible in plain English (this is a
class project to be presented). Complexity lives in the *system*; clarity lives in the *story*.
Match the house rules in `claude-config/rules/*.md` (immutability, small files, no silent failures,
boundary validation, 80% coverage).

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to
overcomplication, clarifying questions come before implementation — and **zero un-cited or
un-reconciled numbers ever reach the dataset.**
