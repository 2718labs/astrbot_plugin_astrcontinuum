# v0.3 Evidence and RMB Workflow Assets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add provenance-bound research evidence, restrained RMB-palette SVG
figures, and a truthful dual-lane workflow to the v0.3 Technical Preview
documentation.

**Architecture:** Keep the production publication contract separate from
research evidence. Commit only small aggregate CSV extracts and self-contained
SVGs, while recording the source roots and SHA-256 values needed to verify
them locally. Entry-point documents link to the canonical evidence and
workflow pages.

**Tech Stack:** Markdown, CSV, hand-authored SVG, Python standard-library XML
validation, Git.

---

### Task 1: Commit bounded evidence extracts and their manifest

**Files:**

- Create: docs/evidence/README.md
- Create: docs/evidence/frozen-r2-outcomes.csv
- Create: docs/evidence/crm-v21-008-stress-rounds.csv
- Create: docs/EVIDENCE.md

- [x] Record the frozen R2 four-arm aggregates exactly as Full = 27/36
  structural, 27/36 delivered, 21/36 claim-v2; Projection = 36/36, 36/36,
  25/36; Summary = 36/36, 36/36, 27/36; Oracle = 36/36 with no answer metric.
- [x] Record the twelve V21-008 deterministic stress rounds with source SHA-256
  0C8BDC214588AA53F06DA830181D7EB9FF5D164063E8EC8FB74F75B7D0651760.
- [x] State that both extracts are research evidence only and cannot support a
  production v0.3 semantic, Provider, or release claim.

### Task 2: Add restrained RMB-palette evidence and workflow SVGs

**Files:**

- Create: docs/assets/evidence-r2-outcomes-rmb.svg
- Create: docs/assets/v030-workflow-rmb.svg
- Create: docs/WORKFLOW.md

- [x] Draw the R2 aggregate chart with white background, #A44742, #1F6B5B,
  #DDD6CC, #786F63, and #263238; include a frozen-synthetic,
  non-production caption.
- [x] Draw current v0.3 as a solid claim → compile → optional audit →
  publish/CAS lane and CRM as a dashed research-only lane.
- [x] Mark the standard worker empty reorganization tuple, CAS SUPERSEDED
  outcome, ledger-integrity rollback, and no-Summary-runtime boundary.

### Task 3: Connect public documentation entry points

**Files:**

- Modify: README.md
- Modify: docs/EVALUATION.md
- Modify: docs/ROADMAP.md

- [x] Link the evidence and workflow pages from all three entry points.
- [x] Preserve the existing v0.3 Technical Preview boundary: the
  CompactionWorker does not yet invoke reorganize_capsules() and normal
  publication has an empty record tuple.

### Task 4: Verify and integrate

**Files:** No source-code change is expected.

- [ ] Parse both SVGs with Python XML, assert the palette and required labels,
  assert entry-point links, check the CSV row counts, run git diff --check,
  then stage only documentation and asset paths, commit, and push the existing
  draft-PR branch.
