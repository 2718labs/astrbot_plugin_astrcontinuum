# v0.3.0 Database Schema

## Conventions

The persistence target is SQLite. Opaque ids are non-empty text, timestamps are UTC RFC 3339 text, booleans are integer `0` or `1`, and JSON columns contain canonical JSON validated before insert. Journal sequences and Snapshot coverage indexes start at `1`. Bootstrap `EMPTY_BASE` has logical coverage `0` but is not a Snapshot Schema envelope, database row, or active pointer.

Foreign keys MUST be enabled. Write transactions and constraints are the correctness boundary; process-local locks are optional optimizations only. Wire-envelope enum values are uppercase stable values. The v0.3.0 storage-only reorganization ledger uses its separately documented lowercase values `retained`, `approximate`, and `released`. Wire envelopes are closed objects. SQLite normalization MUST round-trip every required wire field exactly and MUST NOT expose physical lookup or JSON-storage columns as extra wire properties.

The active secure format stores conversation-derived text, structured envelopes, compatibility
counts, and canonical token metrics in authenticated `acenc:v1:` AES-256-GCM envelopes. The
logical wire values and compatibility byte counts do not change when encrypted. Physical
`*_envelope` columns and encrypted JSON wrappers are storage details and MUST NOT appear as
extra wire properties.

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
| `token_count_envelope` | TEXT NOT NULL `acenc:v1:` envelope for the non-negative logical compatibility byte count |
| `created_at` | TEXT NOT NULL, UTC RFC 3339 |

The database or repository validation MUST enforce exactly these triples:

- `USER_MESSAGE / USER / ON_LLM_REQUEST`;
- `ASSISTANT_MESSAGE / ASSISTANT / ON_AGENT_DONE`;
- `TOOL_CALL / TOOL / ON_USING_LLM_TOOL`;
- `TOOL_RESULT / TOOL / ON_LLM_TOOL_RESPOND`.

`ON_LLM_RESPONSE` MUST NOT be accepted as a `source_hook`. Required uniqueness constraints are `UNIQUE(session_key_hash, sequence)` and `UNIQUE(session_key_hash, source_hook, idempotency_key)`. Rows are append-only. Capture transactions MUST allocate `sessions.next_event_sequence` and insert atomically; an idempotency conflict returns the existing row without advancing sequence. Repository projection maps physical `session_key_hash` back to the required embedded wire `session_key`.

## `capsules`

The logical table contract is:

```sql
CREATE TABLE capsules (
    capsule_id TEXT PRIMARY KEY,
    session_key_hash TEXT NOT NULL,
    level TEXT NOT NULL,
    covered_event_start INTEGER NOT NULL CHECK (covered_event_start >= 1),
    covered_event_end INTEGER NOT NULL CHECK (covered_event_end >= covered_event_start),
    canonical_capsule_json TEXT NOT NULL,
    token_cost_envelope TEXT NOT NULL,
    source_coverage REAL NOT NULL CHECK (source_coverage BETWEEN 0 AND 1),
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_key_hash) REFERENCES sessions(session_key_hash)
);
```

`canonical_capsule_json` MUST validate against the closed v1 Capsule Schema before
insertion and MUST round-trip the complete embedded SessionKey and every structured
semantic record. `source_coverage` is a physical index projection of
`quality.source_coverage`; it is not an extra wire field.

`canonical_capsule_json` is an encrypted JSON wrapper in the secure format, and
`token_cost_envelope` holds the authenticated non-negative logical compatibility count.
Capsule rows are immutable. A permanent validator MUST reject a Capsule when any
top-level or nested source event id is missing, belongs to another session, or lies
outside the Capsule coverage. Sylanne memory content is not an admissible source event.

## `snapshot_capsules`

The ordered membership contract is:

```sql
CREATE TABLE snapshot_capsules (
    snapshot_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    capsule_id TEXT NOT NULL,
    slot TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, ordinal),
    UNIQUE (snapshot_id, capsule_id),
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id),
    FOREIGN KEY (capsule_id) REFERENCES capsules(capsule_id)
);
```

The normative relational constraints are:

```text
PRIMARY KEY (`snapshot_id`, `ordinal`)
UNIQUE (`snapshot_id`, `capsule_id`)
```

