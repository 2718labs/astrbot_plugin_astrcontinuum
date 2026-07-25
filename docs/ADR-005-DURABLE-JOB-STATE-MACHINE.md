# ADR-005: Durable Compaction Job State Machine

- Status: Accepted for Phase 0
- Scope: durable intent, leases, retries, and publication

## Stable States

`compaction_jobs.state` MUST use only these stable wire values:

- `PENDING`: durable work exists and has no active lease.
- `LEASED`: a worker owns an unexpired lease and has not begun compilation.
- `COMPILING`: the worker is building and mechanically checking a candidate.
- `AUDITING`: optional model-based semantic review is running.
- `READY_TO_COMMIT`: all required checks passed and `candidate_snapshot_id` is allocated.
- `RETRY_WAIT`: a retryable failure is persisted with `next_retry_at` and no active lease.
- `COMMITTED`: publication and job completion committed atomically.
- `SUPERSEDED`: active-pointer CAS lost to a newer committed Snapshot; the losing `candidate_snapshot_id` is retained as failure evidence.
- `FAILED`: a non-retryable failure or exhausted retry policy is persisted.
- `CANCELLED`: an explicit administrative decision ended pending work.

Terminal states are `COMMITTED`, `SUPERSEDED`, `FAILED`, and `CANCELLED`. Working states are `LEASED`, `COMPILING`, `AUDITING`, and `READY_TO_COMMIT`.

## Durable Intent and Claim

`TX_RAISE_COMPACTION_INTENT` MUST atomically set `intent_target_high_water_mark` to the maximum of its current value and the trigger high-water mark. Concurrent or duplicate triggers MUST NOT lower the intent or create conflicting active work for the same session.

`TX_CLAIM_JOB` MUST select eligible `PENDING` work, or due `RETRY_WAIT` work, and move it to `LEASED`. Every successful claim MUST increment `lease_epoch` monotonically to a value of at least `1`, set `lease_owner`, set `lease_expires_at`, increment `attempt_count`, and freeze `target_high_water_mark` for that attempt. Every working state MUST have `lease_epoch >= 1`. A worker MUST supply both `lease_owner` and `lease_epoch` for every working-state transition, failure record, lease renewal, and publish attempt.

An expired or displaced worker MUST NOT publish, change a newer lease, or clear a newer worker's error. The database fencing predicate is authoritative even if an old process still runs.

## Allowed Transitions

The implementation MUST allow only:

- `PENDING -> LEASED` and due `RETRY_WAIT -> LEASED` through `TX_CLAIM_JOB`;
- `LEASED -> COMPILING`;
- `COMPILING -> AUDITING` when optional semantic audit is enabled;
- `COMPILING -> READY_TO_COMMIT` when it is disabled and all mechanical checks pass;
- `AUDITING -> READY_TO_COMMIT` after audit acceptance;
- any working state -> `RETRY_WAIT` or `FAILED` through `TX_FAIL_JOB`;
- `READY_TO_COMMIT -> COMMITTED` through successful `TX_PUBLISH_SNAPSHOT`;
- `READY_TO_COMMIT -> SUPERSEDED` after a publish CAS conflict;
- `PENDING` or `RETRY_WAIT` -> `CANCELLED` by explicit administration;
- an expired nonterminal working state -> `PENDING` through `TX_RECOVER_EXPIRED_LEASES`.

All other transitions MUST be rejected and persisted state MUST remain unchanged.

## Failure Isolation

Compiler, auditor, callback, and worker exceptions MUST be caught at the scheduler iteration boundary and persisted on the affected job as `error_stage`, `error_code`, and a redacted `error_message`. A retryable exception MUST transition to `RETRY_WAIT`; a non-retryable or exhausted failure MUST transition to `FAILED`. One job failure MUST NOT terminate the scheduler loop or prevent unrelated sessions from being claimed.

`strict_audit=false` bypasses only the `AUDITING` transition. It MUST NOT bypass mechanical checks in `COMPILING`.

## Recovery and Follow-up

At startup and periodically, `TX_RECOVER_EXPIRED_LEASES` MUST requeue expired nonterminal leases by moving them to `PENDING`, clearing `lease_owner` and `lease_expires_at`, and preserving the monotonic `lease_epoch`. When the expired state is `READY_TO_COMMIT`, recovery MUST also clear `candidate_snapshot_id` because its candidate payload was worker-local; the next claim MUST compile and validate a new candidate. Recovery MUST NOT mutate `journal_events`, `snapshots`, or `active_snapshots`.

After `TX_PUBLISH_SNAPSHOT`, both terminal branches MUST compare `intent_target_high_water_mark` with the winning active Snapshot coverage. On `COMMITTED`, that is the newly published Snapshot; on `SUPERSEDED`, it is the concurrently winning Snapshot. If intent is greater, the same outer transaction MUST durably leave or create follow-up `PENDING` work using the winning `base_snapshot_id` and `base_pointer_version`. It MUST NOT treat either terminal result as satisfying a later trigger or lose the notification.
