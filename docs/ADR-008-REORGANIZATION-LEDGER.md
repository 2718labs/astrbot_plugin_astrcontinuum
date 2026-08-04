# ADR-008: Immutable Snapshot Reorganization Ledger

- Status: Accepted for v0.3.0 Technical Preview
- Scope: Snapshot publication auditability and durable loss accounting
- Partially supersedes: the publication ordering and conflict classification in ADR-004 and ADR-007. Their coverage and Capsule persistence decisions remain in force.

## Context

AstrContinuum can reorganize structured Capsule content under a token budget. A worker-local
result must account for the disposition of each source item without giving a lossy result a
route around the permanent publication validator. Before v0.3.0, the record shape existed
only in memory: a published Snapshot could not durably show which source items were retained,
approximated, or released.

This decision must preserve the existing publication boundary while documenting its current
scope precisely. The existing candidate-conflict classifier already maps Capsule/Snapshot/
membership insert integrity collisions and CAS loss to `SUPERSEDED`. v0.3.0 must ensure that
ledger insert integrity failures do not join that classifier. The ordinary Provider-bound worker
and default AstrBot composition leave `reorganization_token_budget` unset and pass no
reorganization records. An opt-in injected compiler backend may set that budget and invoke
`reorganize_capsules()` in the fenced synthetic Gate A validation path. This ADR therefore
keeps the repository/publication contract separate from ordinary background behavior; Gate A is
not a Provider or production claim.

## Decision

### Durable shape and canonicalization

Migration v3 creates `snapshot_reorganization_records`, keyed by `(snapshot_id, ordinal)`
with unique `(snapshot_id, source_capsule_id, kind, item_id)`. Every row records source
identity, `retained`/`approximate`/`released` status, before/after token counts, and whether
the item was required. Rows are append-only through update/delete rejection triggers.

Before publication opens its savepoint, repository canonicalization requires an exact
`ReorganizationRecord` and `ReorganizationStatus`, non-empty identity strings, non-negative
non-boolean token counts, exact boolean `required`, and no duplicate source-item identity.
`required=true` is valid only for `retained`.

### Publication and visibility

`TX_PUBLISH_SNAPSHOT` first validates the live lease fence, candidate shape, memberships,
canonical ledger records, and permanent Snapshot conditions. Within one publication
savepoint, physical writes occur in this order:

1. immutable Capsules;
2. `COMMITTED` Snapshot;
3. ordered `snapshot_capsules` membership;
4. ordered `snapshot_reorganization_records` ledger rows, when explicitly supplied;
5. active-pointer CAS, Job terminal transition, and follow-up intent preservation.

The ordering is intentional: membership and ledger rows both use immediate foreign keys to
the Snapshot. The ledger reader accepts only an existing `COMMITTED` Snapshot and returns
rows in ordinal order; worker-local candidates and rolled-back writes are never visible.

### Permanent quality floor

A `released` record whose `kind` is not `narrative_summary` unconditionally adds
`QUALITY_COVERAGE_GAP` to the permanent validation report. Consequently it rejects
publication even when `strict_audit=false`; optional semantic audit cannot relax this floor.
`narrative_summary` release is accounted for but does not by itself introduce this failure.

### Conflict taxonomy

The existing candidate-conflict classifier maps Capsule/Snapshot/membership insert integrity
collisions and pointer CAS loss to `SUPERSEDED`, rolling back the savepoint and removing every
candidate row including ledger rows. Any ledger insert integrity failure is deliberately
different: it propagates after the outer transaction rolls back, leaves the Job outside that
classifier, and leaves no orphaned ledger data. Therefore, a `SUPERSEDED` outcome is not by
itself proof that a Capsule/Snapshot/membership failure was a CAS race; this limitation is
kept explicit for the Technical Preview.

## Consequences

- Existing databases require the checksum-verified migration plan to reach schema version 3.
- Schema migration versions and Capsule `schema_version` are data contracts; neither is the
  package's v0.3.0 release version.
- v0.3.0 keeps the ordinary Provider-bound/default AstrBot path storage-safety-only: it sends an
  empty record tuple. The separate opt-in injected/fenced Gate A path can call
  `reorganize_capsules()` with an explicit budget and persist its canonical records. That
  synthetic validation path does not assert public v1.0 compatibility, Provider coverage,
  semantic quality, performance benchmarks, or end-user release readiness.
