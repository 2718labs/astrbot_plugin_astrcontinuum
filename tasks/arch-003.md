# ARCH-003 Bounded Source Optimizer and Atomic Recomposition Gate

Owner: primary-main-arch003
Depends on: arch-002

## Goal

Select logical sources with an exact canonical resident-state oracle, encode one
new frozen Capsule, and commit it only after a complete atomic gate succeeds.
Internal mismatches preserve the old state and do not consume the delta.

## Context

- `src/crm_experiment/contracts_v2.py`
- `src/crm_experiment/codec_v2.py`
- `src/crm_experiment/resident_v2.py`
- `src/crm_experiment/logical_v2.py` is a read-only input; `kernel_v2.py` is
  changed only to size the same resident-policy certificate used by the final
  canonical encoder.
- Production `G:\AstrContinuum` remains read-only. Protocol v2 stays blocked.

## Write Scope

- `src/crm_experiment/contracts_v2.py`
- `src/crm_experiment/matrix_v2.py`
- `src/crm_experiment/optimizer_v2.py`
- `src/crm_experiment/recompose_v2.py`
- `src/crm_experiment/resident_v2.py`
- `src/crm_experiment/kernel_v2.py` (exact kernel trial must include the frozen
  recomposition-policy certificate)
- `tests/test_matrix_v2.py`
- `tests/test_optimizer_v2.py`
- `tests/test_recompose_v2.py`
- `benchmarks/benchmark_optimizer_v2.py`
- `tasks/arch-003.md`
- `index.md`

## Locked Method

- Matrix rows are logical source records. Direct/packed blocks are physical
  encodings, never semantic alternatives competing with their own sources.
- `CandidatePolicyV2` is frozen and outcome-independent. Direct logical sources
  remain eligible; pack proposals use source-ID adjacency and a fixed
  `max_pack_neighbors`, never all-pairs or dense candidate-squared matrices.
- Dependency closure and conflicts remain in the source-ID universe. Released
  noncore payload is not selectable and cannot be revived from receipts.
- Exact-small enumerates logical source subsets only below a fixed threshold.
  Large cases use deterministic incremental greedy with exact marginal
  resident bytes, fixed source-ID tie-break, fixed work budget, and bounded
  drop/one-swap refinement.
- A reference evaluator remains available. Incremental and reference choices
  must match in the preregistered integer/risk-zero domain; near floating ties
  use the exact local canonical-plan reference comparator over plans already
  evaluated in that bounded greedy step. `reference_tie_breaks` is a frozen
  counter; this does not invoke unbounded global enumeration for large inputs.
- The cost oracle and state builder share the same canonical encoder.
  Predicted bytes must exactly equal `resident_bytes_v2(final_state)`.
- The gate checks decoder roundtrip, predicted/actual bytes, requested budget,
  source partition, kernel coverage, dependency closure, current conflict,
  codec eligibility, strict pack savings, and logical/resident hash domains.
- Any internal cost, decoder, or gate mismatch returns a public `GateReport`,
  preserves the old state, and sets `consumed_delta=False`. `KERNEL_ONLY` is
  only an optimizer result for a valid kernel-only state, never a catch-all.
- Atomic order is decode frozen state; apply bounded delta; build kernel;
  select/release logical sources; encode; gate; commit; then release old
  state/delta/workspace.
- Report packing savings separately from release omission. History/current
  ratios cannot be presented as packing compression.
- No wall-clock value is a hard correctness decision. Work/proposal/evaluation
  counters are frozen gates; benchmark median/p95/peak are descriptive.
- No model, API, network, Summary fallback, gold/truth access, real conversation,
  other-arm input, or production write is allowed.

## TDD Steps

1. RED/GREEN source rows, sparse dependencies/conflicts, bounded adjacency pack
   proposals, deterministic policy, and n=72/n=77 work bounds.
2. RED/GREEN exact full-state oracle, exact-small/reference equivalence, and
   deterministic large-case greedy/refinement counters.
3. RED/GREEN the false-feasible regression using the smallest legal current-v2
   fixture that preserves the original 500-byte counterexample's intent.
4. RED/GREEN atomic recompose and public gate reports, including kernel
   overflow, predicted/actual mismatch, decoder corruption, dependency failure,
   budget overshoot, and zero-delta stability.
5. Prove direct-only ablation shares identical logical inputs, every emitted
   pack has strict positive savings, and all release metrics use fixed source
   and weight denominators.

## Acceptance

