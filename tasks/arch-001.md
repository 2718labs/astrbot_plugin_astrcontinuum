# ARCH-001 CRM v2 Logical State and Exact Resident Bytes

Owner: primary-main-arch001
Depends on: impl-008b

## Goal

Add a parallel CRM v2 foundation that separates source-level logical semantics
from resident physical layout. Preserve the complete v1 implementation and its
materialized input hashes.

## Locked Design

- `LogicalAtomV2` is the immutable original source record. Its fixed-length
  `source_id` is `sha256:<canonical control-header + payload-hash>`, so
  conflicting reuse fails closed without retaining an unbounded seen-source
  ledger or raw released payload.
- `SourceReceiptV2` is mandatory control state for every source referenced by
  the frontier. It retains canonical semantic keys, role, revision/status,
  exactness, dependencies, core flag and payload digest, but no text or raw
  provenance. The receipt independently re-derives `source_id`, so a released
  winner cannot forge its key or core flag.
- `ActiveRecordV2(atom, active_keys)` separates original source semantics from
  current key ownership. Partial supersession changes only `active_keys`; it
  never rewrites `atom.semantic_keys` under the old identity.
- `KeyWinnerV2` is one mandatory canonical dominance mark per known semantic
  key. It commits revision, content-bound source identity, status, record hash,
  and core flag. Retracted/superseded winners survive without payload as
  tombstones; an active non-core winner may also survive after its payload is
  deliberately released. Every receipt key must remain represented in the
  frontier; ARCH-001 forbids silent partial frontier GC.
- `semantic_keys` and `provenance` are canonical non-empty set tuples;
  `depends_on` is a canonical possibly-empty set tuple. They are unique,
  lexicographically sorted, and contain no empty items. Every active receipt
  dependency must resolve to another active frontier source even when either
  payload is released. Kernel dependency closure is separately checked against
  actual kernel payloads.
- Soft source weights do not participate in source identity or tie-breaking.
  `WeightPolicyV2` canonically binds a version, one weight per active frontier
  source, and a policy digest. Post-learning changes this policy, never source
  IDs. Packing eligibility/immutability is deferred to the physical-policy
  layer in ARCH-002 and is not a logical source field.
- `CapsuleStateV2` stores the mandatory frontier, receipts and weight policy,
  an unpacked logical kernel, and canonical physical body blocks. ARCH-001
  supports direct blocks only; packing belongs to ARCH-002.
- Every generation decodes the frozen resident state before latest-key
  resolution. Physical siblings are independent logical records, so updating
  one key cannot delete another.
- Latest resolution uses `(revision, source_id)`, not `as_of`. A canonical
  `DeltaEnvelopeV2` separately requires an atomic complete one-tick high-water
  interval, or a replay-only no-op at the existing high-water. It rejects gaps,
  overlap and stale/new mixing. Any self-validating historical record is an
  inert no-op, including a source that has completely left the frontier.
- Frontier size and semantic-key bytes have explicit frozen bounds. Frontier
  entries are charged to the Capsule budget and cannot be silently garbage
  collected; overflow fails closed. Any future GC requires a separate explicit
  epoch/finality contract.
- `resident_bytes_v2` is exactly `utf8(canonical_json(state))`.
  `resident_breakdown_v2` must reproduce that value from the empty-body frame,
  canonical block bytes and JSON separators without approximation.
- `logical_semantic_hash_v2` hashes the canonical winner frontier, source
  receipts and weight policy. Receipt/source hashes commit logical semantics
  while excluding releasable payload bytes and physical policy, so payload
  packing/release cannot forge a logical change. `resident_hash_v2` hashes the
  complete frame and actual payload layout.
- Kernel admission checks both slot/text rules and the complete canonical
  kernel-only state. The continuity floor is 4608 bytes; metadata cannot bypass
  it.
- No model, API, network, Summary fallback, real conversation, or production
  write is allowed.

## TDD Evidence

Initial RED: four collection errors because all v2 modules were absent.

First GREEN: 23 focused tests passed, covering:

- invalid/noncanonical logical metadata;
- exact additive resident bytes with Unicode and JSON escaping;
- empty-body punctuation;
- logical/resident hash domain separation;
- one-key and multi-key partial supersession/retraction;
- revision/source winner parity independent of `as_of`;
- active weight excluding a retracted winner;
- normal kernel selection, below-K admission, slot/text overflow;
- the 5000-byte provenance kernel overflow counterexample.

