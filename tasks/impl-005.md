# IMPL-005 Loss-Aware Recomposition and Safe Release

Owner: bugkiller-sol-code-writer
Depends on: impl-004

## Goal

Implement the pure CRM recomposition transaction, bounded failure states, release inventory, and experiment-only production diagnostic.

## Context

- Data model: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-data-model.md`
- Experiment boundary: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-experiment-design.md`
- Toolchain: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\python-toolchain.md`
- Implementation plan: Task 5 in `plans\2026-07-30-capsule-recomposition-matrix-implementation.md`
- Production root `G:\AstrContinuum` remains read-only.

## Write Scope

- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\__init__.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\recompose.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\production_diag.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tests\conftest.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tests\test_recompose.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tests\test_production_diag.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tasks\impl-005.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\index.md`

## Steps

1. Use only D-drive temporary, uv cache, environment, and evidence paths from the toolchain contract.
2. TDD RED: add real requests for normal, kernel-only, blocked, invalid initial, and zero-delta cases before production modules; retain the expected missing-module failure.
3. Implement `recompose_capsule` in the exact Task 5 order: continuity floor, atomize, kernel gate, residual budget, candidates, matrix, optimizer, candidate state, loss-aware validation, and result assembly.
4. A blocked transition must keep only the previous kernel, preserve accepted budget/high-water, release the previous body, reject delta, and never return an empty or full stale Capsule.
5. Candidate validation must enforce actual canonical UTF-8 budget, active core coverage, risk ceiling, provenance coverage, current/current conflicts, dependencies, and a complete retained/merged/released partition.
6. Preserve zero-delta semantic hashes, measure matrix/optimizer/gate nanoseconds outside semantic hashes, and never expose released content.
7. Implement `ProductionDiagnostic`; this experiment always returns `strict_eligible=False` and never attempts production publication.
8. Run focused/all tests, Ruff check/format, Pyright, manifest verification, 2718lab validation, and pre-commit.
9. Commit `feat: add loss-aware Capsule recomposition`.

## Acceptance

```powershell
uv run pytest tests/test_recompose.py tests/test_production_diag.py -q
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run python -m crm_experiment.evidence --repo G:\AstrContinuum --manifest evidence\protected-source-hashes.json --verify
```

Expected: `NORMAL`, `KERNEL_ONLY`, and `ADMISSION_BLOCKED` transitions pass; release inventory is exhaustive; zero-delta is a semantic fixed point; all production diagnostics remain ineligible; task worktree is clean after commit.

## Return

Return `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`, changed files, commit SHA, exact RED/GREEN and acceptance outputs, and blockers.
