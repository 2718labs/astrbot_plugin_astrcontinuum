# LEDGER-01 Canonical Record Contract

Owner: terra-high-record-contract
Depends on: none

## Goal

Make supplied reorganization records safe, closed, and order-preserving before they can
reach repository publication.

## Context

- Read `contracts/ledger-publication.md`.
- Route when the verified host is available: `gpt-5.6-terra` at `high` reasoning.
- `astrcontinuum/reorganization.py:33-51` defines the current frozen record and status.
- `astrcontinuum/reorganization.py:536-593` already computes non-summary release quality
  for native reorganization output and must not be changed.

## Write Scope

- `astrcontinuum/reorganization.py`
- `astrcontinuum/__init__.py` only if an export is required
- `tests/test_reorganization.py`

## Steps

1. Add failing focused tests for valid order preservation and every invalid shape in the
   shared contract.
2. Add one pure canonicalizer that returns a tuple, requires exact record/status/boolean
   types, rejects blank identities and boolean/negative tokens, preserves caller order, and
   does not serialize item content.
3. Make only any necessary package-root export; do not alter worker, Capsule construction,
   quality computation, or publication code.

## Acceptance

Set all temp variables below `D:\bun\tmp\codex\AstrContinuum-runtime-v21-integration\tmp\strict-ledger`, then run:

`D:\bun\tmp\codex\AstrContinuum-runtime-v21-integration\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests/test_reorganization.py`

Expected: exit 0 with the new invalid-shape cases covered.

## Return

Return the scoped commit, changed paths, exact command/output, and blockers.

## Forbidden

No storage schema, repository, worker, validator, runtime, provider, or sibling-card edits.
