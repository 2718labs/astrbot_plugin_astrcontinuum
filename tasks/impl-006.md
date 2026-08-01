# IMPL-006 Frozen Projection and Claim-v2 Scoring

Owner: bugkiller-sol-code-writer
Depends on: impl-005

## Goal

Implement query-bounded projection from one frozen Capsule, deterministic
claim-v2 scoring, and the preregistered paired-cluster lower bound.

## Context

- Data model: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-data-model.md`
- Experiment boundary: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-experiment-design.md`
- Toolchain: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\python-toolchain.md`
- Implementation plan: Task 6 in `plans\2026-07-30-capsule-recomposition-matrix-implementation.md`
- Production root `G:\AstrContinuum` remains read-only.

## Write Scope

- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\projection.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\scoring.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tests\test_projection.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tests\test_scoring.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tasks\impl-006.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\index.md`

## Steps

1. Use only `.git\codex-tmp\impl006` under the D-drive task root for temporary artifacts and caches.
2. TDD RED: add projection requests before the production modules and retain the expected missing-module failure.
3. `project_query` accepts only frozen state, query spec, and byte budget; it never accepts old state, delta, truth, gold, or Summary.
4. Select matching atoms by descending revision then stable ID, count exact UTF-8 bytes including newlines, and return `[CONTEXT_INSUFFICIENT]` when none fit.
5. `normalize_text` performs NFKC, casefolding, and whitespace collapse; `score_projection` checks both coverage IDs and normalized required/forbidden text.
6. Implement deterministic paired-cluster bootstrap with sorted cluster IDs, explicit seed, preregistered lower-tail index, and validation for empty clusters or invalid replicate/alpha inputs.
7. Cover Chinese text, exact anchors, explicit unknown, forbidden old revisions, byte boundaries, deterministic bootstrap, and invalid inputs.
8. Run focused/all tests, Ruff check/format, Pyright, lock, protected manifest, 2718lab validation, and pre-commit.
9. Commit `feat: add frozen projection and claim-v2 scoring`.

## Acceptance

```powershell
uv run pytest tests/test_projection.py tests/test_scoring.py -q
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv lock --check
uv run python -m crm_experiment.evidence --repo G:\AstrContinuum --manifest evidence\protected-source-hashes.json --verify
uv run pre-commit run --all-files
```

Expected: bounded projection never reads outside the frozen state; claim-v2
distinguishes omission from stale-current assertions; bootstrap results are
deterministic; all project gates pass.

## Return

Return `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`, changed
files, commit SHA, exact RED/GREEN and acceptance outputs, and blockers.
