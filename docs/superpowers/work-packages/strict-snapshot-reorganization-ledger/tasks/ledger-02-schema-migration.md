# LEDGER-02 Immutable Ledger Migration

Owner: terra-high-schema-migration
Depends on: none

## Goal

Add checksum-safe v2 storage for immutable ordered reorganization records.

## Context

- Read `contracts/ledger-publication.md`.
- Route when the verified host is available: `gpt-5.6-terra` at `high` reasoning.
- `astrcontinuum/storage/migrations.py:57-403` is immutable v1 SQL.
- `astrcontinuum/storage/migrations.py:405-411` is the append-only migration tuple.
- `tests/storage/test_sqlite_migrations.py:81-677` contains version, drift, constraint,
  immutability, and concurrency coverage.

## Write Scope

- `astrcontinuum/storage/migrations.py`
- `tests/storage/test_sqlite_migrations.py`

## Steps

1. Add failing tests for fresh v2, v1 upgrade/idempotence, v2 checksum drift, table checks,
   FK/unique constraints, two immutable triggers, concurrent initialization, and busy retry.
2. Add a standalone v2 SQL constant. Do not change `INITIAL_SCHEMA_SQL`.
3. Append exactly one version-2 Migration with the table, index, and named immutable triggers.
4. Update old test expectations from version 1 to version 2 and preserve their v1 checksum
   tests unchanged.

## Acceptance

Set all temp variables below `D:\bun\tmp\codex\AstrContinuum-runtime-v21-integration\tmp\strict-ledger`, then run:

`D:\bun\tmp\codex\AstrContinuum-runtime-v21-integration\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests/storage/test_sqlite_migrations.py`

Expected: exit 0; no v1 checksum mutation.

## Return

Return the scoped commit, changed paths, exact command/output, and blockers.

## Forbidden

No repository, runtime, worker, DevKit, backfill, or sibling-card changes.
