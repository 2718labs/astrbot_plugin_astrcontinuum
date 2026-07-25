# AstrBot and Sylanne Composition

## Hook Ownership

`on_llm_request` is the sole user Journal writer. `on_agent_done` after native restoration
is the sole assistant Journal writer. Tool call and tool result Hooks own their respective
event mappings. `on_llm_response` is observational.

Hook handlers are coroutines and do not yield. User commands yield
`event.plain_result(...)`.

## Reversible Ordering

Verified composition order:

```text
AC request capture/read/prepare: priority 2000
AC begin identity guard: priority 2000
Sylanne begin projection: priority 0
AC begin projection: priority -100
AC done restore: priority 2000
Sylanne done restore: priority 1000
AC done verification and assistant capture: priority 900
```

State sequence:

```text
N -> S(N) -> A(S(N)) -> A(S(N)) + Delta
  -> S(N) + Delta -> N + Delta -> host save
```

AstrContinuum never modifies `ProviderRequest.contexts`. It preserves system and current
turn objects by identity and replaces only the verified history prefix inside
`run_context.messages`.

## Identity Guard

The transaction records request identity, list identity, original native objects,
preserved system/current objects, and projected prefix objects. Restore runs only when
all recorded identities match. A mismatch disables projection for the request rather
than guessing.

AstrContinuum uses a private namespaced event-extra key and does not inspect Sylanne's
projection transaction.

## Opaque Sylanne Budgeting

Sylanne executes its existing memory pipeline exactly once. Afterward, AstrContinuum may
ephemerally count the cost of preserved non-owned objects and fit AC-owned blocks into:

```text
B_AC = max(0, B_input - B_opaque - B_required)
```

It does not retain text, embeddings, source ids, semantic labels, or stable content hashes
for those objects.

## Version Gate

Projection requires verified agent Hook order, save order, message-list behavior, and any
temporary `TextPart` capability. The release matrix is AstrBot 4.24.2, 4.24.5, and 4.26.7.
Unknown or third-party runners use native standalone fallback until separately verified.
