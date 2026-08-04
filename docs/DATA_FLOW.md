# Phase 0 Data Flow

## Canonical Records

All flows carry the wire field `session_key`. SQLite uses `session_key_hash`, the SHA-256 hash of the canonical SessionKey serialization, only as a lookup key; joining `session_key_hash` to `sessions.canonical_session_key_json` MUST reconstruct the embedded wire `session_key` without loss. Durable data is stored in `sessions`, `journal_events`, `capsules`, `snapshots`, ordered `snapshot_capsules`, ordered `snapshot_reorganization_records`, `active_snapshots`, and `compaction_jobs`. Transaction names in this document are stable architecture names and MUST match `DATABASE_SCHEMA.md` and `CONCURRENCY_STATE_MACHINE.md`.

Journal wire combinations MUST be constrained as follows:

| `event_type` | `role` | sole `source_hook` |
| --- | --- | --- |
| `USER_MESSAGE` | `USER` | `ON_LLM_REQUEST` |
| `ASSISTANT_MESSAGE` | `ASSISTANT` | `ON_AGENT_DONE` |
| `TOOL_CALL` | `TOOL` | `ON_USING_LLM_TOOL` |
| `TOOL_RESULT` | `TOOL` | `ON_LLM_TOOL_RESPOND` |

`ON_LLM_RESPONSE` is observational and is not a Journal `source_hook`.

## User Request Flow

1. `on_llm_request` derives the seven-component SessionKey and its `session_key_hash`.
2. `TX_CAPTURE_USER_EVENT` inserts one `journal_events` row with `event_type=USER_MESSAGE`, `role=USER`, and `source_hook=ON_LLM_REQUEST`, or returns the existing row for the same idempotency tuple. Sequence allocation and insertion MUST be atomic.
3. `TX_READ_REQUEST_VIEW` opens one read transaction, records the committed Journal high-water mark `H`, and selects the active Snapshot through `active_snapshots`. If no pointer exists, it uses logical `EMPTY_BASE` with `C=0` and selects events `1..H`; `EMPTY_BASE` is not a Schema or database Snapshot.
4. The assembler combines the committed Snapshot and ordered Delta. `EMERGENCY_ASSEMBLY` MAY reduce prompt material but MUST NOT mutate durable records or coverage.
5. The AstrBot adapter MAY append a temporary `TextPart` to `ProviderRequest.extra_user_content_parts`. Missing internal capability MUST fail open by skipping enhancement and recording a redacted error.
6. If compaction is indicated, `TX_RAISE_COMPACTION_INTENT` durably raises `intent_target_high_water_mark`. The request hook MUST NOT wait for compilation, audit, scheduler completion, or a remote model.

## Completion and Tool Flows

`on_agent_done` executes `TX_CAPTURE_ASSISTANT_EVENT` for authoritative completed assistant output. `on_using_llm_tool` and `on_llm_tool_respond` use the same idempotent append primitive for `TOOL_CALL` and `TOOL_RESULT` respectively. Each source hook MUST write only its mapped event/role combination.

`on_llm_response` MAY collect metrics or redacted diagnostics, but MUST NOT append Journal rows. Compiler, scheduler, or callback failures MUST NOT retroactively remove a captured event. External Sylanne memory content never enters these flows.

## Durable Compaction Flow

