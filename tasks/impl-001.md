# IMPL-001 Standalone Project and Protected-Source Checkpoint

Owner: crm-sol-code-writer
Depends on: plan-001

## Goal

Create the isolated uv-managed Python experiment repository and freeze a content-addressed, metadata-only checkpoint of protected production sources.

## Context

- Design contract: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-experiment-design.md`
- Toolchain contract: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\python-toolchain.md`
- Read-only production root: `G:\AstrContinuum`
- The implementation root is intentionally outside the dirty production worktree.

## Write Scope

- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\.git\`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\.gitignore`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\.python-version`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\.pre-commit-config.yaml`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\README.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\pyproject.toml`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\uv.lock`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\__init__.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\evidence.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tests\test_evidence.py`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\evidence\protected-source-hashes.json`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\product-brief.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\index.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-experiment-design.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\python-toolchain.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\plans\2026-07-30-capsule-recomposition-matrix-implementation.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tasks\spec-001.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tasks\plan-001.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tasks\impl-001.md`

## Steps

1. Set `TEMP`, `TMP`, `TMPDIR`, `UV_CACHE_DIR`, and `UV_PROJECT_ENVIRONMENT` exactly as the toolchain contract requires.
2. Verify the task root is not already a Git repository; initialize it and switch to `codex/crm-experiment`. Never stage or modify `G:\AstrContinuum`.
3. Run `uv init --build-backend uv` in `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\.tmp\uv-probe` only to obtain the locally supported `uv_build` requirement. Remove only that disposable probe after validating its resolved absolute path remains under the task root.
4. Create the project files from the 2718lab Python templates, adapted to distribution `crm-experiment` / import package `crm_experiment`, Python 3.13, and this contract. Use `uv add` and `uv add --dev` so `uv.lock` is authoritative.
5. Add `.venv/`, `.uv-cache/`, `.tmp/`, Python/tool caches, generated runtime/query/gold data, results, and raster/vector figure outputs to `.gitignore`. Do not ignore `uv.lock` or `evidence\protected-source-hashes.json`.
6. Add concise setup commands to `README.md`: `uv sync`, `uv run pre-commit install`, and the verification commands. Keep all experiment restrictions explicit.
7. TDD RED: create `tests\test_evidence.py` first. It must exercise sorting, deduplication, POSIX relative paths, byte size, and 64-character SHA-256. Run it and retain evidence that it fails because `crm_experiment.evidence` does not exist.
8. TDD GREEN: implement `sha256_file`, `collect_protected_hashes`, `write_manifest`, `verify_manifest`, and the CLI. Manifest schema version is 1 and rows contain only `path`, `bytes`, and `sha256`; the top level may contain `schema_version`, `repo`, and `files`. `write_manifest` must reject any output path located inside the protected repository, including the repository root itself.
9. Generate `evidence\protected-source-hashes.json` from the protected patterns in the implementation plan. Verify it immediately using the CLI.
10. Run the task acceptance commands and the 2718lab project validator. Review the diff and commit the task with message `chore: scaffold isolated CRM experiment`.

## Acceptance

```powershell
uv lock --check
uv run pytest tests/test_evidence.py -q
uv run python -m crm_experiment.evidence --repo G:\AstrContinuum --manifest evidence\protected-source-hashes.json --verify
uv run ruff check pyproject.toml src\crm_experiment tests\test_evidence.py
uv run ruff format --check src\crm_experiment tests\test_evidence.py
uv run pyright
```

Expected:

- Tests pass with no warnings.
- Protected manifest verification exits 0 and contains no source bodies.
- Project validator exits 0.
- `git status --short` is clean after the task commit.
- `G:\AstrContinuum` protected hashes still match the frozen manifest.

## Return

Return status `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`; list changed files, commit SHA, the exact RED and GREEN commands with outputs, all acceptance outputs, and blockers. Do not claim success without fresh evidence.
