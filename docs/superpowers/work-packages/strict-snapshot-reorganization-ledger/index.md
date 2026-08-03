# Strict Snapshot Reorganization Ledger Work Index

## Shared Contracts

- `contracts/ledger-publication.md`

## Tasks

- `tasks/ledger-01-record-contract.md`: ready
- `tasks/ledger-02-schema-migration.md`: ready
- `tasks/ledger-03-publication-atomicity.md`: pending, depends on ledger-01 and ledger-02
- `tasks/ledger-04-verification.md`: pending, depends on ledger-03

## Dispatch

- Current wave: ledger-01, ledger-02
- Write conflicts: none between the two cards; ledger-03 waits for both.
- Runtime mode: DEGRADED_SKILL_ONLY because DevKit MCP index transport is not currently
  attested as usable. One writer only; do not create a cross-session worker, Relay claim,
  or Spark sprint.
- Index basis: project-level DevKit snapshot of the isolated integration worktree. It is
  partial by recorded parser gaps, so every writer must retain the task card's explicit
  source anchors.
- Next gate: both current-wave commits and focused evidence must exist before ledger-03.
