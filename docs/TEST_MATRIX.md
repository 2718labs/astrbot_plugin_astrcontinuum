# Phase 0 Test Matrix

## Test Levels

- Unit: pure identity, validation, state-transition, and assembly behavior.
- Integration: real SQLite transactions, constraints, fencing, and concurrent connections.
- Crash recovery: process loss at transaction/state boundaries and startup requeue.
- AstrBot smoke: verified host hooks and adapter capabilities on supported AstrBot versions.

Every normative test MUST assert durable rows and state, not only return values or logs.

## Runtime Invariants

| Invariant | Required tests | Level |
| --- | --- | --- |
| INV-001 | Instrument `on_llm_request`; assert it captures the user event and reads committed state without awaiting compiler, auditor, scheduler completion, or remote model. A blocked worker must not block the hook. | Unit + AstrBot smoke |
| INV-002 | Seed active coverage `40`, append `41..44`, and read at `H=44`; assert exactly one committed active Snapshot plus ordered `41..44`. Also read a new session with no pointer: assert logical `EMPTY_BASE`, `C=0`, events `1..H`, and no fabricated Snapshot row. Concurrent append/publish must yield an all-before or all-after view. | Integration |
| INV-003 | Inject failures after each candidate Capsule, Snapshot, and membership insertion stage and before pointer/job completion; assert rollback leaves no candidate content and an unchanged pointer. For CAS conflict, assert rollback-to-savepoint removes every new Capsule, membership, and Snapshot row while the fenced outer transaction persists `SUPERSEDED` and retains candidate id. Readers never observe the candidate. | Integration + crash recovery |
| INV-004 | Force `EMERGENCY_ASSEMBLY`; assert prompt material shrinks while Journal row count, sequences, active pointer, and coverage remain unchanged. | Unit + integration |
| INV-005 | Golden-test canonical tuple order, null `group_id`/`persona_id`, canonical JSON, and SHA-256 stability. Assert any component change changes lookup identity. | Unit |
| INV-006 | Deliver duplicate/concurrent `on_llm_request` and `on_agent_done` callbacks plus `on_llm_response`; assert one user row, one assistant row, and zero response-hook Journal rows. | Integration + AstrBot smoke |
| INV-007 | Supply persona id and external Sylanne memory text; assert only persona metadata is stored and memory payload is absent from all Journal content. | Unit + integration |
| INV-008 | Compile from active Snapshot plus contiguous Delta; reject missing middle event, wrong first/last sequence, stale base, dropped exact anchor, dropped prior semantics, and coverage beyond target. | Unit + integration |
| INV-009 | Parameterize `strict_audit`; assert structural, identity, coverage, exact-anchor, and non-empty checks always execute, while only optional model audit is skipped when false. | Unit |
| INV-010 | Run competing claims and expired-worker publication; assert monotonic epochs and stale rejection. Raise intent during active work; assert follow-up `PENDING` work after both `COMMITTED` and `SUPERSEDED` outcomes whenever intent exceeds winning active coverage. | Integration + crash recovery |

## State and Failure Transitions

