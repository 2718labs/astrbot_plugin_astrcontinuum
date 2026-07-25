# Phase 0 Database Schema

## Conventions

The persistence target is SQLite. Opaque ids are non-empty text, timestamps are UTC RFC 3339 text, booleans are integer `0` or `1`, and JSON columns contain canonical JSON validated before insert. Journal sequences and Snapshot coverage indexes start at `1`. Bootstrap `EMPTY_BASE` has logical coverage `0` but is not a Snapshot Schema envelope, database row, or active pointer.

Foreign keys MUST be enabled. Write transactions and constraints are the correctness boundary; process-local locks are optional optimizations only. Enum values are uppercase stable wire values. Wire envelopes are closed objects. SQLite normalization MUST round-trip every required wire field exactly and MUST NOT expose physical lookup or JSON-storage columns as extra wire properties.

Standard JSON Schema does not compare arbitrary fields across records. Permanent mechanical validators MUST enforce `covered_event_end = source_high_water_mark = compaction_jobs.target_high_water_mark` before publish; same-row arithmetic relations SHOULD also use SQLite `CHECK` constraints.

## `sessions`

| Column | Rule |
| --- | --- |
| `session_key_hash` | TEXT PRIMARY KEY; SHA-256 of canonical SessionKey serialization |
| `canonical_session_key_json` | TEXT NOT NULL UNIQUE; exact ordered identity object |
| `platform_instance_id` | TEXT NOT NULL, non-empty |
| `message_type` | TEXT NOT NULL, non-empty |
| `session_id` | TEXT NOT NULL, non-empty |
| `group_id` | TEXT NULL |
| `user_id` | TEXT NOT NULL, non-empty |
| `conversation_id` | TEXT NOT NULL, non-empty |
| `persona_id` | TEXT NULL; identity metadata only |
| `next_event_sequence` | INTEGER NOT NULL CHECK `>= 1` |
| `created_at`, `updated_at` | TEXT NOT NULL, UTC RFC 3339 |

The canonical tuple order MUST be `platform_instance_id`, `message_type`, `session_id`, `group_id`, `user_id`, `conversation_id`, `persona_id`. The canonical JSON and denormalized fields MUST agree. Persona memory content MUST NOT be stored. Event, Snapshot, and Job projection MUST reconstruct the required wire `session_key` from `canonical_session_key_json`; `session_key_hash` is an additional physical lookup key and MUST NOT replace or appear beside `session_key` in a closed wire envelope.

## `journal_events`

| Column | Rule |
| --- | --- |
| `event_id` | TEXT PRIMARY KEY |
| `session_key_hash` | TEXT NOT NULL REFERENCES `sessions` |
| `sequence` | INTEGER NOT NULL CHECK `>= 1` |
| `event_type` | TEXT NOT NULL: `USER_MESSAGE`, `ASSISTANT_MESSAGE`, `TOOL_CALL`, or `TOOL_RESULT` |
| `role` | TEXT NOT NULL: `USER`, `ASSISTANT`, or `TOOL` |
| `content` | TEXT NOT NULL |
| `source_hook` | TEXT NOT NULL: `ON_LLM_REQUEST`, `ON_AGENT_DONE`, `ON_USING_LLM_TOOL`, or `ON_LLM_TOOL_RESPOND` |
| `idempotency_key` | TEXT NOT NULL, non-empty |
| `token_count` | INTEGER NOT NULL CHECK `>= 0` |
| `created_at` | TEXT NOT NULL, UTC RFC 3339 |

The database or repository validation MUST enforce exactly these triples:

- `USER_MESSAGE / USER / ON_LLM_REQUEST`;
- `ASSISTANT_MESSAGE / ASSISTANT / ON_AGENT_DONE`;
- `TOOL_CALL / TOOL / ON_USING_LLM_TOOL`;
- `TOOL_RESULT / TOOL / ON_LLM_TOOL_RESPOND`.

`ON_LLM_RESPONSE` MUST NOT be accepted as a `source_hook`. Required uniqueness constraints are `UNIQUE(session_key_hash, sequence)` and `UNIQUE(session_key_hash, source_hook, idempotency_key)`. Rows are append-only. Capture transactions MUST allocate `sessions.next_event_sequence` and insert atomically; an idempotency conflict returns the existing row without advancing sequence. Repository projection maps physical `session_key_hash` back to the required embedded wire `session_key`.

## `snapshots`

Snapshot wire-to-SQLite mapping is normative:

