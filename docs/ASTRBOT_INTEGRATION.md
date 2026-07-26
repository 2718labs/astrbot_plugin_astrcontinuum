# AstrBot integration contract

English | [简体中文](./ASTRBOT_INTEGRATION.zh-CN.md)

This document defines the boundary between AstrContinuum and AstrBot at repository version
`v0.1.0`. It is a runtime contract, not a list of intended APIs. Any change to hook ownership,
priority, message projection, request identity, or plugin lifecycle must update this document
and include a real `PluginManager` compatibility probe.

## 1. Composition root

`main.py` contains one registered `Star` subclass. It owns:

- plugin data-directory resolution through `StarTools`;
- idempotent SQLite migration and repository construction;
- the AstrBot hook bridge;
- request-scoped state stored on the event object;
- bounded request assembly and temporary projection;
- final restoration verification and compaction-intent persistence;
- resource closure during plugin termination.

The domain, runtime, compaction, and storage packages do not import AstrBot. Private host objects
are isolated behind `astrcontinuum.adapters.astrbot`.

## 2. Verified host range

Public metadata declares:

```yaml
astrbot_version: ">=4.24.0,<5.0.0"
```

The committed plugin archive has been loaded through official AstrBot distributions:

| AstrBot | Python | Verification |
| --- | --- | --- |
| `4.24.0` | `3.12.13` | PluginManager lifecycle, registry priorities, projection, restoration, Journal ordering, termination |
| `4.24.2` | `3.12.13` | Same full probe |
| `4.26.7` | `3.12.13` | Same full probe |

AstrBot `4.24.0` emits an upstream fallback warning for missing `StarMetadata.pages`; this does
not change AstrContinuum loading or runtime behavior. Compatibility through a sample version is
evidence, not a guarantee for every later `4.x` build.

## 3. Authoritative hooks

Only four hook/event triples may append durable Journal rows:

| Hook | Event type | Role |
| --- | --- | --- |
| `on_llm_request` | `USER_MESSAGE` | `USER` |
| `on_agent_done` finalizer | `ASSISTANT_MESSAGE` | `ASSISTANT` |
| `on_using_llm_tool` | `TOOL_CALL` | `TOOL` |
| `on_llm_tool_respond` | `TOOL_RESULT` | `TOOL` |

`on_llm_response` is observation-only. It must never append assistant content because
`on_agent_done` is the single assistant writer.

Deterministic idempotency keys plus SQLite uniqueness constraints make duplicate or concurrent
callback delivery converge on one event row without consuming another sequence number.

## 4. Hook order and ownership

| Handler | Priority | Responsibility | Forbidden work |
| --- | ---: | --- | --- |
| `on_llm_request` | `2000` | Capture user event, freeze high-water `H`, read committed Snapshot plus Delta, assemble bounded context | Remote audit/compiler calls, waiting for workers, full-database scans, migrations |
| `on_agent_begin_guard` | `2000` | Record exact native request/list/message identities before any projection | Replacing or copying host history |
| `on_agent_begin_project` | `-100` | Append one AstrContinuum-owned temporary provider message after other high-priority setup | Editing native message objects or persistent history |
| `on_agent_done_restore` | `2000` | Remove owned projection and preserve provider-appended Delta | Reconstructing host history by value |
| `on_agent_done_finalize` | `900` | Verify restoration, capture assistant event, persist latest compaction intent | Publishing a candidate Snapshot inline |
| `on_using_llm_tool` | `0` | Capture bounded tool-call metadata | Persisting arbitrary object representations |
| `on_llm_tool_respond` | `0` | Capture bounded tool-result metadata | Persisting unbounded results |
| `on_llm_response` | `0` | Optional content-free observation | Any Journal write |

The two `on_agent_begin` and two `on_agent_done` handlers deliberately use different priorities.
Changing them is an architectural change because it alters which object graph is guarded and
when restoration occurs relative to AstrBot and other plugins.

## 5. Request-scoped state

AstrContinuum stores a private `_RequestState` under a namespaced event-extra key. It holds
references required only for the active request:

- the original request object;
- prepared immutable read view;
- projection guard identities;
- projected/restored views;
- captured assistant event;
- tool ordinals;
- redacted adapter faults.

This state is not a durable source of truth and must not escape into SQLite or logs. A missing
state causes bounded fail-open behavior.

## 6. Session identity extraction

The adapter derives a complete `SessionKey` from host data:

```text
platform_instance_id
message_type
session_id
group_id
user_id
conversation_id
persona_id
```

Nullable `group_id` and `persona_id` are represented canonically. The full key remains in every
wire envelope; its SHA-256 hash is only a physical lookup key. If required host identity cannot
be obtained, the request proceeds without AstrContinuum enhancement rather than writing an
ambiguous session.

## 7. Projection capability boundary

AstrContinuum currently relies on AstrBot provider-message internals:

- `Message`;
- `TextPart`;
- `extra_user_content_parts`;
- `mark_as_temp` / `_no_save`.

These are probed at runtime. Projection occurs only when the required capability is present.
The owned `TextPart` and enclosing `Message` are marked temporary, appended without replacing
the existing context list, and removed before finalization.

Restoration is identity-based:

1. capture the request object, context-list object, and each native message object;
2. append only the AstrContinuum-owned message;
3. allow the provider/agent to append its own Delta;
4. remove the exact owned object;
5. verify the original request, list, and native messages still have the same identities;
6. preserve legitimate provider-added objects.

Value equality is insufficient. Rebuilding equivalent dictionaries would still violate host
ownership and can corrupt downstream persistence.

## 8. Failure and logging behavior

Compatibility or invariant failure produces a bounded `AdapterFault` containing codes and
counts, never message content or object representations. The live request remains usable:

- identity extraction failure → no capture or projection for that request;
- missing projection capability → durable capture can continue, projection is skipped;
- assembly failure → native request continues;
- projection/restoration invariant failure → owned enhancement is removed where safely
  possible and the host continues;
- finalization failure → no fabricated assistant row or Snapshot coverage.

## 9. Lifecycle and current limitation

Initialization migrates the database and constructs durable services. Termination closes
connections/resources. The finalizer raises durable compaction intent after assistant capture.

At `v0.1.0`, the `Star` lifecycle does **not** start a loop that claims and executes
`compaction_jobs`. Compiler, validator, auditor, scheduler, lease/fencing, and atomic-publication
primitives are implemented and tested in the core, but queued intent may remain pending.
Describing automatic long-running compaction as active would be incorrect.

## 10. Change checklist

For every integration change:

1. inspect the target AstrBot source/signatures rather than relying on remembered APIs;
2. update adapter capability probes before using a private host field;
3. run unit and SQLite integration tests;
4. load the committed archive through a real AstrBot `PluginManager`;
5. assert handler count, hook names, priorities, initialization, request behavior, and
   termination;
6. exercise at least the declared lower bound and newest verified sample for compatibility
   claims;
7. update both language versions of this document and the test matrix.

Related documents: [Architecture](./ARCHITECTURE.md),
[Data flow](./DATA_FLOW.md), [Test matrix](./TEST_MATRIX.md), and
[ADR-006](./ADR-006-ASTRBOT-HOOK-OWNERSHIP.md).
