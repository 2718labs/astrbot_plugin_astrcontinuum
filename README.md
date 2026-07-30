# AstrContinuum Capsule Replacement Experiment

This isolated Python 3.13 project evaluates the deterministic Capsule
Recomposition Matrix design. It is an experiment only: it must not modify,
publish, or bypass validation for the production repository at
`G:\AstrContinuum`.

Runtime code must not use recursive summaries, released generations, sealed
gold data, hidden queries, model APIs, or production release paths as context.
The protected-source manifest stores metadata only: POSIX-relative paths, byte
sizes, and SHA-256 digests. It never stores source bodies.

## Setup

```powershell
uv sync
uv run pre-commit install
```

## Verification

```powershell
uv lock --check
uv run pytest tests/test_evidence.py -q
uv run python -m crm_experiment.evidence --repo G:\AstrContinuum --manifest evidence\protected-source-hashes.json --verify
uv run ruff check pyproject.toml src\crm_experiment tests\test_evidence.py
uv run ruff format --check src\crm_experiment tests\test_evidence.py
uv run pyright
```
