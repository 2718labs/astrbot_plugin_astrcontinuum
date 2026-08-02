# V21-002 Schema-3 Control Guard and Resident Accounting

Owner: one Terra-max implementation writer
Depends on: V21-001 accepted control-fold contract
Contract input: `sha256:f999609df7402cffb084a996436c8faf82b95a97a831ea9c345ca8f8bd4c5849`

## Goal

Implement the smallest independent schema-3 control-state contract and
resident-byte accounting layer needed to prove that every bounded control field
is measured and that invalid candidates roll back atomically.

## Exact Write Scope

- `src/crm_experiment/contracts_v21.py`
- `src/crm_experiment/resident_v21.py`
- `tests/test_contracts_v21.py`
- `tests/test_resident_v21.py`
- `tasks/v21-002-schema-guard.md`

## TDD Order

1. Add focused RED tests for schema/domain isolation, bounded control fields,
   hard closure, direct-parent lineage, stale records, query-label priority,
   and candidate rollback.
2. Add the minimum schema-3 types and validation guard to turn those tests
   GREEN.
3. Add canonical resident bytes/breakdown and domain-separated hashes, then
   extend tests for full control-plane accounting.

## Acceptance

- `ControlFoldBoundsV21` binds and validates every growth bound from the
  accepted contract.
- Schema 3 rejects V2 state/hash domains; dictionary/commitments/segments have
  no source-text recovery path.
- `validate_reencoding_candidate_v21` rejects stale per-record inputs, broken
  hard closure, non-base/tampered parents, lineage/capacity breaches, and budget
  breaches by returning the identical base state without consuming a delta.
- `resident_bytes_v21`/breakdown/hash account for frame, dictionary, barriers,
  sparse weights, segments, and loss ledger; source body size is separately
  reported, not silently omitted.
- Required command: `uv run pytest tests/test_contracts_v21.py tests/test_resident_v21.py -q`.
- Report the initial RED command/result, final GREEN result, Ruff, Pyright,
  changed paths, and remaining exclusion boundary.

## Explicitly Deferred to V21-003+

- The real `reencode_capsule_v21` decision planner, EXACT/SEGMENT/DROP matrix,
  segment construction, and optimizer.
- Projection/answer rendering, codec/decoder, V2 migration, V2.1 protocol,
  runner, config, data, results, figures, post-learning, or any model/API use.

## Prohibited

- Do not modify any `*_v2.py`, `contracts/crm-v21-control-fold.md`, old tests,
  `index.md`, `results`, `data`, `figures`, production tree, or workflow files.
- Do not use a local model, network/API, Summary runtime, broad formatter, or
  Git reset/checkout.

## Temporary Root

`D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\.git\codex-tmp\v21-schema-guard`

Set `CODEX_TASK_TEMP`, `TEMP`, `TMP`, `TMPDIR`, and `PYTHONPYCACHEPREFIX` under
that root before commands that create temporary artifacts.
