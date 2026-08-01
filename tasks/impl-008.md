# IMPL-008 Freeze-before-Reveal Runner

Owner: bugkiller-sol-code-writer
Depends on: impl-007

## Goal

Implement deterministic protocol materialization and the phase-separated
six-arm runner that freezes content-free state evidence before revealing public
queries, while retaining no released raw state.

## Context

- Data model: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-data-model.md`
- Experiment boundary: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-experiment-design.md`
- Toolchain: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\python-toolchain.md`
- Frozen protocol/baselines: `src\crm_experiment\protocol.py` and `src\crm_experiment\baselines.py`
- Implementation plan: Task 8 in `plans\2026-07-30-capsule-recomposition-matrix-implementation.md`
- Production root `G:\AstrContinuum` remains read-only.

## Write Scope

- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\runner.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\cli.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tests\test_runner.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\data\runtime\protocol.json`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\data\public-queries\queries.json`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\data\sealed-gold\gold.json`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\data\input-manifest.json`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\results\records.jsonl`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\results\learning-events.jsonl`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\results\state-hash-manifest.json`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tasks\impl-008.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\index.md`

## Steps

1. Use only `.git\codex-tmp\impl008` under the D-drive task root for temporary artifacts and caches.
2. TDD RED: add smoke materialization, freeze ordering, generational release, fixed denominator, content-free learning-event, and deterministic rerun tests before production modules.
3. Materialize runtime events, public query specs, and sealed gold into three distinct canonical JSON files plus a SHA-256/byte-size input manifest.
4. `run_protocol(config_path, runtime_path, query_path, output_root)` must have no gold argument and never open sealed gold or records as runtime input.
5. Iterate stream, generation, budget, and `ARM_ORDER` deterministically. Advance and hash every arm for a checkpoint before opening the public query file; only then project the six queries.
6. Persist content-free state hashes, byte counts, outcomes, release counts, matrix/stage/runner timing, and tracemalloc peak bytes. Never write kernel/body/Summary/released raw text into manifests or learning events.
7. Records may contain only bounded projected answer text and selected IDs needed for offline scoring. Invalid checkpoints emit six explicit failure rows so no denominator is dropped.
8. Retain only each arm's current generation state. Run 16 zero-delta recompressions for CRM, Legacy, and Recursive Summary at every budget, plus the final 8K-to-K squeeze; record content-free stability evidence.
9. CLI exposes `materialize`, `run`, `aggregate`, and `render`; reporting imports remain lazy until Task 9 exists.
10. Run focused/all tests, Ruff check/format, Pyright, lock, protected manifest, both 2718lab validators, and pre-commit.
11. Materialize the full frozen protocol. Generated data/results remain ignored and must not enter the source commit.
12. Commit only source/test/task/index files as `feat: add phase-separated CRM experiment runner`.

## Acceptance

```powershell
uv run pytest tests/test_runner.py tests/test_protocol.py -q
uv run python -m crm_experiment.cli materialize --config config/protocol-v1.json --output data
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv lock --check
uv run python -m crm_experiment.evidence --repo G:\AstrContinuum --manifest evidence\protected-source-hashes.json --verify
uv run pre-commit run --all-files
```

Expected: input files are phase-separated and hashed; smoke state manifests are
byte-identical across roots; six arms retain every query denominator; all
content-free and budget/release assertions pass.

## Return

Return `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`, changed
source files, generated ignored artifacts, exact RED/GREEN/acceptance outputs,
review findings, and blockers.
