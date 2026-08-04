# v0.3.0 Technical Preview Data Flow

This data-flow contract overlays the verified `v0.2.1` runtime with the `v0.3.0`
storage-only reorganization ledger. It describes durable and wired behavior separately: normal
background compaction uses the token-metric lane and publishes an empty reorganization ledger;
only an explicit repository publisher can supply ledger records in this Technical Preview.

## Canonical Records

All flows carry the wire field `session_key`. SQLite uses `session_key_hash`, the SHA-256 hash of
the canonical SessionKey serialization, only as a lookup key; joining `session_key_hash` to
`sessions.canonical_session_key_json` MUST reconstruct the embedded wire `session_key` without
loss. Durable data is stored in `sessions`, `journal_events`, `capsules`, `snapshots`, ordered
`snapshot_capsules`, optional ordered `snapshot_reorganization_records`, `active_snapshots`,
`compaction_jobs`, encrypted `token_metrics`, and content-free
`token_metric_backfill_intents`. Transaction names in this document are stable architecture names
and MUST match `DATABASE_SCHEMA.md` and `CONCURRENCY_STATE_MACHINE.md`.

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
2. The adapter reads the active model and context limit through public
   `Context.get_using_provider()` and Provider metadata. It resolves `AUTO_ASTRBOT`, `MANUAL`,
   or `AUTO_SAFE_FALLBACK=128000`, then freezes one immutable request tokenizer and budget
   profile.
3. `TX_CAPTURE_USER_EVENT` inserts one `journal_events` row with `event_type=USER_MESSAGE`,
   `role=USER`, and `source_hook=ON_LLM_REQUEST`, or returns the existing row for the same
   idempotency tuple. Sequence allocation and insertion MUST be atomic. The same transaction
   writes the canonical Event metric or a matching backfill intent.
4. `TX_READ_REQUEST_VIEW` opens one read transaction, records the committed Journal high-water
   mark `H`, and selects the active Snapshot through `active_snapshots`. If no pointer exists, it
   uses logical `EMPTY_BASE` with `C=0` and selects events `1..H`; `EMPTY_BASE` is not a Schema
   or database Snapshot.
5. One request-scoped counter counts every host component and candidate under the frozen profile.
   Tool call and result pairs are indivisible. The assembler combines the committed Snapshot and
   ordered Delta; `EMERGENCY_ASSEMBLY` MAY reduce prompt material but MUST NOT mutate durable
   records or coverage.
6. The final complete Provider projection is recounted under the same profile. Any tokenizer
   construction or count failure discards all partial primary results and replays the whole
   request from source with `utf8-byte-v1`; BPE and BYTE units never coexist in one request.
7. The AstrBot adapter MAY append a temporary `TextPart` to
   `ProviderRequest.extra_user_content_parts`. Missing internal capability MUST fail open by
   skipping enhancement and recording a redacted error.
8. If compaction is indicated, `TX_RAISE_COMPACTION_INTENT` durably raises
   `intent_target_high_water_mark`. The request hook MUST NOT wait for compilation, audit,
   scheduler completion, or a remote model.

## Completion and Tool Flows

`on_agent_done` executes `TX_CAPTURE_ASSISTANT_EVENT` for authoritative completed assistant
output. `on_using_llm_tool` and `on_llm_tool_respond` use the same idempotent append primitive
for `TOOL_CALL` and `TOOL_RESULT`, respectively. Each source hook MUST write only its mapped
event and role combination. Each new Event commits either its canonical metric or a retryable
backfill intent in the same transaction; callback success cannot leave an untracked metric gap.

`on_llm_response` MAY collect metrics or redacted diagnostics, but MUST NOT append Journal rows.
Compiler, scheduler, or callback failures MUST NOT retroactively remove a captured event.
External Sylanne memory content never enters these flows.

## Durable Compaction Flow

1. `TX_RAISE_COMPACTION_INTENT` persists the maximum requested target for a session without
   lowering an existing target.
2. `TX_CLAIM_JOB` moves eligible `PENDING` or due `RETRY_WAIT` work to `LEASED`, increments
   `lease_epoch`, sets lease fields, and freezes `target_high_water_mark` for this attempt.
3. Before compiling, the worker processes at most one bounded, stable-order batch of missing
   `canonical-o200k-v1` metrics. Each metric and matching intent deletion commit atomically.
4. The fenced worker moves `LEASED -> COMPILING`, reads the expected active Snapshot, the
   contiguous raw Delta ending at `target_high_water_mark`, and the complete canonical Event
   metric set. Missing or unavailable canonical metrics defer the job without consuming an
   ordinary failure attempt.
5. The compiler consumes both inputs and canonical counts, then produces a closed Snapshot plus
   closed structured Capsules. Mechanical structural, identity, source-event, coverage,
   exact-anchor, same-session membership, and non-empty-output checks MUST always run. Sylanne
   memory is not an admissible Capsule source.
6. `v0.3.0` adds an optional repository-publication argument for canonical reorganization records.
   The standard `CompactionWorker` does not invoke `reorganize_capsules()` or supply that argument,
   so ordinary background work publishes an empty ledger. This storage path is not an assertion
   that reorganization is wired or evaluated in the installed plugin.
7. With optional semantic audit enabled, the worker moves `COMPILING -> AUDITING`; otherwise it
   moves directly to `READY_TO_COMMIT`. `strict_audit=false` affects only this optional step.
