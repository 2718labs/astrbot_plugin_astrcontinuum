# SC-02 Sparse Numerical Core

Owner: numerical-core-implementer
Depends on: SC-01

## Goal

Implement the bounded graph types, constraint compiler, linear-algebra backends, state reduction,
query solver, and error certificate without touching AstrBot integration.

## Context

Read:

- `contracts/context-engine.md`
- `astrcontinuum/runtime/types.py`
- project `pyproject.toml`
- project `requirements.txt`

Do not read sibling task cards or the full product history.

## Write Scope

- `astrcontinuum/context_graph/`
- `tests/context_graph/`
- `pyproject.toml`
- `requirements.txt`
- `uv.lock`

## Steps

1. Write failing tests for closed enums, finite bounded values, unique ids, CSR validation, and
   content-free representations.
2. Implement immutable graph and certificate types.
3. Write failing backend tests for finite input, bounded dense conversion, singular systems,
   backend selection, backward error, and lazy imports.
4. Implement NumPy, optional SciPy, and tiny reference backends without explicit inversion.
5. Write failing tests for fix-one, fix-zero, equality grouping, contradictory constraints, and
   retained constraint support.
6. Implement exact constraint compilation and projected systems.
7. Write failing reduction tests comparing reduced and full solutions and rejecting unsupported
   constraint elimination.
8. Implement solve-based Schur reduction and reconstruction.
9. Write failing certificate tests for stationarity, constraint, reconstruction, provenance, and
   required-block gates.
10. Implement bounded query solving and stable error codes.
11. Add NumPy through uv, add SciPy only as an optional extra, and keep the root plugin
    requirements synchronized.

## Acceptance

Use task-local D-drive temporary directories and run:

```powershell
uv lock --check
uv run --frozen --extra dev pytest tests/context_graph -q
uv run --frozen --extra dev ruff check astrcontinuum/context_graph tests/context_graph
uv run --frozen --extra dev mypy astrcontinuum/context_graph
```

All commands must exit zero. Verify that importing `astrcontinuum` alone does not add `numpy` or
`scipy` to `sys.modules`.

## Return

Return changed files, exact commands and results, numerical limits, conclusion, and blockers. Do
not commit.
