# ARCH-002 Frozen Self-Contained Deterministic Packing Codec

Owner: primary-main-arch002
Depends on: arch-001

## Goal

Implement lossless physical packing for retained logical CONTEXT records. The
self-contained boundary is one complete frozen `CapsuleStateV2`, including its
mandatory receipts. Packing is not semantic compression and never masks
release omission.

## Context

- `src/crm_experiment/contracts_v2.py`
- `src/crm_experiment/resident_v2.py`
- ARCH-001 logical/frontier/receipt invariants remain authoritative.
- Production `G:\AstrContinuum` is read-only. Protocol v2 arms remain blocked.

## Write Scope

- `src/crm_experiment/contracts_v2.py`
- `src/crm_experiment/resident_v2.py`
- `src/crm_experiment/codec_v2.py`
- `src/crm_experiment/projection_v2.py`
- `tests/test_resident_v2.py`
- `tests/test_codec_v2.py`
- `tests/test_projection_v2.py`
- `tasks/arch-002.md`
- `index.md`

## Locked DMC1 Contract

- `PackedEntryV2` stores only `source_id`, `active_keys`, `text_middle`, and
  provenance. The corresponding receipt in the same frozen state supplies the
  complete immutable logical control header.
- `PackedContextBlockV2` stores canonical common prefix/suffix plus at least
  two leaf entries. It cannot contain direct or packed blocks.
- Decode reconstructs a complete `LogicalAtomV2`, re-derives its source and
  payload hashes, and then constructs `ActiveRecordV2`. Equality means complete
  record equality, not text-only equality.
- Factorization is unique: longest common prefix by Unicode code point, then
  longest common suffix over the remainders. Prefix and suffix never overlap;
  equal texts use the full text as prefix and an empty suffix/middle.
- No Unicode normalization is allowed. Empty text, prefix, suffix, and middle
  are structurally legal and round-trip exact UTF-8 bytes. DMC1 nevertheless
  emits only when at least one common affix is non-empty; a no-sharing group
  remains direct even if receipt-reference framing alone would be smaller.
- Entries are strictly source-ID sorted and unique. Multiple blocks cover
  contiguous slices of the global source-ID order; body blocks are canonical.
- Kernel records are never packed. Eligible records are v2 ACTIVE, physical
  policy-marked immutable, noncore, nonexact, dependency-free CONTEXT records.
- `PackingPolicyV2` is physical resident state. It binds codec version, bounded
  records/block, total decoded text bytes/block, and immutable source IDs. It
  affects resident bytes/hash but is excluded from the logical semantic hash.
- State construction decodes every packed entry and reuses receipt, frontier,
  active-key, dependency, core/body, uniqueness, and high-water invariants.
  Unknown kind, missing receipt, corrupted payload/provenance, forged keys,
  duplicate source, noncanonical factoring, nesting, and illegal body types
  fail closed.
- Every generation first flattens the frozen body to logical records. Packing
  depth is always one; a packed block is never wrapped by another packed block.
- ARCH-002 attempts one already-bounded contiguous group atomically. It does
  not auto-split an oversized group; ARCH-003 owns bounded group proposals.
- Emission compares complete canonical resident states with the same physical
  policy. It emits only when packed bytes are strictly smaller; equality and
  adverse overhead stay direct. Lossless packing risk is exactly zero.
- Projection accepts only a `FrozenStateViewV2`, consumes the common flat
  decoder, and emits source-bound decoded records. Coverage and injection bytes
  derive from emitted original texts, never compressed residual bytes.
- Malformed frozen state projects to UNKNOWN without reading an old Capsule,
  delta, truth, gold, Summary, other arm, model, API, or network.

## TDD Steps

1. RED/GREEN the packed entry/block/policy contracts and complete-state decode.
2. RED/GREEN canonical LCP/LCS factoring, round-trip corpus, permutation
   determinism, strict full-state savings, direct fallback, and idempotence.
3. RED/GREEN direct-view update behavior so packed A/B followed by an A-only
   update preserves B without nesting.
4. RED/GREEN source-bound projection parity and malformed-state UNKNOWN.
5. Prove packed/direct logical-hash equality, resident-hash distinction, exact
   resident breakdown, zero-delta stability, and unchanged v1 artifacts.

## Acceptance

```powershell
uv run pytest tests/test_codec_v2.py tests/test_projection_v2.py tests/test_resident_v2.py -q
uv run pytest tests/test_contracts_v2.py tests/test_logical_v2.py tests/test_kernel_v2.py -q
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv lock --check
uv run python -m crm_experiment.evidence --repo G:\AstrContinuum --manifest evidence\protected-source-hashes.json --verify
uv run pre-commit run --all-files
```

Also rerun the bundled DevKit Python validator and rematerialize v1 only under
the task-specific D-drive temporary root; all three v1 input hashes must remain
byte-identical.

## TDD and Gate Evidence

- Initial RED: three collection errors because `codec_v2`, `projection_v2`,
  and the physical packing contracts did not exist.
- First implementation run: `37 passed, 2 failed`. One fixture accidentally
  shared a suffix; the duplicate-residency fixture hit canonical ordering
  before uniqueness. Both fixtures were corrected without weakening the
  implementation.
- First focused GREEN: `39 passed`; ARCH-001 direct/logical/kernel regression:
  `47 passed`.
- Malformed nested-entry RED reproduced an escaping `AttributeError` rather
  than UNKNOWN. Leaf-entry validation and exception-domain closure made the
  focused counterexample GREEN.
- Independent Luna audit found two further P0 counterexamples: a mutated
  released receipt produced `no_matching_source`, and a mutated nested weight
  still produced a supported projection. Both tests failed as predicted;
  recursive frontier/receipt/weight/policy/record validation made them GREEN.
- The Luna no-affix ambiguity was resolved as a frozen encoder policy: empty
  affixes remain structurally decodable, while DMC1 never emits a group with no
  shared affix. Its duplicate fixture issue had already been corrected.
- Independent 5.3 Codex Spark scan identified the legacy direct-only
  `logical_v2` access boundary and one union-narrowing trap. ARCH-002 keeps the
  old entry fail-closed and requires `direct_view_v2` before resolution;
  ARCH-003 owns unified resolver integration. Pyright confirmed the narrowed
  implementation.
- Final affected suite: `90 passed`.
- Final complete suite: `229 passed, 1 skipped`.
- Ruff lint and format check: passed.
- Pyright: `0 errors, 0 warnings`.
- `uv lock --check`, protected production-source verifier, and all pre-commit
  hooks passed.
- Bundled DevKit Python validator: `0 errors, 0 warnings`.
- Protocol v1 D-drive rematerialization remained byte-identical:
  - runtime:
    `902b2dadbb9ab6cdf5df99561cbbdde24ef0c17cc43c583c6fc34286866faa8e`
  - queries:
    `4d603c899fd426f69d7dcd3b3135db52d8143bc8f7c420d91365e8e42accd3f2`
  - gold:
    `20f41d99e0d943607c5783b5cd895b408da2aecd254f1de86f96dba717ed09c8`
- Independent Sol final review: PASS with no P0/P1 blocker; its focused rerun
  reported `43 passed`. The only non-blocking interface note is to preserve the
  explicit `direct_view_v2` before legacy `resolve_latest_v2` discipline until
  ARCH-003 integrates the unified decoder.

## Return

Return observed RED/GREEN evidence, codec corpus and strict-savings fixtures,
malformed fail-closed evidence, complete gates, Sol review, and blockers. Do
not implement the optimizer or run Protocol v2 arms.
