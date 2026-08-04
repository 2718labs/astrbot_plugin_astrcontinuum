# SC-01 Release Safety Baseline

Owner: release-safety-implementer
Depends on: none

## Goal

Close the remaining authenticated-storage and lifecycle findings before graph integration touches
the same composition root.

## Context

Read:

- `astrcontinuum/compaction/worker.py`
- `main.py`
- `tests/test_compaction_worker.py`
- `tests/test_main_hooks.py`

Do not read sibling task cards.

## Write Scope

- `astrcontinuum/compaction/worker.py`
- `main.py`
- `tests/test_compaction_worker.py`
- `tests/test_main_hooks.py`

## Steps

1. Add a failing test proving an authenticated-storage failure raised by lease renewal cannot be
   swallowed by heartbeat cleanup.
2. Propagate that failure through the worker fatal callback without overriding task cancellation.
3. Add a failing startup test proving a configured replacement key id is not reported as the
   durable active key id when activation fails.
4. Set the displayed durable key id only after successful activation.
5. Add inspection regression coverage for same-CID persona change after the repository query.
6. Recheck lifecycle state after status queries so stale ACTIVE output is discarded.

## Acceptance

Run with `TEMP`, `TMP`, and `TMPDIR` under
`D:\bun\tmp\codex\astrcontinuum\release-s4-fixes`:

```powershell
uv run --python D:\bun\tmp\codex\astrcontinuum\envs\release-v0.1.0\Scripts\python.exe pytest tests/test_compaction_worker.py tests/test_main_hooks.py -q
uv run --python D:\bun\tmp\codex\astrcontinuum\envs\release-v0.1.0\Scripts\python.exe ruff check astrcontinuum/compaction/worker.py main.py tests/test_compaction_worker.py tests/test_main_hooks.py
```

Both commands must exit zero.

## Return

Return changed files, exact commands and results, conclusion, and remaining blockers. Do not
commit.