| Wire field | SQLite representation |
| --- | --- |
| `session_key` | Join `session_key_hash` to `sessions.canonical_session_key_json` and reconstruct the embedded object |
| `capsule_ids` | `capsule_ids_json` canonical JSON array; at least one unique non-empty id |
| `exact_anchor_ids` | `exact_anchor_ids_json` canonical JSON array of unique non-empty ids; empty is allowed |
| `audit_outcome` | `audit_outcome` canonical JSON object |
| `state` | `lifecycle_state`; the table admits only `COMMITTED` |

| Column | Rule |
| --- | --- |
| `snapshot_id` | TEXT PRIMARY KEY |
| `session_key_hash` | TEXT NOT NULL REFERENCES `sessions` |
| `base_snapshot_id` | TEXT NULL REFERENCES `snapshots(snapshot_id)` |
| `covered_event_end` | INTEGER NOT NULL CHECK `>= 1` |
| `source_high_water_mark` | INTEGER NOT NULL CHECK `>= covered_event_end` |
| `capsule_ids_json` | TEXT NOT NULL, canonical JSON array |
| `exact_anchor_ids_json` | TEXT NOT NULL, canonical JSON array |
| `rendered_context` | TEXT NOT NULL, non-empty |
| `token_cost` | INTEGER NOT NULL CHECK `>= 0` |
| `audit_outcome` | TEXT NOT NULL; canonical object with `mechanical_passed`, `semantic_status`, and unique `failure_codes` |
| `lifecycle_state` | TEXT NOT NULL CHECK value is `COMMITTED` |
| `created_at`, `committed_at` | TEXT NOT NULL, UTC RFC 3339 |

The wire `state` enum is exactly `CANDIDATE` or `COMMITTED`. A `CANDIDATE` envelope MUST have `committed_at=null`; a `COMMITTED` envelope MUST have a non-null UTC RFC 3339 `committed_at`. Rows are immutable after insertion. Worker-local `CANDIDATE` envelopes are closed Schema objects but are not inserted in Phase 0; `candidate_snapshot_id` is preallocated on the job, and `TX_PUBLISH_SNAPSHOT` inserts the final row directly as `COMMITTED`. Every inserted row MUST have `audit_outcome.mechanical_passed=true`, `semantic_status` equal to `NOT_RUN` or `PASSED`, and empty `failure_codes`. For compaction output, the permanent validator MUST establish `source_high_water_mark = covered_event_end = compaction_jobs.target_high_water_mark`. `UNIQUE(session_key_hash, covered_event_end)` prevents two committed representations of one prefix; violation during publish MUST enter the same isolated `SUPERSEDED` path as pointer CAS conflict.

## `active_snapshots`

| Column | Rule |
| --- | --- |
| `session_key_hash` | TEXT PRIMARY KEY REFERENCES `sessions` |
| `snapshot_id` | TEXT NOT NULL UNIQUE REFERENCES `snapshots` |
| `pointer_version` | INTEGER NOT NULL CHECK `>= 1` |
| `updated_at` | TEXT NOT NULL, UTC RFC 3339 |

There is at most one active Snapshot per session. Before first publish there is no row; logical `EMPTY_BASE` is not inserted. `TX_PUBLISH_SNAPSHOT` MUST use one of two CAS forms: bootstrap (`base_snapshot_id=null` and `base_pointer_version=0`) conditionally inserts the absent row with `pointer_version=1`; an existing base (`base_snapshot_id` non-null and `base_pointer_version>=1`) conditionally updates the row whose `snapshot_id` and `pointer_version` match, then increments the version. Both forms MUST reject a new Snapshot whose `covered_event_end` is not strictly greater than current logical/active coverage. A Snapshot is reader-visible only through this table.

## `compaction_jobs`

| Column | Rule |
| --- | --- |
| `job_id` | TEXT PRIMARY KEY |
| `session_key_hash` | TEXT NOT NULL REFERENCES `sessions` |
| `state` | TEXT NOT NULL; stable Job state enum |
| `target_high_water_mark` | INTEGER NOT NULL CHECK `>= 1`; frozen per claimed attempt |
| `intent_target_high_water_mark` | INTEGER NOT NULL CHECK `>= target_high_water_mark` |
| `base_snapshot_id` | TEXT NULL REFERENCES `snapshots` |
| `base_pointer_version` | INTEGER NOT NULL CHECK `>= 0`; `0` for bootstrap |
| `candidate_snapshot_id` | TEXT NULL |
| `lease_owner` | TEXT NULL |
| `lease_epoch` | INTEGER NOT NULL CHECK `>= 0`; monotonic |
| `lease_expires_at` | TEXT NULL, UTC RFC 3339 |
| `attempt_count` | INTEGER NOT NULL CHECK `>= 0` |
| `next_retry_at` | TEXT NULL, UTC RFC 3339 |
| `error_stage`, `error_code`, `error_message` | TEXT NULL; message MUST be redacted |
| `created_at`, `updated_at`, `committed_at` | TEXT; first two NOT NULL, committed time nullable |

