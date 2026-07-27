# SC-03 Live Request Chain

Owner: live-context-implementer
Depends on: SC-01R, SC-02

## Goal

Connect the frozen sparse numerical core to the existing single-`RequestView` AstrBot request
path. Active output may replace the deterministic fallback only after numerical, provenance,
required-block, dependency-closure, and final budget-pack validation.

## Context

Read:

- `contracts/context-engine.md`
- `astrcontinuum/context_graph/`
- `astrcontinuum/runtime/types.py`
- `astrcontinuum/runtime/budget.py`
- `astrcontinuum/adapters/astrbot.py`

Do not read unrelated storage implementation or release documentation.

## Write Scope

- `astrcontinuum/context_graph/build.py`
- `astrcontinuum/context_graph/closure.py`
- `astrcontinuum/context_graph/live.py`
- `astrcontinuum/context_graph/__init__.py`
- `astrcontinuum/adapters/astrbot.py`
- `astrcontinuum/adapters/__init__.py`
- `tests/context_graph/test_build.py`
- `tests/context_graph/test_closure.py`
- `tests/context_graph/test_live.py`
- `tests/adapters/test_astrbot_adapter.py`
- `tests/integration/test_astrbot_projection_persistence.py`

## Steps

1. Write failing tests that build deterministic coordinates, normalized sparse relations,
   fix-one constraints for required blocks, and one query activation from the existing immutable
   candidates and current input.
2. Implement bounded construction using exact provenance, Capsule membership, raw-event
   adjacency, candidate kind, runtime slot, and normalized lexical overlap. Do not call a model,
   persist a graph, or duplicate candidate text.
3. Write failing dependency-closure and final-pack tests, including required companions and
   complete tool/source pairs where structurally available.
4. Implement recursive bounded closure separately from numerical constraints.
5. Write failing Active, Shadow, Off, solver-fault, invalid-graph, adaptive-recovery, and
   same-view tests.
6. Implement live selection: compute the deterministic fallback first, run bounded graph
   selection, retry only by retaining additional source coordinates, score optional blocks by
   verified activation per token inside the existing slot order, then run canonical `assemble`.
7. Validate the final packed block ids against required/provenance/dependency closure and exact
   budget. Any failure returns the already-computed fallback with `DEGRADED_RAW`.
8. Connect only inside `AstrBotHookBridge.assemble_prepared()`. Do not reread Journal state,
   change authoritative event capture, or mutate AstrBot history.
9. Expose bounded content-free last-result data on the bridge for the later observability task;
   keep the existing `AssemblyResult` return contract.

## Acceptance

Use task-local D-drive temporary directories and run:

```powershell
pytest tests/context_graph tests/adapters/test_astrbot_adapter.py `
  tests/integration/test_astrbot_projection_persistence.py -q
ruff check astrcontinuum/context_graph astrcontinuum/adapters tests/context_graph `
  tests/adapters/test_astrbot_adapter.py tests/integration/test_astrbot_projection_persistence.py
mypy astrcontinuum/context_graph astrcontinuum/adapters
```

Also prove that Active failure is byte-for-byte equivalent to deterministic fallback, Shadow
uses the same immutable view, the current user event is not projected as history, and native
AstrBot objects are restored by identity.

## Return

Return changed files, RED/GREEN evidence, mode behavior, stable error codes, exact commands and
results, conclusion, and blockers. Do not commit.