Membership rows are immutable and authoritative for the Snapshot wire `capsule_ids`
order. Before insertion, a permanent validator MUST prove that the Capsule and Snapshot
have the same `session_key_hash`, every Capsule source event exists in that session, and
the Snapshot exact-anchor membership is consistent with the ordered Capsule content.
Cross-session membership and dangling Capsule, source-event, or anchor references MUST
be rejected.

## `snapshot_reorganization_records` (migration v3)

This v0.3.0 table is the immutable audit ledger for a committed Snapshot's source-item
reorganization. It is not a replacement for Capsule provenance or Snapshot quality.

| Column | Rule |
| --- | --- |
| `snapshot_id` | TEXT NOT NULL REFERENCES `snapshots(snapshot_id)` |
| `ordinal` | INTEGER NOT NULL CHECK `>= 0`; preserves caller order |
| `source_capsule_id`, `kind`, `item_id` | TEXT NOT NULL and non-empty; together identify one source item within a Snapshot |
| `status` | TEXT NOT NULL: `retained`, `approximate`, or `released` |
| `before_tokens`, `after_tokens` | INTEGER NOT NULL CHECK `>= 0` |
| `required` | INTEGER NOT NULL CHECK `IN (0, 1)` |

The primary key is `PRIMARY KEY(snapshot_id, ordinal)` and source identity is unique by
`UNIQUE(snapshot_id, source_capsule_id, kind, item_id)`. Update and delete triggers make
the rows immutable. Before the savepoint opens, repository canonicalization requires exact
record/status/boolean types, non-empty source identity, non-negative non-boolean token
counts, unique source identity, and `required=true` only with `status=retained`.

`released` is an audit disposition, not permission to bypass the quality floor. A
non-`narrative_summary` `released` record adds `QUALITY_COVERAGE_GAP` during permanent
validation and prevents publication even when `strict_audit=false`. Ledger rows are
written only after their `COMMITTED` Snapshot row exists, are ordered by `ordinal`, and
are readable only through a committed Snapshot. Records are optional at the repository
publication API: the standard `CompactionWorker` currently supplies none, so a committed
Snapshot with an empty ledger is valid in v0.3.0.

## `snapshots`

Snapshot wire-to-SQLite mapping is normative:

| Wire field | SQLite representation |
| --- | --- |
| `session_key` | Join `session_key_hash` to `sessions.canonical_session_key_json` and reconstruct the embedded object |
| `capsule_ids` | Join `snapshot_capsules` ordered by `ordinal`; at least one unique non-empty id |
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
| `exact_anchor_ids_json` | TEXT NOT NULL, canonical JSON array |
| `rendered_context` | TEXT NOT NULL `acenc:v1:` envelope |
| `token_cost_envelope` | TEXT NOT NULL `acenc:v1:` envelope for the non-negative logical compatibility count |
| `audit_outcome` | TEXT NOT NULL encrypted JSON wrapper for the logical canonical audit object |
| `lifecycle_state` | TEXT NOT NULL CHECK value is `COMMITTED` |
| `created_at`, `committed_at` | TEXT NOT NULL, UTC RFC 3339 |

The wire `state` enum is exactly `CANDIDATE` or `COMMITTED`. A `CANDIDATE` envelope MUST have `committed_at=null`; a `COMMITTED` envelope MUST have a non-null UTC RFC 3339 `committed_at`. Rows are immutable after insertion. Worker-local `CANDIDATE` envelopes are closed Schema objects but are not inserted in Phase 0; `candidate_snapshot_id` is preallocated on the job, and `TX_PUBLISH_SNAPSHOT` inserts the final row directly as `COMMITTED`. Ordered `snapshot_capsules` rows are the sole durable authority for wire `capsule_ids`; no denormalized id array may compete with them. Every inserted row MUST have `audit_outcome.mechanical_passed=true`, `semantic_status` equal to `NOT_RUN` or `PASSED`, and empty `failure_codes`. For compaction output, the permanent validator MUST establish `source_high_water_mark = covered_event_end = compaction_jobs.target_high_water_mark`. `UNIQUE(session_key_hash, covered_event_end)` prevents two committed representations of one prefix; violation during publish MUST enter the same isolated `SUPERSEDED` path as pointer CAS conflict.

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

## `token_metrics`

Canonical counts are derived sidecars, never replacements for compatibility wire fields:

