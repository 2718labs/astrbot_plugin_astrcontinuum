# ADR-007: v1 Capsule Persistence

Status: accepted

> Update: v0.3.0 的发布写入顺序、账本可见性和冲突分类由 ADR-008 局部修订；本 ADR 的 Capsule 持久化决策保持有效。

## Context

The starter Schema represented most semantics as generic claims and identified a Capsule
with only `session_id`. That shape conflicts with the Master Prompt, the closed
seven-component SessionKey contract, and the requirement that a Snapshot be a
source-verifiable structured state rather than a narrative summary.

Publication also needs one rollback boundary. A losing publisher must not leave orphaned
Capsules or membership rows that readers could later mistake for committed state.

## Decision

Master Prompt semantic fields are materialized as separate `goals`, `constraints`,
`decisions`, `progress`, `open_loops`, `preferences`, `entities`,
`emotional_context`, `exact_anchors`, and `dependencies` arrays. Every active claim,
decision, entity, anchor, and dependency carries source event ids.

Every Capsule embeds the full SessionKey in this canonical order:
`platform_instance_id`, `message_type`, `session_id`, `group_id`, `user_id`,
`conversation_id`, and `persona_id`.

Capsules are immutable closed structured records. `narrative_summary` is navigation only
and never replaces the structured fields or their provenance.

Snapshots refer to Capsules through ordered `snapshot_capsules` membership. The
membership order is authoritative; a denormalized id list cannot become a second source
of truth.

New candidate `capsules`, their `snapshot_capsules` membership, and the committed
Snapshot are inserted in the same savepoint before active-pointer CAS. If same-prefix
uniqueness or pointer CAS loses, publication must roll back every new candidate Capsule,
membership, and Snapshot row before the fenced losing job is committed as
`SUPERSEDED`. A stale fencing token rejects the whole operation and does not record
`SUPERSEDED`.

Capsule and Snapshot membership must use the same SessionKey identity. All referenced
source events must exist in that session, and exact-anchor membership must agree with the
Capsule content before insertion.

Sylanne memory is not a Capsule source and is never persisted by AstrContinuum.

## Consequences

The JSON Schema is the authoritative wire envelope. SQLite stores canonical Capsule JSON
plus indexed identity and coverage columns, while ordered membership is normalized into
`snapshot_capsules`. Readers still resolve only the active committed Snapshot; candidate
content is invisible and fully removable at the publication savepoint.
