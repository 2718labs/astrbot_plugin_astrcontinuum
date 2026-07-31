# IMPL-002 Immutable Contracts and Canonical Byte Semantics

Owner: bugkiller-sol-code-writer
Depends on: impl-001

## Goal

Implement the frozen CRM records and deterministic canonical serialization used by every later experiment stage.

## Context

- Data model: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-data-model.md`
- Experiment boundary: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-experiment-design.md`
- Toolchain: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\python-toolchain.md`
- Task 1 protected-source checkpoint must continue to verify.

## Write Scope

- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\contracts.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\canonical.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tests\test_contracts.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-data-model.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tasks\impl-002.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\index.md`

## Steps

1. Set all temporary and uv paths from the Python toolchain contract.
2. TDD RED: add focused tests before production files. Cover exact enum values and dataclass field order, frozen/slots behavior, multibyte UTF-8 counts, canonical mapping order, enum/dataclass conversion, explicit rejection of unsupported or non-string-keyed values, semantic-hash metadata exclusion, and semantic-hash sensitivity to content and weight version.
3. Run the focused tests and retain the expected missing-module/API failures.
4. Implement every enum and frozen slotted dataclass exactly as `crm-data-model.md` specifies.
5. Implement canonical functions with deterministic output. Do not silently stringify unsupported keys or values.
6. Run focused tests, all existing tests, Ruff check/format, Pyright, protected manifest verification, 2718lab project validation, and pre-commit.
7. Commit with message `feat: define deterministic CRM contracts`.

## Acceptance

```powershell
uv run pytest tests/test_contracts.py tests/test_evidence.py -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run python -m crm_experiment.evidence --repo G:\AstrContinuum --manifest evidence\protected-source-hashes.json --verify
```

Expected:

- All commands exit 0; the symlink capability test may remain an explicit platform skip.
- Contract public fields match the locked model exactly.
- Canonical serialization is stable across equivalent mapping insertion order.
- `git status --short` is clean after the task commit.

## Return

Return one status (`DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, `BLOCKED`), changed files, commit SHA, exact RED/GREEN and full verification outputs, and blockers.
