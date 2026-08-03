# Strict Snapshot Reorganization Ledger Design

Status: approved direction, pending implementation review

Date: 2026-08-03

## Goal

Persist an immutable, auditable record of a reorganization **only when** its
Snapshot is successfully published. The ledger supplies provenance for a committed
Snapshot; it does not make a lossy result publishable.

The existing permanent publication floor remains unchanged:

```text
source_coverage = 1
anchor_recall = 1
coverage_gap = 0
unsupported_critical_claims = 0
```

In particular, a non-summary `RELEASED` `ReorganizationRecord` continues to make the
candidate fail `QUALITY_COVERAGE_GAP`. No ledger row, Snapshot, Capsule, membership,
or active-pointer update may survive that rejection.

## Non-goals

- Do not invoke `reorganize_capsules()` from the worker or request lane.
- Do not relax the permanent validator, semantic-audit policy, lease fence, or CAS
  rules.
- Do not add a model provider, credentials, network access, or a local model.
- Do not backfill, alter, or delete historical Snapshots.
- Do not make `APPROXIMATE` or `RELEASED` a new production policy. This card merely
  records already-valid publication provenance; future automatic use needs a separate
  policy decision.

## Chosen approach

The ledger is Snapshot-attached rather than worker-attached.

| Alternative | Decision | Reason |
| --- | --- | --- |
| Worker-local log | Rejected | It disappears on lease recovery and cannot prove what was committed. |
| Independent post-publish table write | Rejected | It can leave a committed Snapshot without its audit record, or an orphan record after CAS loss. |
| Snapshot-attached rows in the publish savepoint | Chosen | Gives the ledger identical atomicity to Capsules, membership, Snapshot, and pointer CAS. |

## Data model

Migration v2 adds `snapshot_reorganization_records`:

| Column | Constraint / meaning |
| --- | --- |
| `snapshot_id` | Non-empty FK to the committed Snapshot; part of the primary key. |
| `ordinal` | Non-negative, ordered position in the caller-supplied canonical record tuple. |
| `source_capsule_id` | Non-empty source Capsule identity recorded by the planner. |
| `kind` | Non-empty reorganization item kind. |
| `item_id` | Non-empty source item identity. |
| `status` | Exactly `retained`, `approximate`, or `released`. |
| `before_tokens`, `after_tokens` | Non-negative integer accounting values. |
| `required` | SQLite boolean (`0` or `1`). |

The primary key is `(snapshot_id, ordinal)`. A unique constraint on
`(snapshot_id, source_capsule_id, kind, item_id)` rejects duplicate disposition for
one source item. Index `snapshot_reorganization_records_snapshot_idx(snapshot_id,
ordinal)` supports ordered audit reads.

Update and delete triggers make ledger rows immutable, matching Capsules, Snapshots,
and Snapshot membership. The migration remains part of the existing checksum ledger;
v1-to-v2 upgrade, duplicate initialization, and checksum drift must all retain the
current fail-closed behavior.

## Publication flow

`SQLiteRepository.publish_snapshot()` gains an optional ordered tuple of
`ReorganizationRecord` values. Existing callers omit it and retain current behavior.

Before the savepoint, repository code validates each supplied record has the closed
shape above, no duplicate source-item key, non-negative accounting, and a retained
status for every `required` record. It must not inspect or log user content. It also
counts non-summary `released` records. After the unchanged permanent candidate
validation returns, the repository overlays `QUALITY_COVERAGE_GAP` on a new immutable
report whenever that count is non-zero. This closes the hand-built-record path without
changing `validate_permanent()` or treating `strict_audit=false` as a quality bypass.

The existing permanent candidate validation runs unchanged. Only after it succeeds,
the savepoint performs this order:

1. insert candidate Capsules;
2. insert the committed Snapshot;
3. insert ordered Snapshot membership;
4. insert the optional ledger rows with the exact input ordinal;
5. execute the existing active-pointer CAS;
6. release the savepoint and mark the job `COMMITTED` only on CAS success.

Both membership and ledger rows immediately reference the committed Snapshot under
SQLite foreign-key enforcement, so this order is required. Every step remains in the
same savepoint: a ledger failure or CAS loss leaves no candidate Snapshot, membership,
or ledger orphan.

CAS loss rolls the savepoint back, including every new ledger row, then retains the
current fenced `SUPERSEDED` behavior. A stale fence, permanent-validation rejection,
or write failure inserts no ledger rows. The ledger never changes Journal events,
active pointer selection, lease state, or request assembly.

Repository code also exposes a read-only, ordered audit read for one committed Snapshot.
It returns only canonical `ReorganizationRecord` fields and rejects a missing or
non-committed Snapshot rather than fabricating provenance.

## Reorganization boundary

`reorganize_capsules()` remains deterministic and worker-local. This card may add a
small canonical record serializer or identity helper so records have one stable storage
representation, but it does not change planning, token accounting, required anchors,
dependencies, or `coverage_gap` calculation.

The permanent validator remains authoritative:

- exact anchors and dependencies remain retained;
- a non-summary release remains unpublishable through `QUALITY_COVERAGE_GAP`;
- `strict_audit=false` still skips only semantic model review;
- recording a ledger never turns a failed candidate into a publishable one.

`approximate` remains an audit disposition only. It neither relaxes existing Capsule
quality fields nor grants a new publication path; the candidate must independently
pass every unchanged permanent validation requirement.

## Failure behavior

| Condition | Required result |
| --- | --- |
| No ledger records | Existing publication behavior, unchanged. |
| Invalid/duplicate record tuple | Fenced publication rejects before durable writes. |
| Permanent validation failure | No Snapshot or ledger rows persist. |
| CAS conflict | No candidate rows or ledger rows persist; job becomes `SUPERSEDED`. |
| Stale owner/epoch | No ledger row or newer job state changes. |
| Ledger trigger/check failure | Whole publish savepoint rolls back. |

## Verification plan

Tests must demonstrate:

1. v1 databases upgrade to v2; repeated migration is idempotent and checksum drift
   remains rejected.
2. A valid permanent candidate plus retained/approximate records commits the Snapshot
   and ordered immutable ledger rows atomically.
3. Existing publication with no records remains byte-for-byte behaviorally compatible.
4. Duplicate, malformed, negative-token, and required-but-not-retained records fail
   without durable candidate or ledger writes.
5. `RELEASED` non-summary reorganization output still fails permanent validation even
   when records are supplied and `strict_audit=false`.
6. Pointer-CAS conflict, stale fence, and injected publication failure leave no orphan
   `snapshot_reorganization_records` rows.
7. Ledger update/delete attempts fail, and ordered read returns the original canonical
   tuple only for a committed Snapshot.

## Implementation scope

Expected write scope after this specification is reviewed:

- `astrcontinuum/reorganization.py`
- `astrcontinuum/storage/migrations.py`
- `astrcontinuum/storage/repository.py`
- `tests/test_reorganization.py`
- `tests/test_permanent_validator.py`
- `tests/storage/test_sqlite_migrations.py`
- `tests/storage/test_repository_publication.py`
- a focused new storage ledger test if the existing test boundaries become unclear.

`main.py`, worker scheduling, model providers, release metadata, and external services
are out of scope.
