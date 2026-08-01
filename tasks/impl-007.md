# IMPL-007 Preregistered Protocol and Offline Baselines

Owner: bugkiller-sol-code-writer
Depends on: impl-006

## Goal

Freeze the deterministic 24-stream multiround protocol and implement the four
budgeted offline baseline/ablation adapters that the phase-separated runner can
advance without query or gold access.

## Context

- Data model: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-data-model.md`
- Experiment boundary: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-experiment-design.md`
- Toolchain: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\python-toolchain.md`
- Implementation plan: Task 7 in `plans\2026-07-30-capsule-recomposition-matrix-implementation.md`
- Task 8 consumes the frozen protocol members and baseline `advance` APIs, but runner behavior is out of scope here.
- Production root `G:\AstrContinuum` remains read-only.

## Write Scope

- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\config\protocol-v1.json`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\protocol.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\baselines.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tests\test_protocol.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tasks\impl-007.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\index.md`

## Steps

1. Use only `.git\codex-tmp\impl007` under the D-drive task root for temporary artifacts and caches.
2. TDD RED: add protocol tests before production modules and retain the expected missing-module failure.
3. Freeze `config/protocol-v1.json` exactly as Task 7 specifies and load it through structured JSON parsing with explicit validation.
4. Add frozen `ProtocolConfig`, `GenerationCase`, `StreamCase`, and `ProtocolBundle` records; keep runtime events, public query specs, and sealed gold as separate members.
5. Generate seeds `271800..271823`, 12 generations per stream, exactly six queries per generation in the fixed role order, deterministic IDs, and byte-identical canonical output.
6. Runtime state-building inputs contain only runtime events. Public queries contain specs but no answers; sealed gold contains required/forbidden IDs and text but is never accepted by any state `advance` API.
7. Implement `LegacyCapsuleBaseline`, `RecursiveSummaryBaseline`, `NoProjectionAblation`, and `NoKernelAblation` plus the exact `ARM_ORDER`; all persistent and query-time bytes count as UTF-8.
8. Legacy uses stable direct `weight / byte_cost`; recursive Summary sees only prior summary text plus current delta; no-projection is query-blind; no-kernel clears core flags only in its offline copy and is the only budgeted arm allowed to emit empty state.
9. Prove API isolation, 1728-query fixed denominator, kernel-capacity validity, main-cluster exclusion of the overflow fixture, equal budgets, deterministic reruns, and arm ordering.
10. Run focused/all tests, Ruff check/format, Pyright, lock, protected manifest, 2718lab validation, and pre-commit.
11. Return without committing; the main Sol agent performs both read-only review stages and final acceptance before commit `feat: add sealed multiround protocol and baselines`.

## Acceptance

```powershell
uv run pytest tests/test_protocol.py -q
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv lock --check
uv run python -m crm_experiment.evidence --repo G:\AstrContinuum --manifest evidence\protected-source-hashes.json --verify
uv run pre-commit run --all-files
```

Expected: protocol generation is byte-identical with 24 x 12 x 6 fixed query
units; runtime/query/gold boundaries are inspectably separate; every budgeted
arm obeys the same UTF-8 ceiling; all gates pass.

## Return

Return `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`, changed
files, exact RED/GREEN and acceptance outputs, self-review findings, and
blockers. Do not commit.
