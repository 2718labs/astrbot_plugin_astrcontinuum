# SC-05 Runtime Mode and Observability

Owner: runtime-observability-implementer
Depends on: SC-02

## Goal

Wire the user-facing `active` / `shadow` / `off` mode into the composition root and show bounded,
content-free proof of actual engine use through the existing administration commands.

## Interface Contract

SC-03 keeps `AstrBotHookBridge.assemble_prepared()` returning `AssemblyResult` and exposes:

- a constructor engine-mode option using `ContextEngineMode`;
- the current engine mode;
- the most recent content-free live trace;
- a bounded current-session trace lookup that accepts a fully reconstructed `SessionKey`.

If the final SC-03 names differ, adapt imports only; do not change bridge implementation.

## Write Scope

- `main.py`
- `tests/test_main_hooks.py`

## Requirements

- Parse `context_engine_mode` case-insensitively from config with safe default `active`; invalid
  values must not crash plugin load and must resolve to the safe documented behavior.
- Pass the mode to `AstrBotHookBridge` during the existing encrypted initialization sequence.
- `/context_status` adds configured engine mode and latest content-free outcome only.
- `/context_inspect` adds current-session candidate/selection/relation/constraint/retained/reduced
  counts, bounded reduction ratio and residual band, recovery count, required/provenance coverage,
  and stable code.
- Reconstruct and compare the complete current `SessionKey` after existing repository queries and
  before returning session metrics. A cross-session race returns a stable unavailable result.
- Never render content, source ids, entity names, SessionKey, provider id, paths, exception text,
  NaN, or infinity.
- Keep lifecycle lock behavior, worker ownership, command permissions, and native fail-open
  behavior unchanged.

## Acceptance

- Focused main-hook tests cover all three modes, Active verified/degraded status, Shadow
  unverified evidence, Off, current-session isolation, race recheck, bounded formatting, and no
  sensitive output.
- Existing storage lifecycle and command tests remain green.
- Ruff, format, mypy, and `git diff --check` pass for the write scope.

## Return

Return changed files, exact tests/checks, status/inspect examples without content, conclusion,
and blockers. Do not commit.
