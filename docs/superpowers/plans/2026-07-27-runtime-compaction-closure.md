# Runtime Compaction Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the live-lane/background-lane loop so AstrContinuum switches to a bounded temporary Provider View under context pressure, schedules compaction without blocking the request, and publishes verified Checkpoints from a durable worker.

**Architecture:** The live lane remains authoritative-history preserving: it measures pressure, optionally replaces only native history with a bounded projection, and restores the original AstrBot object graph before persistence. The background lane claims durable jobs under the existing owner/epoch lease fence, compiles complete base-plus-Delta input, validates it, and publishes with the existing SQLite CAS transaction. A configured provider may override compilation; otherwise the most recently observed conversation provider is used.

**Tech Stack:** Python 3.10+, asyncio, AstrBot hooks, Pydantic, SQLite WAL/CAS, pytest, uv, ruff, mypy.

---

## File Map

| Path | Responsibility |
| --- | --- |
| `astrcontinuum/runtime/pressure.py` | Pure context-pressure decision from trusted provider usage or conservative fallback |
| `astrcontinuum/runtime/projection.py` | Reversible projection that survives AstrBot replacing the message-list object |
| `astrcontinuum/runtime/retrieval.py` | Role-labelled exact raw-event blocks |
| `astrcontinuum/adapters/astrbot.py` | Capture trusted usage and prepare request-local immutable input |
| `astrcontinuum/storage/repository.py` | Read one fenced compaction input against the job's frozen base and target |
| `astrcontinuum/compaction/worker.py` | Claim, compile, audit, publish, retry, wake, and lifecycle loop |
| `astrcontinuum/compaction/astrbot_backend.py` | Small-model-friendly structured extractive compiler with exact-source validation |
| `main.py` | Compose pressure policy, provider registry/backend, worker, hooks, and lifecycle |
| `_conf_schema.json` | User-readable auto-compaction and compiler-provider controls |
| `tests/runtime/test_pressure.py` | Pressure thresholds and trusted/fallback accounting |
| `tests/runtime/test_projection.py` | Empty projection and list-replacement restoration |
| `tests/test_compaction_worker.py` | Durable worker state transitions and failure isolation |
| `tests/test_astrbot_compiler_backend.py` | Exact-span parser, deterministic ids, and invalid-model-output rejection |
| `tests/test_main_hooks.py` | Conditional projection, token-usage masking, worker wake, and lifecycle |

## Task 1: Make the Live Lane Pressure-Aware and Always Bounded

- [x] **Step 1: Write failing pressure tests**

Add tests asserting:

```python
decision = assess_pressure(
    trusted_token_usage=81,
    estimated_input_usage=10,
    current_input_cost=1,
    config=PressureConfig(
        context_limit=100,
        reserved_output_and_tools=0,
        compact_ratio=0.75,
        project_ratio=0.80,
    ),
)
assert decision.should_compact is True
assert decision.should_project is True
assert decision.source is PressureSource.TRUSTED_PROVIDER_USAGE
```

Also assert that unknown trusted usage selects the conservative estimate, thresholds are ordered, and invalid ratios are rejected.

- [x] **Step 2: Run the focused tests and observe RED**

Run:

```powershell
uv run --frozen --extra dev pytest tests/runtime/test_pressure.py -q
```

Expected: import failure for the missing pressure module.

- [x] **Step 3: Implement the pure pressure policy**

Create frozen `PressureConfig`, `PressureDecision`, and `PressureSource` types. Compute usable capacity as `context_limit - reserved_output_and_tools`; prefer positive trusted usage and add the current request cost, otherwise use the conservative complete-message estimate.

- [x] **Step 4: Write failing projection compatibility tests**

Add tests proving:

```python
projected = project(messages, guard, ())
replacement_list = list(messages)
replacement_list.append(provider_delta)
restored = restore(replacement_list, projected)
assert verify_native(replacement_list, restored)
assert replacement_list == [*native_objects, provider_delta]
```

The test must fail because the current projection rejects an empty projection and requires the original list identity.

- [x] **Step 5: Implement bounded empty projection and identity-safe restoration**

Permit an empty temporary projection so native history can still be removed when no AC block fits. Restore by locating the exact current-user object identity and treating later objects as provider Delta; do not require AstrBot to preserve the list object's identity.

- [x] **Step 6: Run focused tests GREEN**

```powershell
uv run --frozen --extra dev pytest tests/runtime/test_pressure.py tests/runtime/test_projection.py tests/test_budget.py -q
```

## Task 2: Trigger Only Under Context Pressure

- [x] **Step 1: Write failing hook tests**

Add hook tests for three modes:

```text
below compact threshold -> no intent, no projection
above compact threshold but below project threshold -> intent only
above project threshold -> bounded projection plus intent
```

Also assert the temporary request-local Conversation copy has `token_usage=0` while the original Conversation remains unchanged, preventing AstrBot from re-compressing the projected view.

- [x] **Step 2: Observe the hook RED**

```powershell
uv run --frozen --extra dev pytest tests/test_main_hooks.py tests/integration/test_astrbot_projection_persistence.py -q
```

Expected: existing hooks always project and always raise intent.

- [x] **Step 3: Implement request-local pressure state**