Second RED: eight failures exposed source-ID reuse, noncanonical set metadata,
dangling kernel dependencies, and logical/physical hash-domain gaps.

Second GREEN: 56 focused tests passed, additionally covering:

- exact replay versus conflicting source-ID reuse in both input orders;
- deterministic same-revision winners independent of input order;
- empty, duplicate, and out-of-order set metadata rejection;
- dangling active dependencies after supersession;
- invalid kernel slot/schema definitions and selected-kernel dependency closure;
- exact additive bytes for 0/1/2/4 body blocks with control-character escaping;
- logical-hash sensitivity to semantic fields and deliberate insensitivity to
  physical packing policy.

Third RED: four collection errors proved the intermediate API could not
represent a winner frontier or source-record/active-key split. The motivating
counterexamples were cross-generation multi-key replay and retraction followed
by a lower-rank arrival.

Third GREEN: 53 rewritten focused tests passed after one 51/53 intermediate
run exposed a non-finite-weight error-domain bug and one incorrect test fixture.
The final suite additionally covers:

- original multi-key source identity across partial supersession and replay;
- tombstone dominance, legal higher-rank reactivation, and released-payload
  dominance without replay rehydration;
- fixed-length content-bound source IDs without a seen-source ledger;
- frontier/active-record bidirectional invariants and missing-core rejection;
- exact replay no-op, unknown historical replay, continuous watermarks, and
  stale/new batch rejection;
- logical hash stability across payload release and distinction between empty
  and retracted-empty state;
- mandatory frontier bytes, registry limits, and frontier-driven kernel budget
  failure.

A subsequent two-test RED separated post-learning weights from source identity
and rejected inconsistent per-source control marks; the focused suite reached
55 passing tests.

Fourth RED followed Sol final review: one collection failure plus explicit
counterexamples showed that the state still lacked released-source receipts,
fully superseded replay was rejected, released dependencies could not advance,
and soft weights were not independently bound.

Fourth GREEN: 59 focused tests passed, additionally covering:

- receipt-bound key/core/status/revision control after payload release;
- fully superseded and arbitrary content-addressed historical replay as inert
  no-ops without payload rehydration;
- logical dependency closure over active frontier receipts, including released
  dependency and dependent payloads;
- independent canonical versioned weight policy and policy-hash validation;
- removal of soft weights and physical immutability from source identity;
- missing-core selection as an auditable invalid result rather than exception;
- complete receipt-key frontier coverage with no silent partial GC.

## Acceptance

```powershell
uv run pytest tests/test_contracts_v2.py tests/test_logical_v2.py tests/test_resident_v2.py tests/test_kernel_v2.py -q
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv lock --check
uv run python -m crm_experiment.evidence --repo G:\AstrContinuum --manifest evidence\protected-source-hashes.json --verify
uv run pre-commit run --all-files
```

Also rematerialize protocol v1 and require all three input manifest hashes to
remain unchanged.

## Final Gate Evidence

- Focused CRM v2 suite: `59 passed`.
- Complete repository suite: `198 passed, 1 skipped`.
- Ruff lint and format check: passed.
- Pyright: `0 errors, 0 warnings`.
- `uv lock --check`: passed.
- Protected production-source hash verification: passed.
- Pre-commit (`ruff`, format, Pyright): all hooks passed.
- DevKit Python validator: `0 errors, 0 warnings`. The MCP wrapper first timed
  out after 120 seconds, so that timeout was not counted; the bundled validator
  script was then run directly and completed successfully.
- Protocol v1 rematerialization remained byte-identical:
  - runtime:
    `902b2dadbb9ab6cdf5df99561cbbdde24ef0c17cc43c583c6fc34286866faa8e`
  - queries:
    `4d603c899fd426f69d7dcd3b3135db52d8143bc8f7c420d91365e8e42accd3f2`
  - gold:
    `20f41d99e0d943607c5783b5cd895b408da2aecd254f1de86f96dba717ed09c8`
- Independent Sol final review: PASS, with no remaining P0/P1 findings in the
  ARCH-001 scope.

## Return

Return RED/GREEN evidence, exact-byte and kernel-overflow evidence, v1 hash
comparison, full gates and blockers. Do not implement packing or run v2 arms.
