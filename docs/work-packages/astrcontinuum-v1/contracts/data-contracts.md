# Data Contracts

## SessionKey

Closed fields in canonical order:

```text
platform_instance_id
message_type
session_id
group_id
user_id
conversation_id
persona_id
```

`group_id` and `persona_id` are explicit string-or-null fields. Other values are non-empty
strings. The physical lookup key is `SHA256(UTF8(canonical_json(SessionKey)))`.

## Event

Closed fields: `event_id`, embedded `session_key`, `sequence`, `event_type`, `role`,
`content`, `source_hook`, `idempotency_key`, `token_count`, `created_at`.

Valid triples are only:

```text
USER_MESSAGE / USER / ON_LLM_REQUEST
ASSISTANT_MESSAGE / ASSISTANT / ON_AGENT_DONE
TOOL_CALL / TOOL / ON_USING_LLM_TOOL
TOOL_RESULT / TOOL / ON_LLM_TOOL_RESPOND
```

Idempotency uniqueness is `(session_key_hash, source_hook, idempotency_key)`. Reusing a
key with different content or mapping is a conflict.

## ContextCapsule

A v1 Capsule is a closed immutable envelope with full SessionKey, resolution level,
inclusive event coverage, source event ids, schema version/time, typed goals,
constraints, decisions, progress, open loops, preferences, entities, emotional context,
exact anchors, dependencies, navigation summary, token cost, and quality metrics.

Every active claim, decision, entity, dependency, and anchor links to source event ids.
`narrative_summary` is never the only persisted semantic representation.

## Snapshot

A Snapshot is a closed immutable envelope with full SessionKey, base Snapshot identity,
coverage/source high-water, ordered Capsule ids, exact-anchor ids, rendered projection,
token cost, audit outcome, lifecycle state, and timestamps.

`CANDIDATE` is worker-local. The database stores only `COMMITTED`. Publication requires
mechanical pass, semantic `NOT_RUN` or `PASSED`, and no failure codes.

## Job

Job states are exactly:

```text
PENDING LEASED COMPILING AUDITING READY_TO_COMMIT
RETRY_WAIT COMMITTED SUPERSEDED FAILED CANCELLED
```

All nullable wire fields are present with explicit null. Base id is null exactly when
base pointer version is zero. Working states require owner, expiry, and epoch at least
one. Candidate id exists only for ready/committed/superseded. Error stage/code/message is
an all-null or all-non-empty tuple.

## Persistence Relations

`capsules` stores the full canonical closed envelope plus indexed identity/coverage fields.
`snapshot_capsules` stores ordered membership with same-session validation. Source events,
base Snapshot, active pointer, and Capsule membership cannot cross SessionKey identity.