1. `TX_RAISE_COMPACTION_INTENT` persists the maximum requested target for a session without lowering an existing target.
2. `TX_CLAIM_JOB` moves eligible `PENDING` or due `RETRY_WAIT` work to `LEASED`, increments `lease_epoch`, sets lease fields, and freezes `target_high_water_mark` for this attempt.
3. The fenced worker moves `LEASED -> COMPILING`, reads the expected active Snapshot, and reads the contiguous raw Delta ending at `target_high_water_mark`.
4. The compiler consumes both inputs and produces a closed Snapshot plus closed structured Capsules. A reorganization worker may additionally produce ordered source-item audit records. The standard `CompactionWorker` does not yet invoke that reorganization path and therefore publishes an empty record tuple. Mechanical structural, identity, source-event, coverage, exact-anchor, same-session membership, and non-empty-output checks MUST always run. Sylanne memory is not an admissible Capsule source.
5. With optional semantic audit enabled, the worker moves `COMPILING -> AUDITING`; otherwise it moves directly to `READY_TO_COMMIT`. `strict_audit=false` affects only this optional step.
6. At `READY_TO_COMMIT`, `candidate_snapshot_id` identifies the worker-local immutable wire envelope with `state=CANDIDATE`; the worker also holds the closed candidate Capsule envelopes and ordered membership. They are Schema-validated but not yet visible to readers.
7. `TX_PUBLISH_SNAPSHOT` checks fencing and the permanent target/coverage relations. A publishable envelope MUST have `audit_outcome.mechanical_passed=true`, semantic status `NOT_RUN` or `PASSED`, no failure codes, valid same-session Capsule/source membership, and canonical reorganization records. Any non-`narrative_summary` `released` record adds `QUALITY_COVERAGE_GAP` and rejects publication independently of optional semantic audit.
8. In one outer transaction, publish opens the same savepoint for every new candidate row. It inserts new immutable `capsules`, inserts the Snapshot as `COMMITTED`, inserts ordered `snapshot_capsules`, inserts ordered `snapshot_reorganization_records` when supplied, and then performs active-pointer CAS. Bootstrap (`base_snapshot_id=null`, `base_pointer_version=0`) conditionally creates the absent `active_snapshots` row at version `1`; an existing base conditionally updates the row matching its snapshot/version and increments the version. Both require new coverage to be strictly greater than active coverage.
9. Success commits Capsules, Snapshot, membership, ledger, pointer, and job atomically. The current candidate-conflict classifier maps Capsule/Snapshot/membership insert integrity collisions and pointer conflict to `SUPERSEDED`, rolling back the savepoint and removing every new candidate Capsule, Snapshot, membership, and ledger row; the fenced outer transaction retains `candidate_snapshot_id` without moving the winning pointer. A ledger write integrity error instead rolls back the whole publication transaction and propagates; it must not be relabeled as `SUPERSEDED`. A stale fence rejects the whole operation instead.
10. Before either terminal branch commits, if durable intent exceeds the winning active coverage, the same transaction MUST leave or create follow-up `PENDING` work using the winning Snapshot and pointer version.

Journal events appended above the frozen target remain Delta for later views and jobs. Publish never deletes Journal data.

## Failure and Recovery Flow

A compiler, auditor, callback, or worker exception crosses a scheduler iteration boundary into `TX_FAIL_JOB`, which persists `error_stage`, `error_code`, and redacted `error_message`, then selects `RETRY_WAIT` or `FAILED`. The scheduler MUST continue processing unrelated jobs.

A Capsule/Snapshot/membership insert integrity collision, bootstrap-create race, or
existing-pointer CAS conflict in `TX_PUBLISH_SNAPSHOT` follows the current candidate
conflict classifier: it rolls back every newly inserted candidate Capsule,
`snapshot_capsules` membership, ledger row, and unpublished Snapshot through the publish
savepoint, then records `SUPERSEDED` in the fenced outer transaction. The losing worker
MUST NOT overwrite the winning Snapshot or leave orphaned candidate content. Ledger write
integrity failures are not part of that classifier and must instead roll back the entire
transaction.
If durable intent remains above winning coverage, the transaction MUST preserve
notification by scheduling follow-up work against that winning base. A stale owner/epoch
changes zero rows and cannot record `SUPERSEDED`.

At startup and periodically, `TX_RECOVER_EXPIRED_LEASES` moves expired nonterminal working jobs to `PENDING`, clears owner/expiry, and preserves monotonic `lease_epoch`. For expired `READY_TO_COMMIT` it also clears the worker-local `candidate_snapshot_id`, and the next claimant recompiles. It MUST NOT change `journal_events`, `snapshots`, or `active_snapshots`.

## Transaction Inventory

| Transaction | Durable responsibility |
| --- | --- |
| `TX_CAPTURE_USER_EVENT` | Idempotent user append and per-session sequence allocation |
| `TX_CAPTURE_ASSISTANT_EVENT` | Idempotent assistant append and per-session sequence allocation |
| `TX_CAPTURE_TOOL_EVENT` | Idempotent tool call/result append and per-session sequence allocation |
| `TX_READ_REQUEST_VIEW` | Consistent active Snapshot, high-water mark, and uncovered Delta read |
| `TX_RAISE_COMPACTION_INTENT` | Monotonic durable trigger coalescing |
| `TX_CLAIM_JOB` | Eligible selection, fencing increment, and lease acquisition |
| `TX_FAIL_JOB` | Persisted error and retry/terminal transition |
| `TX_PUBLISH_SNAPSHOT` | Fenced Capsule/Snapshot/membership/ledger insert in one savepoint, active-pointer CAS, job commit, and follow-up intent |
| `TX_RECOVER_EXPIRED_LEASES` | Startup/periodic requeue without content mutation |