Capture trusted provider usage in `PreparedRequest`. In `on_agent_begin`, estimate the complete native request, assess pressure, and:

```text
NORMAL: leave native messages untouched
COMPACT_PENDING: leave native messages untouched
PROJECTED: mask request-local token usage, remove native history, add the bounded Provider View
```

If assembly or projection-object construction fails after projection is required, apply the empty projection rather than falling back to the full native history.

- [x] **Step 4: Gate durable intent and wake the worker**

After the authoritative assistant event is captured, raise durable intent only when `should_compact` is true. Notify the in-process worker after the transaction returns; never await compilation from the request hook.

- [x] **Step 5: Run hook and real-projection tests GREEN**

```powershell
uv run --frozen --extra dev pytest tests/test_main_hooks.py tests/integration/test_astrbot_projection_persistence.py tests/test_scheduler_nonblocking.py -q
```

## Task 3: Close the Durable Worker State Machine

- [x] **Step 1: Write failing repository and worker tests**

Test a real SQLite flow:

```text
PENDING -> LEASED -> COMPILING -> READY_TO_COMMIT -> COMMITTED
```

Assert the worker reads the job's frozen base/target even if newer Journal events exist, a compiler exception becomes bounded redacted `RETRY_WAIT`, cancellation leaves only an expiring lease, and one failed job does not terminate the scheduler.

- [x] **Step 2: Observe worker RED**

```powershell
uv run --frozen --extra dev pytest tests/storage/test_repository_jobs.py tests/test_compaction_worker.py -q
```

Expected: missing fenced compaction read and worker module.

- [x] **Step 3: Add the fenced compaction read**

Add `read_compaction_view(job_id, owner, lease_epoch, now)` to `SQLiteRepository`. In one read transaction, verify the live fence, load exactly `base_snapshot_id` plus ordered memberships, and read contiguous events through `target_high_water_mark`.

- [x] **Step 4: Implement one worker iteration**

`CompactionWorker.run_iteration()` must:

```text
claim -> COMPILING -> read frozen input -> compile_candidate
-> optional audit_semantic -> READY_TO_COMMIT -> publish_snapshot
```

Catch compiler/provider/validator errors at the iteration boundary, persist only stable stage/code/redacted text, use bounded exponential retry, and ignore stale-lease publication.

- [x] **Step 5: Implement lifecycle loop**

Provide idempotent `start()`, non-blocking `wake()`, and `close()`. Startup and periodic cycles recover expired leases; `close()` cancels and awaits the task.

- [x] **Step 6: Run worker tests GREEN**

```powershell
uv run --frozen --extra dev pytest tests/test_compaction_worker.py tests/storage/test_repository_jobs.py tests/storage/test_repository_publication.py tests/storage/test_compiler_publication.py -q
```

## Task 4: Add a Small-Model-Friendly Extractive Compiler

- [x] **Step 1: Write failing backend tests**

Use a fake generator returning JSON with event ids and exact quotes. Assert every emitted semantic text or anchor is an exact substring of its declared source event, ids are derived deterministically by SHA-256 rather than trusted from the model, base Capsules remain present, and prose or malformed JSON is rejected without publication.

- [x] **Step 2: Observe backend RED**

```powershell
uv run --frozen --extra dev pytest tests/test_astrbot_compiler_backend.py -q
```

- [x] **Step 3: Implement segment-local extraction**

For each deterministic segment, call the selected provider with tools disabled and a closed JSON contract. Accept only event-id plus exact-quote selections for goals, constraints, decisions, progress, open loops, preferences, entities, emotional context, and anchors. Discard no invalid critical output silently: reject the segment so the durable job retries.

- [x] **Step 4: Render the Checkpoint deterministically**

Retain base Capsules, append validated segment Capsules, and render role-labelled structured records and exact anchors. Do not ask the model for or inject a narrative conversation summary.

- [x] **Step 5: Compose provider selection**

The configured `compaction_provider_id` overrides all sessions. When blank, remember the current conversation provider per session in memory; after restart, a pending job waits until that session is observed again rather than choosing a different data recipient silently.

- [x] **Step 6: Run backend tests GREEN**

```powershell
uv run --frozen --extra dev pytest tests/test_astrbot_compiler_backend.py tests/test_compiler.py tests/test_compaction_validator.py -q
```

## Task 5: Lifecycle, Configuration, Documentation, and Verification

- [x] **Step 1: Wire worker startup and shutdown**

Create the backend and worker after migrations, start exactly one tracked task, and cancel/await it from the Star class's directly defined `terminate()`.

- [x] **Step 2: Replace engineering-only configuration descriptions**

Expose understandable controls for context capacity, automatic start percentage, Provider View switch percentage, and optional compaction provider. The provider override description must warn that choosing another provider sends conversation-derived source material to another data recipient.

- [x] **Step 3: Update architecture and status docs**

Document that compaction is progressive enhancement, the fast path is deterministic reconstruction rather than compression, and the bounded empty projection is the hard fallback.

- [x] **Step 4: Run all quality gates**

```powershell
uv lock --check
uv run --frozen --extra dev ruff format --check .
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev mypy astrcontinuum main.py
uv run --frozen --extra dev pytest -q
python "<astrbot-skill>\scripts\validate_plugin.py" .
```

All commands must exit zero before packaging.
