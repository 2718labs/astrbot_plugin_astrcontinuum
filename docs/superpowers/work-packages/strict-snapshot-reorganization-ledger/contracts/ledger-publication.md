# Ledger Publication Contract

## Record shape

`ReorganizationRecord` is the only record type. Its `kind`, `item_id`, and
`source_capsule_id` are non-empty; status is an exact `ReorganizationStatus`; token counts
are non-boolean non-negative integers; and a required item is retained. The tuple preserves
caller order and cannot repeat a `(source_capsule_id, kind, item_id)` identity.

## Permanent quality closure

The existing `validate_permanent()` interface stays unchanged. After it returns, repository
publication adds `QUALITY_COVERAGE_GAP` to an immutable report for every non-summary
released record, preserves enum failure-code order, and raises its coverage gap to at least
the non-summary release count. An approximate record changes no permanent quality field by
itself.

## Durable order

The publication savepoint inserts Capsules, committed Snapshot, membership, ledger rows,
and then performs pointer CAS. Ledger SQL failures propagate; only existing candidate/CAS
conflicts may enter the `SUPERSEDED` path.

## Read boundary

Ledger reads require an existing committed Snapshot and return canonical records in ordinal
order. Missing and candidate Snapshot ids are rejected.
