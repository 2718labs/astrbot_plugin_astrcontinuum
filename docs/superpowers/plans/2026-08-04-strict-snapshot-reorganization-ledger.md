# Strict Snapshot Reorganization Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist an immutable, ordered reorganization audit ledger only when a candidate Snapshot is atomically published, without weakening the permanent publication gate.

**Architecture:** Keep `ReorganizationRecord` as the shared typed input, add a v2 SQLite ledger table, and pass an optional canonical record tuple through `SQLiteRepository.publish_snapshot()`. The repository validates records after its live-fence checks, overlays `QUALITY_COVERAGE_GAP` for non-summary releases, inserts ledger rows after Snapshot membership in the existing savepoint, and exposes a committed-Snapshot-only ordered reader.

**Tech Stack:** Python 3.10+, Pydantic v2 frozen envelopes, SQLite migrations/savepoints/triggers, pytest, Ruff, mypy.

---

## Current evidence and dispatch gate

- The DevKit project-level index has a current-base snapshot for the isolated integration
  worktree. It maps `publish_snapshot()` at `astrcontinuum/storage/repository.py:800-986`,
  `ReorganizationRecord` at `astrcontinuum/reorganization.py:42-51`, and `MIGRATIONS` at
  `astrcontinuum/storage/migrations.py:405-411`.
- A bounded Atlas regression packet was prepared for
  `tests/storage/test_repository_publication.py`; it is evidence only, not an acceptance
  decision. The project index reports `INDEX_PARTIAL` because its parser has recorded
  gaps. Do not infer unindexed dependencies from it.
- The DevKit public MCP index transport currently fails/times out even after storage
  bootstrap. Treat scheduler state as `DEGRADED_SKILL_ONLY`: one writer at a time, no
  Relay claim, no cross-session dispatch, and no claim of durable host routing.
- The user authorized broad Spark experimentation, but this migration/publication change
  is not an eligible Spark sprint: it has no reproducible severe blocker and crosses a
  persistent schema/publication boundary. Reserve Spark for a later, bounded, reproduced
  mechanical blocker only; do not use it merely to consume quota.

## Fixed invariants

- The publication floor remains `source_coverage=1`, `anchor_recall=1`,
  `coverage_gap=0`, and `unsupported_critical_claims=0`.
- `strict_audit=false` skips semantic-model review only; it never permits a non-summary
  released record to publish.
- The physical savepoint order is exactly: Capsules, committed Snapshot, membership,
  ledger rows, active-pointer CAS.
- CAS loss rolls all candidate rows back and is the only ledger-adjacent write conflict
  that can transition the job to `SUPERSEDED`. A ledger trigger/check failure propagates
  and leaves the fenced job `READY_TO_COMMIT`.
- No worker, provider, runtime lifecycle, environment configuration, or release version
  changes belong to this plan.

## Work package

Use [the scoped work package](../work-packages/strict-snapshot-reorganization-ledger/index.md)
as the human/agent projection. The coordinator owns the dependency DAG and final
acceptance; a worker reads only its own task card and the shared contract.

## Implementation steps

### Task 1: canonical record contract

**Files:**

- Modify `astrcontinuum/reorganization.py:33-51`.
- Modify `astrcontinuum/__init__.py` only to export the canonicalizer if public tests
  require package-root access.
- Modify `tests/test_reorganization.py`.

- [ ] Add RED tests for `canonicalize_reorganization_records(records)` that accept an
  ordered tuple of valid `ReorganizationRecord` values unchanged and reject: a non-record
  value, blank `kind`/`item_id`/`source_capsule_id`, a non-enum status, boolean or negative
  token counts, `required=True` with `APPROXIMATE` or `RELEASED`, and a duplicate
  `(source_capsule_id, kind, item_id)` key.
- [ ] Implement the smallest pure canonicalizer in `reorganization.py`. It must return a
  tuple in caller order; require exact record/status/boolean runtime types; use
  `str.strip()` only to validate identities without rewriting them; inspect no capsule
  content; and leave `_build_capsule()` and its current `coverage_gap` calculation
  unchanged.
- [ ] Run the focused reorganization tests, then commit only this task's files as
  `feat(reorganization): canonicalize publication records`.

### Task 2: v2 immutable SQLite ledger

**Files:**

- Modify `astrcontinuum/storage/migrations.py:57-411` without editing
  `INITIAL_SCHEMA_SQL`.
- Modify `tests/storage/test_sqlite_migrations.py:81-677`.

- [ ] Add RED migration tests for fresh v2 initialization, v1-to-v2 upgrade, repeat
  migration, v2 checksum drift, the full row constraint matrix, and immutable
  update/delete triggers.
- [ ] Add a separate v2 SQL constant and append
  `Migration(version=2, name="snapshot_reorganization_ledger_v2", ...)` to
  `MIGRATIONS`. Create `snapshot_reorganization_records` with the exact composite primary
  key, source-item unique key, Snapshot foreign key, status/boolean/non-negative checks,
  `(snapshot_id, ordinal)` index, and two `BEFORE UPDATE/DELETE` abort triggers.
