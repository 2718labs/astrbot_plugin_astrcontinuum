# CRM Experiment Python Toolchain

## Boundary

- Project root: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3`.
- Python: 3.13, pinned by `.python-version`.
- `G:\AstrContinuum` remains read-only.
- All temporary files, uv caches, environments, test caches, and evidence stay under the project root on `D:`.

## Package and dependency rules

- Use `uv` only for project initialization, dependency changes, locking, sync, build, and command execution.
- Use the `uv_build` backend emitted by a local `uv init --build-backend uv` probe.
- Distribution name is `crm-experiment`, whose normalized import package is `crm_experiment`.
- Use src-layout: `src\crm_experiment`.
- Runtime dependency lower bounds are `numpy>=2.4.6` and `matplotlib>=3.10.9`; exact resolution is frozen in committed `uv.lock`.
- Development dependencies are `pytest>=9.0.3`, `ruff>=0.8`, `pyright>=1.1`, and `pre-commit>=4.0`.
- Do not introduce pip, Poetry, Pipenv, Conda, requirements files, setuptools, Black, Flake8, isort, autopep8, or mypy.

## Quality configuration

- Ruff line length is 88 with explicit `E4`, `E7`, `E9`, `F`, `I`, and `UP` selections.
- Pyright uses `standard` mode, includes `src` and `tests`, and targets Python 3.13.
- Pytest discovers `tests` and disables its cache provider.
- Public APIs require parameter and return annotations.
- Pre-commit runs Ruff check, Ruff format, and Pyright.

## Process environment

Before any project command:

```powershell
$TaskRoot = 'D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3'
$TaskTemp = Join-Path $TaskRoot '.tmp'
New-Item -ItemType Directory -Path $TaskTemp -Force | Out-Null
$env:TEMP = $TaskTemp
$env:TMP = $TaskTemp
$env:TMPDIR = $TaskTemp
$env:UV_CACHE_DIR = Join-Path $TaskRoot '.uv-cache'
$env:UV_PROJECT_ENVIRONMENT = Join-Path $TaskRoot '.venv'
$env:PYTHONDONTWRITEBYTECODE = '1'
Set-Location $TaskRoot
```

Use `uv run ...` for all standalone experiment commands. Repository regressions may use only `G:\AstrContinuum\.venv\Scripts\python.exe`.

## Required verification

```powershell
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

The 2718lab `python-engineering/scripts/validate_project.py` validator must also pass using the currently resolved plugin installation path.
