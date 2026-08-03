# Strict Snapshot Reorganization Ledger

## Goal

Give every successfully published Snapshot an immutable, ordered audit trail of
reorganization decisions while retaining the existing permanent publication floor.

## Scope

This work adds typed record validation, a v2 SQLite ledger migration, atomic publication
rows, committed-Snapshot audit reads, and focused regression evidence. It does not add a
provider, alter compaction worker lifecycle, relax loss policy, backfill history, or
change release metadata.

## Direction

Records remain worker-produced evidence. The repository validates them only after the
existing live fence, rejects non-summary releases through the permanent quality result,
and persists them after Snapshot membership inside the existing savepoint. The ledger is
append-only and cannot determine active Snapshot selection.

## Risk Gate

Stop for a new public loss policy, an attempt to bypass the permanent validator, an
unanticipated destructive migration, or an Atlas/host route that lacks current
attestation.

## Done

The scoped tests prove ordered successful persistence, backward compatibility, v1-to-v2
upgrade, immutability, no orphan rows under all publication failure paths, and no quality
bypass for non-summary releases.