- [ ] Update existing migration expectations from schema version 1 to 2; do not alter v1
  SQL because its already-applied checksum is authoritative.
- [ ] Run only the migration test module, then commit only this task's files as
  `feat(storage): migrate snapshot reorganization ledger`.

### Task 3: repository publication and audit reader

**Files:**

- Modify `astrcontinuum/storage/repository.py:800-986` and add nearby private helpers.
- Modify `tests/storage/test_repository_publication.py`.
- Modify `tests/storage/test_repository_crash_atomicity.py`.
- Modify `tests/storage/test_compiler_publication.py` only where its table inventory must
  include the new table.

- [ ] Add RED tests for a successful ordered record round trip, omitted-record backward
  compatibility, malformed record failure without durable writes, non-summary `RELEASED`
  rejection with `QUALITY_COVERAGE_GAP` even with `strict_audit=false`, stale fence,
  pointer-CAS loss, and a `publish.after_ledger` injected crash after a partial ledger
  insert.
- [ ] Extend `publish_snapshot()` with keyword-only
  `reorganization_records: Sequence[ReorganizationRecord] = ()`. Convert the sequence to
  a tuple but invoke the Task 1 canonicalizer only after `_require_live_fence`, job-state,
  candidate-id/session, and membership checks, so stale-fence precedence remains intact.
- [ ] Run the existing `validate_permanent()` call unchanged. If canonical records contain
  any `RELEASED` status whose `kind != "narrative_summary"`, create an immutable derived
  `PermanentValidationReport`: union its failure set with `QUALITY_COVERAGE_GAP`, emit
  enum-order-stable failure codes, set `passed=False`, and set `coverage_gap` to the
  maximum of its former value and the non-summary release count. Raise
  `PublicationRejected` from that report; do not add record parameters to
  `validate_permanent()`.
- [ ] In the existing savepoint, insert rows only after `snapshot_capsules` has been
  inserted. Use the caller index as `ordinal`, store `required` as `0`/`1`, invoke
  `publish.after_ledger` after each row, and let ledger `sqlite3.IntegrityError` propagate
  rather than setting the CAS-conflict flag.
- [ ] Add `read_snapshot_reorganization_records(snapshot_id)` that rejects a missing or
  non-committed Snapshot, selects by Snapshot id ordered by ordinal, and maps every row
  back to the frozen domain record.
- [ ] Run the three storage publication/crash/compiler modules and commit only this task's
  files as `feat(storage): publish immutable reorganization ledger`.

### Task 4: coordinator integration and evidence

**Files:** No production writes unless a scoped regression exposes a defect.

- [ ] Re-sync the project-level DevKit index after the accepted commits, verify the
  required publication/migration paths are present and not stale, then regenerate the
  bounded Atlas regression packet against the resulting snapshot.
- [ ] Run the scoped regression command below, followed by Ruff, mypy for all three changed
  production modules, and `git diff --check`.
- [ ] Inspect `git status --short`, confirm no unrelated dirty path was staged or changed,
  and report exact commits, commands, result counts, Atlas status, and any remaining
  parser/MCP transport limitations.

## Scoped verification command

Run from `D:\bun\tmp\codex\AstrContinuum-runtime-v21-integration` with all temporary
variables under that worktree's D-drive task root:

```powershell
$taskRoot = 'D:\bun\tmp\codex\AstrContinuum-runtime-v21-integration'
$env:CODEX_TASK_TEMP = "$taskRoot\tmp\strict-ledger"
$env:TEMP = $env:CODEX_TASK_TEMP
$env:TMP = $env:CODEX_TASK_TEMP
$env:TMPDIR = $env:CODEX_TASK_TEMP
$env:PYTHONPYCACHEPREFIX = "$taskRoot\tmp\strict-ledger\pycache"
New-Item -ItemType Directory -Force $env:CODEX_TASK_TEMP | Out-Null
& "$taskRoot\.venv\Scripts\python.exe" -m pytest -p no:cacheprovider -q `
  tests/test_reorganization.py `
  tests/test_permanent_validator.py `
  tests/storage/test_sqlite_migrations.py `
  tests/storage/test_repository_publication.py `
  tests/storage/test_repository_crash_atomicity.py `
  tests/storage/test_compiler_publication.py
& "$taskRoot\.venv\Scripts\python.exe" -m ruff check astrcontinuum/reorganization.py astrcontinuum/storage/migrations.py astrcontinuum/storage/repository.py
& "$taskRoot\.venv\Scripts\python.exe" -m mypy astrcontinuum/reorganization.py astrcontinuum/storage/migrations.py astrcontinuum/storage/repository.py
git diff --check
```

Expected result: every scoped pytest test passes, Ruff and mypy exit 0, and
`git diff --check` emits no whitespace error. Do not report an unrun full repository suite
as passing.

## Completion criteria

- Migration v2 is checksum-safe, idempotent, constrained, and immutable.
- Valid records persist atomically only with a committed Snapshot and return in order.
- Rejected, stale, CAS-losing, and injected-crash publications leave no ledger orphan.
- Non-summary released records cannot bypass the permanent quality floor.
- Current scoped verification and index/Atlas evidence are attached to the accepted
  integration state.
