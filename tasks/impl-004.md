# IMPL-004 Deterministic CRM Optimizer

Owner: bugkiller-sol-code-writer
Depends on: impl-003

## Goal

Implement deterministic exact-small and greedy-large candidate selection over `MatrixBundle`.

## Context

- Data model: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-data-model.md`
- Experiment boundary: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-experiment-design.md`
- Toolchain: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\python-toolchain.md`
- Implementation plan: Task 4 in `plans\2026-07-30-capsule-recomposition-matrix-implementation.md`
- Production root `G:\AstrContinuum` remains read-only.

## Write Scope

- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\optimizer.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\matrix.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\contracts.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tests\conftest.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tests\test_optimizer.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tasks\impl-004.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\index.md`

## Steps

1. Use only the toolchain contract's D-drive temporary and uv paths.
2. TDD RED: add the frozen five-atom reference fixture and test first; retain the expected missing-module failure.
3. Implement `optimize`, exact enumeration, greedy marginal improvement, subset evaluation, and `Selection` construction exactly as Task 4 specifies.
4. Reject over-budget, over-risk, conflicting, and dependency-open selections. Minimize omission plus risk and redundancy costs, use candidate-ID tie breaks, round only returned objectives, and never use randomness or wall-clock ordering.
5. Test both exact and greedy branches, deterministic repeated calls, conflicts, dependency closure, budget/risk limits, and empty feasible selection.
6. Run focused/all tests, Ruff check/format, Pyright, manifest verification, 2718lab validation, and pre-commit.
7. Commit `feat: implement deterministic CRM optimizer`.

## Acceptance

```powershell
uv run pytest tests/test_optimizer.py tests/test_matrix.py -q
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run python -m crm_experiment.evidence --repo G:\AstrContinuum --manifest evidence\protected-source-hashes.json --verify
```

Expected: the frozen reference selects `("c2", "c3")`, costs `15`, and has objective `1.15`; repeated calls are identical; all commands exit 0; task worktree is clean after commit.

## Return

Return `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`, changed files, commit SHA, exact RED/GREEN and acceptance outputs, and blockers.