The Job wire envelope requires every declared field. A nullable field MUST be present with explicit `null` when its condition does not supply a value; omission is invalid. State conditions are:

- `base_snapshot_id=null` if and only if `base_pointer_version=0`; a non-null base requires `base_pointer_version>=1`.
- Working states `LEASED`, `COMPILING`, `AUDITING`, and `READY_TO_COMMIT` require non-null `lease_owner` and `lease_expires_at` plus `lease_epoch>=1`. All other states require explicit null owner and expiry.
- `candidate_snapshot_id` is non-null only for `READY_TO_COMMIT`, `COMMITTED`, and `SUPERSEDED`; every other state requires explicit null.
- `error_stage`, `error_code`, and `error_message` are an all-or-none tuple: all three MUST be explicit null, or all three MUST be non-empty strings with a redacted message. `RETRY_WAIT` and `FAILED` require the non-empty form. Other states MAY use the all-null form or retain the complete redacted tuple from a prior attempt; a partial tuple is always invalid.
- `next_retry_at` is non-null only for `RETRY_WAIT`; every other state requires explicit null.
- `committed_at` is non-null only for `COMMITTED`; every other state requires explicit null.

`state` MUST be one of `PENDING`, `LEASED`, `COMPILING`, `AUDITING`, `READY_TO_COMMIT`, `RETRY_WAIT`, `COMMITTED`, `SUPERSEDED`, `FAILED`, `CANCELLED`. The Job wire envelope uses these complete field names unchanged; `session_key_hash` is projected back to embedded `session_key`.

The database MUST enforce at most one nonterminal intent chain per session, for example with a partial unique index over nonterminal states. `TX_RAISE_COMPACTION_INTENT` MUST coalesce duplicates by taking the maximum intent target.

## Atomic Transactions

| Name | Required atomic effects |
| --- | --- |
| `TX_CAPTURE_USER_EVENT` | Upsert/validate session, idempotency check, allocate sequence, append mapped user event |
| `TX_CAPTURE_ASSISTANT_EVENT` | Upsert/validate session, idempotency check, allocate sequence, append mapped assistant event |
| `TX_CAPTURE_TOOL_EVENT` | Upsert/validate session, idempotency check, allocate sequence, append mapped tool event |
| `TX_READ_REQUEST_VIEW` | Read active pointer/Snapshot, Journal high-water, and uncovered ordered events from one read snapshot |
| `TX_RAISE_COMPACTION_INTENT` | Create/coalesce nonterminal work and monotonically raise intent target |
| `TX_CLAIM_JOB` | Select eligible job, increment fencing epoch, freeze target, set lease, enter `LEASED` |
| `TX_FAIL_JOB` | Fence by owner/epoch, persist redacted error, enter `RETRY_WAIT` or `FAILED`, clear lease |
| `TX_PUBLISH_SNAPSHOT` | Fence and mechanically validate strict coverage advance; open savepoint; insert committed Snapshot; bootstrap CAS-create or existing-pointer CAS-update; on same-prefix uniqueness or pointer conflict roll back only insert and persist `SUPERSEDED`; in either branch preserve higher intent as follow-up against winning base |
| `TX_RECOVER_EXPIRED_LEASES` | Requeue expired working jobs and clear owner/expiry; for expired `READY_TO_COMMIT` also clear candidate id so the next claim recompiles |

If Snapshot insert violates `UNIQUE(session_key_hash, covered_event_end)`, bootstrap CAS-create conflicts, or existing-pointer CAS-update affects zero rows, `TX_PUBLISH_SNAPSHOT` MUST use the same isolated publish-conflict path. It MUST roll back to an inner savepoint (or use equivalent statement-level rollback) so only the unpublished Snapshot insert is removed. The outer transaction MUST retain `candidate_snapshot_id`, record the fenced job as `SUPERSEDED`, and clear its lease. Before commit it MUST read the winning active Snapshot and pointer version; when durable `intent_target_high_water_mark` exceeds winning coverage, it MUST leave or create `PENDING` follow-up work using that winning base. A stale fencing predicate MUST reject the entire attempted transition.
