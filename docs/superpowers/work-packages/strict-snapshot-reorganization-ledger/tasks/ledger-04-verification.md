# LEDGER-04 Scoped Integration Verification

Owner: coordinator-verifier
Depends on: ledger-03

## Goal

Verify the integrated ledger change, refresh bounded DevKit evidence, and report only
current scoped results.

## Context

- Read `contracts/ledger-publication.md` and the three predecessor commits.
- Use the project-level DevKit index as evidence only; it may remain `INDEX_PARTIAL`.

## Write Scope

- none

## Steps

1. Run the six scoped test modules plus Ruff, mypy, and `git diff --check` from the plan.
2. Re-sync the isolated integration worktree into the project-level DevKit index and confirm
   required paths are current.
3. Recreate the bounded Atlas pytest-regression packet and record whether it is READY,
   EVIDENCE_INCOMPLETE, or unavailable.
4. Inspect the final diff and status; accept only after all scoped evidence is current.

## Acceptance

All commands in `docs/superpowers/plans/2026-08-04-strict-snapshot-reorganization-ledger.md`
exit successfully, no unrelated file is changed, and the result reports real test counts.

## Return

Return exact commits, commands/results, index/Atlas status, remaining limitations, and no
claim of full-suite or cross-session acceptance without that evidence.

## Forbidden

No production edits, auto-archive, release, push, or generated acceptance receipt.
