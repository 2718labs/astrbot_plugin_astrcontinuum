# Phase 0 Concurrency State Machine

## Correctness Boundaries

SQLite transactions, uniqueness constraints, active-pointer CAS, and monotonically increasing `lease_epoch` are the concurrency boundaries. In-memory queues and locks MUST NOT be required for correctness. All worker mutations MUST be fenced by both `lease_owner` and `lease_epoch`.

## Job Transitions

| From | Event or guard | To | Atomic action |
| --- | --- | --- | --- |
| `PENDING` | eligible claim | `LEASED` | `TX_CLAIM_JOB` increments epoch and establishes lease |
| `RETRY_WAIT` | `next_retry_at <= now` | `LEASED` | `TX_CLAIM_JOB` increments epoch and establishes lease |
| `LEASED` | fenced worker starts | `COMPILING` | owner/epoch conditional update |
| `COMPILING` | mechanical checks pass; audit enabled | `AUDITING` | owner/epoch conditional update |
| `COMPILING` | mechanical checks pass; audit disabled | `READY_TO_COMMIT` | persist preallocated candidate id |
| `AUDITING` | audit accepts | `READY_TO_COMMIT` | persist preallocated candidate id |
| working | retryable exception | `RETRY_WAIT` | `TX_FAIL_JOB` persists error/backoff and clears lease |
| working | fatal/exhausted exception | `FAILED` | `TX_FAIL_JOB` persists error and clears lease |
| `READY_TO_COMMIT` | fence and bootstrap-create/existing-update CAS succeed | `COMMITTED` | `TX_PUBLISH_SNAPSHOT` commits candidate Capsules, Snapshot, ordered membership, supplied reorganization ledger rows, strictly advanced pointer, and job |
| `READY_TO_COMMIT` | fence valid; Capsule/Snapshot/membership insert integrity collision or pointer CAS conflict | `SUPERSEDED` | roll back every new candidate Capsule, Snapshot, membership, and ledger row; retain candidate id; persist terminal conflict and any higher-intent follow-up |
| `PENDING`/`RETRY_WAIT` | administrative cancellation | `CANCELLED` | conditional terminal update |
| expired working | recovery scan | `PENDING` | `TX_RECOVER_EXPIRED_LEASES` clears owner/expiry, keeps epoch; from `READY_TO_COMMIT` also clears candidate id |

Transitions not listed above MUST fail without changing durable state. `strict_audit=false` removes only the `AUDITING` branch; mechanical checks remain mandatory.

## Duplicate and Concurrent Triggers

`TX_RAISE_COMPACTION_INTENT(session, trigger_high_water)` MUST compute `intent_target_high_water_mark = max(existing, trigger_high_water)`. For an active job, it MUST NOT rewrite the attempt's frozen `target_high_water_mark`. Duplicate triggers are idempotent, and a later trigger cannot be lost.

Both publish outcomes compare intent with the winning active `covered_event_end`. On `COMMITTED` the winner is the new Snapshot; on `SUPERSEDED` it is the concurrent winner. If intent is greater, the outer transaction MUST leave or create follow-up `PENDING` work based on the winner's `snapshot_id` and `pointer_version`. Neither outcome may lose notification.

## Competing Claims and Expired Workers

Only one claimant can conditionally change an eligible row to `LEASED`. Each successful claim increments `lease_epoch` to at least `1`; the token never decreases or resets, including after recovery. Every working state requires `lease_epoch >= 1`. Lease renewal and every state transition require matching owner, epoch, and an unexpired lease where applicable.

An expired worker may continue computing in memory, but its next database mutation MUST affect zero rows. It MUST NOT publish, record a failure against the new owner, renew the new lease, or clear new state. Recovery from expired `READY_TO_COMMIT` clears its worker-local `candidate_snapshot_id`; after a new claim, the new epoch MUST compile and validate a fresh candidate. Only the new epoch is authoritative.

## Concurrent Publication

