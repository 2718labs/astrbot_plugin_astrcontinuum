# SC-06 Playable Release Surface

Owner: release-surface-implementer
Depends on: SC-01R, SC-02

## Goal

Prepare the user-visible v0.2.0 surface for a local playable package while implementation tasks
finish in parallel.

## Write Scope

- `pyproject.toml`
- `metadata.yaml`
- `_conf_schema.json`
- `tests/test_config_schema.py`
- `README.md`
- `CHANGELOG.md`

## Requirements

- Set every public version in scope to `0.2.0` / `v0.2.0`.
- Add one clear `context_engine_mode` choice with `active`, `shadow`, and `off`; default to
  `active`. Explain that Active only replaces fallback after verification, Shadow measures but
  sends fallback, and Off uses the deterministic path. Do not expose numerical knobs.
- Keep key provisioning and rotation guidance accurate and never suggest pasting a secret into
  WebUI, chat, logs, or command arguments.
- Explain how administrators know the plugin is working through `/context_status` and
  `/context_inspect`, including content-free mode/outcome/reduction evidence.
- State that AstrContinuum modifies only the temporary provider request and does not rewrite
  AstrBot conversation history or the authoritative Journal.
- Describe pressure-triggered activation, exact-source structured compilation, encrypted
  persistence, fail-open behavior, NumPy required baseline, and optional SciPy accelerator.
- Remove the stale warning that SQLite is not encrypted.
- Do not mention unpublished source material, comparisons, or domain analogies.
- Keep the exact existing `logo.png`; do not transform it.
- Do not add contributor or co-author claims. Commit identity is handled later by the
  coordinator.

## Acceptance

- JSON config schema parses and its focused tests pass.
- README, changelog, metadata, and pyproject agree on v0.2.0 and security behavior.
- Ruff/format checks for the focused test pass.
- `git diff --check` passes.

## Return

Return changed files, exact checks/results, and blockers. Do not commit.
