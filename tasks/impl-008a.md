# IMPL-008A Runner Metric Provenance

Owner: bugkiller-sol-code-writer
Depends on: impl-008

## Goal

Add the content-free byte and loss provenance required to compute the frozen
rate-distortion metrics without approximating or reconstructing released text.

## Context

- Task 9 requires cumulative history compression, step compression, kernel/body split, weighted omission, continuity, and stale-current metrics.
- Existing records contain persistent bytes and timing but not their required numerators or split fields.
- Production root `G:\AstrContinuum` remains read-only.

## Write Scope

- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\runner.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tests\test_runner.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tasks\impl-008a.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\index.md`
- regenerated ignored files under `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\results`

## Steps

1. TDD: require every query row to carry cumulative history bytes, step-input bytes, delta bytes, kernel/body bytes, omission/error risk, and continuity/stale flags.
2. Compute values during advancement while current delta and previous state are available; never persist raw released content.
3. Keep deterministic state manifest unchanged and timing-free.
4. Re-run smoke and full protocol; verify fixed denominator, no budget overshoot, and no content leakage.
5. Run all project gates and commit `fix: add runner metric provenance`.

## Acceptance

```powershell
uv run pytest tests/test_runner.py -q
uv run python -m crm_experiment.cli run --config config/protocol-v1.json --runtime data/runtime/protocol.json --queries data/public-queries/queries.json --output results
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

Expected: every record contains truthful content-free metric inputs and the
state-hash manifest remains byte-deterministic and content-free.

## Return

Return changed files, RED/GREEN evidence, full-run counts, validation outputs,
and blockers.