| Transition or failure | Required assertion | Level |
| --- | --- | --- |
| `PENDING -> LEASED` | Exactly one claimant wins; owner/expiry set, epoch and attempt increment, target frozen. | Integration |
| due `RETRY_WAIT -> LEASED` | Claim before due time fails; due claim succeeds with a higher epoch. | Unit + integration |
| `LEASED -> COMPILING` | Matching owner/epoch succeeds; mismatched or expired lease changes zero rows. | Integration |
| `COMPILING -> AUDITING` | Occurs only when semantic audit is enabled and mechanical checks passed. | Unit |
| `COMPILING -> READY_TO_COMMIT` | With audit disabled, candidate id is required and every mechanical check has passed. | Unit + integration |
| `AUDITING -> READY_TO_COMMIT` | Accepted audit records outcome and candidate id; rejection cannot enter ready state. | Unit + integration |
| working -> `RETRY_WAIT` | Retryable compiler/auditor/callback/worker exception persists stage/code/redacted message, backoff, and clears lease. Scheduler processes another job. | Integration |
| working -> `FAILED` | Fatal or exhausted exception persists terminal error and clears lease; scheduler remains alive. | Integration |
| `READY_TO_COMMIT -> COMMITTED` | Snapshot insert, pointer CAS, job commit, and lease clear are atomic; committed timestamp and candidate id are present. Snapshot audit has mechanical true, semantic `NOT_RUN` or `PASSED`, and no failures. | Integration + crash recovery |
| `READY_TO_COMMIT -> SUPERSEDED` | Exercise same-prefix Snapshot uniqueness, competing bootstrap create, and existing-pointer update conflicts. Each rolls back only the losing Snapshot insert through savepoint/equivalent; the outer transaction persists `SUPERSEDED`, retains candidate id, clears lease, and leaves the winner unchanged. If intent exceeds winning coverage, assert `PENDING` follow-up uses the winning base/version. | Integration |
| `PENDING`/`RETRY_WAIT -> CANCELLED` | Explicit administration succeeds; working/terminal cancellation is rejected. | Unit + integration |
| expired working -> `PENDING` | Startup recovery clears owner/expiry, preserves epoch, and does not mutate Snapshot or Journal rows. From expired `READY_TO_COMMIT` it also clears candidate id; the next claim recompiles and validates a fresh candidate. | Crash recovery |
| forbidden transition | Each unlisted state pair is rejected with no durable mutation. | Unit + integration |

## Database and Flow Contracts

The event-enum test fixture MUST accept exactly `USER_MESSAGE/USER/ON_LLM_REQUEST`, `ASSISTANT_MESSAGE/ASSISTANT/ON_AGENT_DONE`, `TOOL_CALL/TOOL/ON_USING_LLM_TOOL`, and `TOOL_RESULT/TOOL/ON_LLM_TOOL_RESPOND`.