| Column | Rule |
| --- | --- |
| `artifact_kind` | TEXT NOT NULL: `EVENT`, `CAPSULE`, or `SNAPSHOT` |
| `artifact_id` | TEXT NOT NULL, non-empty |
| `tokenizer_profile_id` | TEXT NOT NULL, non-empty immutable profile identity |
| `metric_envelope` | TEXT NOT NULL `acenc:v1:` envelope containing only schema version and non-negative token count |
| `created_at` | TEXT NOT NULL, UTC RFC 3339 |

The primary key is
`(artifact_kind, artifact_id, tokenizer_profile_id)`. A repeated write with the same logical
count is idempotent; a different count for that identity is `TOKEN_METRIC_CONFLICT`. The
authenticated record key binds all three key fields, so swapping encrypted metric envelopes
between artifacts or profiles fails authentication.

## `token_metric_backfill_intents`

| Column | Rule |
| --- | --- |
| `session_key_hash` | TEXT NOT NULL REFERENCES `sessions` |
| `artifact_kind` | TEXT NOT NULL: `EVENT`, `CAPSULE`, or `SNAPSHOT` |
| `artifact_id` | TEXT NOT NULL, non-empty |
| `tokenizer_profile_id` | TEXT NOT NULL, non-empty |
| `created_at` | TEXT NOT NULL, UTC RFC 3339 |

The primary key matches `token_metrics`. Rows identify content-free missing work; they contain no
message text or count. Backfill reads authenticated artifact text in stable bounded order,
rechecks session ownership, writes the metric, and deletes its intent in one transaction.
Crashes leave either the old intent or the completed metric, never an untracked partial state.

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
| `TX_PUBLISH_SNAPSHOT` | Fence and mechanically validate strict coverage advance plus canonical ledger records; open one savepoint; insert new candidate Capsules, committed Snapshot, ordered membership, and ordered reorganization records; bootstrap CAS-create or existing-pointer CAS-update; the existing classifier maps Capsule/Snapshot/membership insert integrity collisions and pointer conflict to `SUPERSEDED`, rolls back every new candidate row, and preserves higher intent as follow-up against winning base; ledger insert integrity errors propagate instead |
| `TX_RECOVER_EXPIRED_LEASES` | Requeue expired working jobs and clear owner/expiry; for expired `READY_TO_COMMIT` also clear candidate id so the next claim recompiles |
| `TX_BACKFILL_TOKEN_METRICS` | Recheck artifact/session ownership, CAS-write a bounded canonical metric batch, and delete matching intents atomically |

Each capture transaction also commits either the canonical Event metric or a matching backfill
intent with the new event. `TX_PUBLISH_SNAPSHOT` requires and atomically commits exactly one
canonical metric for every new Capsule and the new Snapshot. Metrics never advance the active
pointer independently of the artifact they describe.

`TX_PUBLISH_SNAPSHOT` MUST insert all newly compiled `capsules`, the committed Snapshot,
its ordered `snapshot_capsules` rows, and ordered `snapshot_reorganization_records` rows
inside the same savepoint before active-pointer CAS. Existing immutable base Capsules may
be referenced but are never rewritten. The required physical order is Capsules → Snapshot
→ membership → ledger → CAS because membership and ledger rows both have immediate foreign
keys to the Snapshot.

If a pre-savepoint validation fails, publication rejects before writing candidate content.
The existing candidate-conflict classifier treats a Capsule/Snapshot/membership insert
integrity collision, `UNIQUE(session_key_hash, covered_event_end)`, bootstrap CAS-create
conflict, or existing-pointer CAS-update affecting zero rows as the isolated conflict path.
It MUST roll back to the inner savepoint so every new candidate Capsule, membership,
Snapshot, and ledger row is removed. The outer transaction MUST retain
`candidate_snapshot_id`, record the fenced job as `SUPERSEDED`, and clear its lease.
Before commit it MUST read the winning active Snapshot and pointer version; when durable
`intent_target_high_water_mark` exceeds winning coverage, it MUST leave or create
`PENDING` follow-up work using that winning base. A stale fencing predicate MUST reject
the entire attempted transition and MUST NOT record `SUPERSEDED`. An integrity error while
writing the ledger itself is not an expected publish conflict: it MUST propagate and roll
back the entire outer transaction.