```powershell
uv run pytest tests/test_matrix_v2.py tests/test_optimizer_v2.py tests/test_recompose_v2.py -q
uv run pytest tests/test_contracts_v2.py tests/test_codec_v2.py tests/test_projection_v2.py tests/test_resident_v2.py tests/test_logical_v2.py tests/test_kernel_v2.py -q
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv lock --check
uv run python -m crm_experiment.evidence --repo G:\AstrContinuum --manifest evidence\protected-source-hashes.json --verify
uv run pre-commit run --all-files
```

Run the bundled DevKit Python validator, the bounded microbenchmark under the
task-specific D-drive temporary root, and a D-drive-only v1 rematerialization.
All three v1 hashes must remain unchanged.

## TDD and Gate Evidence

- RED/GREEN covered missing v2 matrix/optimizer/recompose surfaces, the legal
  `500B` continuity-floor rejection and `4608B` packable analogue, exact-small
  versus independent reference equivalence, public gate rollback, direct-only
  ablation, and source-release/non-revival.
- Additional final RED/GREEN regressions cover recursive no-op request
  validation for mutated nested historical atoms and kernel slots, packed
  multi-round A-only update preserving the unchanged B sibling, stale immutable
  packing-policy rollback, correct released-control accounting, finite near
  ties, same-sign-infinity ties from nonpositive marginal resident bytes,
  stricter valid kernel-schema rollback, direct-only no-delta re-encoding, and
  drop-only refinement of a zero-weight source under nonmonotonic exact costs.
- Each newly encoded state binds a compact hash of its kernel schema and
  candidate policy. The same certificate is included in the kernel trial and
  full canonical encoder, so the zero-delta fast path cannot reuse a state
  under a stricter schema or a different physical encoding policy.
- The exact proposal-count gates are `207` for `n=72` (one kernel source,
  `71` pack-eligible sources, `k=3`) and `204` for `n=77` (seven payloads
  already released, `70` pack-eligible sources, `k=3`).
- Final bounded benchmark (three descriptive runs per case):
  - `n72-kernel`: `207` proposals, `2` oracle/full-state evaluations,
    `35` emitted packs, median `1091.9343ms`, p95 `1175.5377ms`, max peak
    `398167B`.
  - `n77-no-kernel-7-released`: `204` proposals, `2` oracle/full-state
    evaluations, `35` emitted packs, median `1071.1813ms`, p95 `1211.5900ms`,
    max peak `402505B`.
  - `n72-kernel-constrained`: `207` proposals, `73` oracle evaluations,
    `1` greedy step, `4` refinement evaluations, median `13567.5732ms`, p95
    `13979.7807ms`, max peak `636009B`; the frozen work bound was reached.
  - `n77-no-kernel-7-released-constrained`: `204` proposals, `72` oracle
    evaluations, `1` greedy step, `4` refinement evaluations, median
    `13854.9972ms`, p95 `15993.7474ms`, max peak `333220B`; the frozen work
    bound was reached.
- Full local suite: `263 passed, 1 skipped`; Ruff, format, Pyright, lock check,
  protected production-source verifier, and all pre-commit hooks passed.
- ARCH-003 focused suite: `34 passed`; preceding v2 contracts/codec/logical/
  kernel regression suite: `90 passed`.
- DevKit Python validator: `0 errors, 0 warnings`. Its MCP wrapper timed out
  after 120 seconds during the observed connection instability, so the same
  bundled local validator script was run directly under the D-drive task root.
- D-drive-only v1 rematerialization remained byte-identical:
  - runtime: `902b2dadbb9ab6cdf5df99561cbbdde24ef0c17cc43c583c6fc34286866faa8e`
  - queries: `4d603c899fd426f69d7dcd3b3135db52d8143bc8f7c420d91365e8e42accd3f2`
  - gold: `20f41d99e0d943607c5783b5cd895b408da2aecd254f1de86f96dba717ed09c8`
- Independent Sol final review: **PASS**; no P0/P1/P2 findings remain. It
  independently reran the `34` focused tests, confirmed proposal counts
  `207/204`, real constrained `greedy_steps=1` and
  `refinement_evaluations=4`, encoder full-state evaluations `<=2`, and no
  Protocol v2/network/production write.

## Return

Return RED/GREEN evidence, exact/reference equivalence, bounded work counters,
atomic rollback evidence, benchmark output, full gates, Sol final review, and
blockers. Do not run Protocol v2 arms.