8. At `READY_TO_COMMIT`, `candidate_snapshot_id` identifies the worker-local immutable wire
   envelope with `state=CANDIDATE`; the worker also holds the closed candidate Capsule envelopes
   and ordered membership. They are Schema-validated but not yet visible to readers.
9. `TX_PUBLISH_SNAPSHOT` checks fencing and the permanent target and coverage relations. A
   publishable envelope MUST have `audit_outcome.mechanical_passed=true`, semantic status
   `NOT_RUN` or `PASSED`, no failure codes, valid same-session Capsule and source membership, and
   exactly one canonical metric for every new Capsule and the Snapshot. If reorganization records
   are explicitly supplied, they must be canonical; every non-`narrative_summary` `released`
   record adds `QUALITY_COVERAGE_GAP` and rejects publication independently of optional semantic
   audit.
10. In one outer transaction, publication opens one savepoint for every new candidate row. It
    inserts new immutable `capsules`, inserts the Snapshot as `COMMITTED`, inserts ordered
    `snapshot_capsules`, writes any explicit ordered `snapshot_reorganization_records`, writes
    canonical metrics, and then performs active-pointer CAS. Bootstrap
    (`base_snapshot_id=null`, `base_pointer_version=0`) conditionally creates the absent
    `active_snapshots` row at version `1`; an existing base conditionally updates the row matching
    its Snapshot and version and increments the version. Both require new coverage to be strictly
    greater than active coverage.
11. Success commits Capsules, Snapshot, membership, optional ledger rows, canonical metrics,
    pointer, and Job atomically. The candidate-conflict classifier maps eligible Capsule,
    Snapshot, or membership insert integrity collisions and pointer conflict to `SUPERSEDED`,
    rolling back the savepoint and removing every candidate write. Ledger-write integrity errors
    and canonical-metric failures instead roll back the outer transaction and propagate; they
    must not be relabelled as `SUPERSEDED`. A stale fence rejects the whole operation instead.
12. Before either terminal branch commits, if durable intent exceeds the winning active coverage,
    the same transaction MUST leave or create follow-up `PENDING` work using the winning Snapshot
    and pointer version.

Journal events appended above the frozen target remain Delta for later views and jobs. Publish
never deletes Journal data.

## Failure and Recovery Flow

A compiler, auditor, callback, or worker exception crosses a scheduler iteration boundary into
`TX_FAIL_JOB`, which persists `error_stage`, `error_code`, and redacted `error_message`, then
selects `RETRY_WAIT` or `FAILED`. The scheduler MUST continue processing unrelated jobs.

An eligible Capsule, Snapshot, or membership insert integrity collision, bootstrap-create race,
or existing-pointer CAS conflict in `TX_PUBLISH_SNAPSHOT` rolls back every newly inserted
candidate Capsule, `snapshot_capsules` membership, optional ledger row, canonical metric, and
unpublished Snapshot through the publish savepoint, then records `SUPERSEDED` in the fenced outer
transaction. The losing worker MUST NOT overwrite the winning Snapshot or leave orphaned
candidate content. Ledger write integrity failures and canonical-metric failures are not part of
that classifier and must instead roll back the entire transaction. If durable intent remains above
winning coverage, the transaction MUST preserve notification by scheduling follow-up work against
that winning base. A stale owner and epoch changes zero rows and cannot record `SUPERSEDED`.

At startup and periodically, `TX_RECOVER_EXPIRED_LEASES` moves expired nonterminal working jobs
to `PENDING`, clears owner and expiry, and preserves monotonic `lease_epoch`. For expired
`READY_TO_COMMIT`, it also clears the worker-local `candidate_snapshot_id`, and the next claimant
recompiles. It MUST NOT change `journal_events`, `snapshots`, or `active_snapshots`.

Tokenizer failure in the live lane replays only the current request in BYTE mode and does not
rewrite durable canonical metrics. Canonical counter failure leaves or preserves a content-free
backfill intent and defers compaction; it never substitutes live or compatibility units.

## Transaction Inventory

| Transaction | Durable responsibility |
| --- | --- |
| `TX_CAPTURE_USER_EVENT` | Idempotent user append, sequence allocation, and canonical metric or intent |
| `TX_CAPTURE_ASSISTANT_EVENT` | Idempotent assistant append, sequence allocation, and canonical metric or intent |
| `TX_CAPTURE_TOOL_EVENT` | Idempotent tool append, sequence allocation, and canonical metric or intent |
| `TX_READ_REQUEST_VIEW` | Consistent active Snapshot, high-water mark, and uncovered Delta read |
| `TX_RAISE_COMPACTION_INTENT` | Monotonic durable trigger coalescing |
| `TX_CLAIM_JOB` | Eligible selection, fencing increment, and lease acquisition |
| `TX_FAIL_JOB` | Persisted error and retry or terminal transition |
| `TX_PUBLISH_SNAPSHOT` | Fenced Capsule, Snapshot, membership, optional-ledger, and canonical-metric publication in one savepoint, active-pointer CAS, Job commit, and follow-up intent |
| `TX_RECOVER_EXPIRED_LEASES` | Startup and periodic requeue without content mutation |
| `TX_BACKFILL_TOKEN_METRICS` | Bounded ownership-checked metric CAS plus matching intent deletion |
