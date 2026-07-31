# IMPL-003 Continuity Kernel and CRM Matrix Construction

Owner: bugkiller-sol-code-writer
Depends on: impl-002

## Goal

Implement the bounded continuity kernel and deterministic atom/candidate/matrix construction used by later CRM optimization stages.

## Context

- Design contract: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-experiment-design.md`
- Data model: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-data-model.md`
- Toolchain: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\python-toolchain.md`
- Implementation plan: Task 3 in `plans\2026-07-30-capsule-recomposition-matrix-implementation.md`
- Production root `G:\AstrContinuum` remains read-only.

## Write Scope

- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\kernel.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\matrix.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tests\conftest.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tests\test_kernel.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tests\test_matrix.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tasks\impl-003.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\index.md`

## Steps

1. Run all commands with the Python toolchain contract's D-drive temporary paths.
2. TDD RED: write kernel tests for the frozen default/custom ceilings and matrix tests for key replacement, exact-atom merge exclusion, and matrix dimensions; run them before production modules and retain the expected missing-module failures.
3. Implement `default_kernel_schema`, `derive_kernel_ceiling`, and deterministic `select_kernel` exactly as the plan specifies. Preserve active core atoms and report slot/text/coverage failures without silently dropping them.
4. Implement `atomize`, `compose_candidates`, and `build_matrix` with immutable tuple outputs, stable canonical IDs, exact UTF-8 costs, verified pair merges only, fixed feature columns, coverage, dependency, conflict, and Jaccard redundancy matrices.
5. Run focused tests, all existing tests, Ruff check/format, Pyright, protected manifest verification, 2718lab project validation, and pre-commit.
6. Review the diff and commit `feat: add continuity kernel and CRM matrices`.

## Acceptance

```powershell
uv run pytest tests/test_kernel.py tests/test_matrix.py tests/test_contracts.py tests/test_evidence.py -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run python -m crm_experiment.evidence --repo G:\AstrContinuum --manifest evidence\protected-source-hashes.json --verify
```

Expected: default kernel ceiling `4608`; all tests pass except the explicit platform skip; no production files change; task worktree is clean after the commit.

## Return

Return `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`, changed files, commit SHA, exact RED/GREEN and acceptance outputs, and blockers.
