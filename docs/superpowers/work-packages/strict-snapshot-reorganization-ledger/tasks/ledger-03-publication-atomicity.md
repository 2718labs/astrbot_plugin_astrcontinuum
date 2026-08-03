# LEDGER-03 Publication Atomicity and Reader

Owner: terra-max-publication-atomicity
Depends on: ledger-01, ledger-02

## Goal

Thread canonical records through fenced Snapshot publication and expose a committed-only
ordered audit reader without permitting a quality bypass or an orphan row.

## Context

- Read `contracts/ledger-publication.md` and the two predecessor commits.
- Route when the verified host is available: `gpt-5.6-terra` at `max` reasoning.
- `astrcontinuum/storage/repository.py:800-986` is the exact publication savepoint.
- `astrcontinuum/domain/validation.py:258-306` supplies the immutable permanent report.
- `tests/storage/test_repository_publication.py:28-807` and
  `tests/storage/test_repository_crash_atomicity.py:182-326` provide test helpers.

## Write Scope

- `astrcontinuum/storage/repository.py`
- `tests/storage/test_repository_publication.py`
- `tests/storage/test_repository_crash_atomicity.py`
- `tests/storage/test_compiler_publication.py` only for its public table inventory

## Steps

1. Write failing tests for ordered successful rows/readback, no-record compatibility,
   malformed tuples, non-summary release rejection, stale fence, pointer loss, and
   `publish.after_ledger` crash rollback.
2. Add the optional keyword-only records argument and canonicalize it only after the live
   fence and current structural validations.
3. Overlay the permanent report for non-summary releases without changing
   `validate_permanent()`.
4. Insert ledger rows after membership, propagate their SQL failure, and add the committed
   reader with ordinal ordering.
5. Extend crash/publication table-count assertions to include the ledger.

## Acceptance

Set all temp variables below `D:\bun\tmp\codex\AstrContinuum-runtime-v21-integration\tmp\strict-ledger`, then run:

`D:\bun\tmp\codex\AstrContinuum-runtime-v21-integration\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests/storage/test_repository_publication.py tests/storage/test_repository_crash_atomicity.py tests/storage/test_compiler_publication.py`

Expected: exit 0, including every no-orphan failure path.

## Return

Return the scoped commit, changed paths, exact command/output, and blockers.

## Forbidden

No migration edits, permanent-validator signature changes, worker/provider work, direct
merge/rebase, or acceptance decision.