A worker records expected `base_snapshot_id` and `base_pointer_version`.
`TX_PUBLISH_SNAPSHOT` verifies fencing and permanent
identity/source/coverage/audit conditions, including strict coverage advance, canonical
ledger records, and the non-summary `released` quality floor, then opens an inner
savepoint inside the outer publish transaction. New immutable `capsules`, the committed
Snapshot, ordered `snapshot_capsules` membership, and any supplied ordered
`snapshot_reorganization_records` are inserted in that order in the same savepoint before
pointer CAS. Existing immutable base Capsules may be referenced but are not rewritten. For bootstrap null/`0`, CAS conditionally creates the absent pointer at
version `1`. For an existing non-null base/version `>=1`, CAS conditionally updates the
matching row and increments its version. Capsule insert, Snapshot insert, membership,
ledger insert, pointer change, and job `COMMITTED` transition MUST commit as one success
branch.

If another worker wins the same coverage prefix, the loser may hit
`UNIQUE(session_key_hash, covered_event_end)` during Snapshot insert before reaching
pointer CAS; a bootstrap create or existing-pointer update may instead fail at CAS. Those
conditions, along with Capsule/Snapshot/membership insert integrity collisions handled by
the current candidate-conflict classifier, take the `SUPERSEDED` path rather than escaping
as an unhandled worker exception.
The worker MUST roll back to the inner savepoint so every new candidate Capsule,
membership, ledger, and unpublished Snapshot row is removed, then retain
`candidate_snapshot_id`, transition its fenced job to `SUPERSEDED`, and clear the lease.
Before committing the outer transaction it MUST read the winner; if durable intent
exceeds winning coverage, it MUST create or retain `PENDING` follow-up work using the
winning base/version. It MUST NOT retry the old candidate because it used a stale base.
A stale owner/epoch rejects the entire transition and MUST NOT persist `SUPERSEDED`.
An integrity failure while inserting ledger rows is not a publish conflict and MUST
propagate after rolling back the whole outer transaction.

## Event Concurrency

Capture transactions serialize per-session sequence allocation through `sessions.next_event_sequence`. `UNIQUE(session_key_hash, source_hook, idempotency_key)` makes duplicate hook delivery return one event, while `UNIQUE(session_key_hash, sequence)` prevents ordering collisions.

The only valid event/role/hook triples are `USER_MESSAGE/USER/ON_LLM_REQUEST`, `ASSISTANT_MESSAGE/ASSISTANT/ON_AGENT_DONE`, `TOOL_CALL/TOOL/ON_USING_LLM_TOOL`, and `TOOL_RESULT/TOOL/ON_LLM_TOOL_RESPOND`. `ON_LLM_RESPONSE` MUST remain observational. Multiple plugin runners MUST converge through database constraints, not process affinity.

## Read Isolation

`TX_READ_REQUEST_VIEW` fixes the active pointer and Journal high-water `H` in one read transaction. Its normal view is the pointed committed Snapshot plus events `covered_event_end < sequence <= H`. Before the first publish it observes no active pointer and uses logical `EMPTY_BASE` with `C=0` plus events `1..H`; `EMPTY_BASE` is not a Snapshot row. A concurrent append above `H` waits for the next view; a concurrent publish is observed wholly before or wholly after, never as a mixed Snapshot/Delta boundary.

Worker-local candidate data has wire `state=CANDIDATE` and is not reader-visible.
`TX_PUBLISH_SNAPSHOT` inserts validated Capsules, `state=COMMITTED` Snapshot rows, ordered
membership, and supplied ordered ledger rows only inside the publication savepoint. Readers resolve
Snapshots only through `active_snapshots` and load Capsule ids through committed
`snapshot_capsules`; the reorganization-ledger reader accepts only a committed Snapshot.
Partial or rolled-back candidates are invisible.

## Scheduler Isolation and Recovery

The scheduler MUST catch compiler, auditor, callback, and worker exceptions per iteration, persist them with `TX_FAIL_JOB`, and continue claiming unrelated work. Startup and periodic recovery MUST requeue expired nonterminal leases without mutating committed Snapshot or Journal rows.
