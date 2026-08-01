# TOOLING-001 Pre-commit Pyright Environment Repair

Owner: terra-high-tooling-writer
Depends on: impl-004

## Goal

Make the isolated pre-commit Pyright hook resolve the same project imports as
`uv run pyright`, without skipping or weakening type checking.

## Context

- `uv run pyright` passes with zero diagnostics.
- `uv run pre-commit run --all-files` fails only because the isolated Pyright
  hook cannot resolve `pytest` from the project development environment.
- The project runtime dependencies are `numpy` and `matplotlib`; tests import
  `pytest`.
- Production root `G:\AstrContinuum` remains read-only.

## Write Scope

- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\.pre-commit-config.yaml`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tasks\tooling-001.md`

## Steps

1. Keep the existing Ruff and Pyright hook revisions unchanged.
2. Add only the isolated-hook dependencies needed to resolve project imports.
3. Do not skip Pyright, reduce its include paths, or weaken type checking.
4. Run the direct and pre-commit type-check paths.

## Acceptance

```powershell
uv run pyright
uv run pre-commit run --all-files
```

Expected: both commands exit zero; the Pyright hook reports `Passed`.

## Return

Return changed files, exact outputs, conclusion, and blockers.