| Contract | Required tests | Level |
| --- | --- | --- |
| Event enum mapping | Accept exactly four `event_type/role/source_hook` triples; reject all cross-pairings and `ON_LLM_RESPONSE` as a Journal source. | Unit + integration |
| Exact wire fields | For Event, Snapshot, and Job, assert required field names exactly match their Schema and reject unknown properties under closed-object validation. | Unit/schema |
| Capsule closed envelope | Validate the full seven-component SessionKey, exact required top-level fields, and every nested object with unknown-property rejection. Persist and reload one Capsule; assert structured semantic arrays, version/time, quality, aggregate source ids, and nested provenance round-trip exactly. | Unit/schema + integration |
| Capsule membership integrity | Insert ordered `snapshot_capsules` rows and assert durable ordinal order, primary-key and duplicate-Capsule rejection. Reject cross-session Capsule membership, dangling Capsule/source-event references, out-of-coverage source ids, and exact-anchor membership inconsistent with Capsule content. | Integration |
| Candidate Capsule rollback | Inject process loss after each candidate insert stage: new Capsule rows, committed-form Snapshot row, and each ordered membership stage. After recovery assert no partial candidate rows or pointer change. Separately force same-prefix and pointer CAS loss; assert every new candidate row is rolled back before the fenced job commits `SUPERSEDED`, while a stale fence commits nothing. | Integration + crash recovery |
| Narrative-only regression | Use a compiler fixture with empty `narrative_summary` and assert closed-Schema rejection does not erase its structured inputs; then persist a Capsule with an intentionally unhelpful non-empty navigation summary and assert goals, constraints, decisions, progress, open loops, preferences, entities, emotional context, anchors, dependencies, and source ids survive unchanged. | Unit/schema + integration |
| Conditional wire states | Assert every Job field is required and nullable values are explicit null. Base null iff pointer version `0`; non-null base requires version `>=1`. Candidate id is non-null only for `READY_TO_COMMIT/COMMITTED/SUPERSEDED`. Error stage/code/message are all null or all non-empty strings; reject every partial tuple. `RETRY_WAIT/FAILED` require the non-empty form, while another state may retain the complete redacted tuple from a prior attempt. Retry time is non-null only for `RETRY_WAIT`; committed time only for `COMMITTED`. Working states require owner/expiry and epoch `>=1`. Snapshot accepts only `CANDIDATE` with `committed_at=null` or `COMMITTED` with non-null RFC 3339 `committed_at`. | Unit/schema |
| SQLite round-trip | Persist and reload each envelope; assert embedded `session_key` and wire `state/capsule_ids/exact_anchor_ids/audit_outcome` round-trip exactly while `session_key_hash/lifecycle_state/*_json` remain physical-only. Reconstruct `capsule_ids` solely from `snapshot_capsules` ordered by ordinal. | Integration |
| Bootstrap request | With no `active_snapshots` row, assert `EMPTY_BASE`/`C=0` and Delta `1..H`; assert no Snapshot row or pointer is created by assembly. | Integration |
| Active pointer CAS | For null base/version `0`, assert publish conditionally creates the absent pointer at version `1`; two bootstrap/same-prefix publishers yield one `COMMITTED` and one `SUPERSEDED` whether the loser conflicts at Snapshot uniqueness or pointer create. For an existing base/version `>=1`, assert conditional update increments version and coverage strictly; equal or lower coverage is rejected. | Integration |
| Candidate projection | Validate worker-local `state=CANDIDATE` and candidate Capsules without exposing them; publish inserts immutable Capsules, ordered membership, and only `state=COMMITTED` Snapshot rows in one savepoint. Expired ready work clears candidate id before recompilation. | Integration + crash recovery |
| Permanent cross-field checks | Reject publish unless `covered_event_end = source_high_water_mark = target_high_water_mark`, target is strictly greater than active/logical coverage, and the COMMITTED audit outcome is mechanically successful with no failure codes. Run with `strict_audit` both true and false. | Unit + integration |
| User idempotency | Duplicate `ON_LLM_REQUEST` key returns existing event and does not consume another sequence. | Integration |
| Assistant idempotency | Duplicate `ON_AGENT_DONE` key returns existing event and does not consume another sequence. | Integration |
| Tool idempotency | Duplicate `ON_USING_LLM_TOOL` and `ON_LLM_TOOL_RESPOND` keys each produce one correctly mapped event. | Integration + AstrBot smoke |
| Per-session ordering | Concurrent user, assistant, and tool inserts produce unique contiguous sequences; different sessions retain separate identity. | Integration |
| Append-only Journal | Update/delete paths are absent or rejected; compaction and emergency assembly preserve all rows. | Integration |
| Active uniqueness | Constraints prevent two active pointers per session and a pointer to a missing/non-committed Snapshot. | Integration |
| Trigger coalescing | Duplicate/lower trigger does not lower intent; higher concurrent trigger wins without changing current frozen target. | Integration |
| Follow-up scheduling | Commit target `43` while intent is `44`; assert coverage `43` and durable follow-up target `44`. Then force CAS conflict against a winner covering `42` with intent `44`; assert losing job is `SUPERSEDED` and follow-up target `44` uses the winner's Snapshot/version. | Integration + crash recovery |
| Read high-water | Append above captured `H`; assert exclusion from current view and inclusion in next view. | Integration |
| Dependency roles | Static check asserts host runtime requirements are in root `requirements.txt` and local-only tools are in `pyproject.toml`. | Unit/static |

## AstrBot Compatibility Smoke Matrix

| Case | Required assertion |
| --- | --- |
| v4.24.0 declared lower-bound load | Official `PluginManager` initializes and terminates the committed archive; handler names/priorities, `TextPart`, `extra_user_content_parts`, `mark_as_temp`, projection/restoration, Journal ordering, and durable intent pass. The host's own `StarMetadata.pages` fallback warning is recorded separately and is not an AstrContinuum failure. |
| v4.24.2 intermediate sample | The same lifecycle, hook, persistence, and projection checks pass. |
| v4.26.7 newest verified sample | The same checks pass. This is evidence through the sampled version, not a maximum compatibility cap or proof for every intervening/future build. |
| Missing `TextPart` import | Adapter records a redacted compatibility error and host request proceeds without enhanced context. No fabricated Journal success or coverage change occurs. |
| Missing `extra_user_content_parts` or `mark_as_temp` | Same fail-open behavior; core persistence and scheduling remain operational. |
| `on_llm_response` observation | Metrics may be emitted, but Journal row count and coverage are unchanged. |

## Completion Gate

Phase 0 verification MUST retain the input index trace, pre-write checkpoint, output index trace, and a verification artifact bound to the output snapshot. A passing Markdown/static check alone MUST NOT override a failed invariant, transition, SQLite concurrency, crash-recovery, or required AstrBot smoke test.
