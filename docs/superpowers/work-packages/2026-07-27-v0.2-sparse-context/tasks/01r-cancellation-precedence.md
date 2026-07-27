# SC-01R Cancellation Precedence Repair

Owner: release-safety-repairer
Depends on: SC-01

## Goal

Preserve task cancellation when lease-heartbeat authentication failure races with cancellation,
while still escalating an unopposed authentication failure through the worker fatal callback.

## Context

Read:

- `astrcontinuum/compaction/worker.py`
- `tests/test_compaction_worker.py`

Do not read sibling task cards or modify lifecycle composition.

## Write Scope

- `astrcontinuum/compaction/worker.py`
- `tests/test_compaction_worker.py`

## Steps

1. Add a failing concurrent test in which heartbeat returns
   `STORAGE_AUTHENTICATION_FAILED` while `run_iteration` is being cancelled.
2. Assert that the final exception is `asyncio.CancelledError`.
3. Add direct coverage proving an unopposed heartbeat authentication failure reaches the worker
   fatal callback.
4. Change cleanup propagation so heartbeat authentication failure is raised only when no
   cancellation or other primary exception is already propagating.

## Acceptance

Use a task-local temporary root under
`D:\bun\tmp\codex\astrcontinuum\sc01r-cancellation` and run:

```powershell
uv run --python D:\bun\tmp\codex\astrcontinuum\envs\release-v0.1.0\Scripts\python.exe pytest tests/test_compaction_worker.py -q
uv run --python D:\bun\tmp\codex\astrcontinuum\envs\release-v0.1.0\Scripts\python.exe ruff check astrcontinuum/compaction/worker.py tests/test_compaction_worker.py
```

Both commands must exit zero.

## Return

Return changed files, exact RED and GREEN commands/results, concurrency behavior, conclusion, and
blockers. Do not commit.
