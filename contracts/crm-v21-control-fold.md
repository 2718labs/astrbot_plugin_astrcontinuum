# CRM V2.1 Control-Fold Contract

## Identity and Boundary

- Protocol id: `crm-capsule-stress-v2.1-control-fold-v1`.
- State schema: `3`; V2 and V2.1 decoders, hashes, config, data, and result
  paths must reject one another.
- V2.1 is a lossy Capsule context cache. It never uses a Summary as subject
  state, a model/API/network, or a production write.
- V2's failed reachability receipt is immutable evidence. V2.1 may not reuse
  its protocol/config/bundle/result hashes or overwrite `data/v2`/`results/v2`.

## State Vocabulary

`CapsuleStateV21` contains only these durable classes:

- `frame`: a canonical `ControlFrameV21(protocol_id, schema_version,
  generation, high_water, accepted_budget, reencoding_policy_hash,
  capacity_policy_hash)`. It binds every state to schema 3 and freezes the
  capacity limits used to judge it. `capacity_policy_hash` binds one
  `ControlFoldBoundsV21` value, including every collection bound below.
- `exact_kernel`: all current active `core_required` records (the five core
  roles) and their hard-dependency closure; resident body is mandatory and
  exact.
- `hot_cache` and `hot_frontier`: bounded exact noncore records and their
  current keys. Their record and canonical-byte limits are part of the frame.
- `dictionary`: canonical interned semantic namespaces and keys referenced by
  resident exact state, segments, or barriers. It has a frozen entry/byte
  limit; historical per-source receipts and recoverable source text are never
  retained here.
- `capsule_segments`: bounded, computable folds of old noncore state.
- `fold_barriers`: monotonic namespace/incarnation barriers for old folds.
- `sparse_weight_policy`: a deterministic default plus sparse overrides, not
  a weight row per active source. Its override count is a frozen bound.
- `loss_ledger`: aggregate role counts, one rolling cumulative-loss root, and
  last-fold generation only; it contains no released source text, event list,
  per-source recovery log, or historical root sequence.

`CapsuleSegmentV21` has a domain-separated id, parent ids, input commitment
root, namespace id, generation range, source/role counts, deterministic
representative records, fixed-size coverage vector, fixed-size dependency
bridge vector, loss class/budget usage, and fold-policy hash. It is not prose
or a Summary. Its representatives are Capsule features, not recoverable source
records, and can only produce `CAPSULE_APPROX`.

`FoldBarrierV21(namespace, incarnation, folded_through_high_water,
cumulative_root)` is the irreversible control boundary for folded history. The
frame fixes maximum segment count, representatives per segment, dictionary
entries/bytes, hot records/bytes, barriers, sparse overrides, direct parent ids
per segment, parent-id bytes, and the one fixed-size loss-ledger record.
Exceeding any limit rejects the entire reencoding transition rather than
retaining unbounded control history.

## Exact Closure and Segment Lineage

A V2.1 hard dependency is an edge explicitly declared in
`ExactRecordV21.hard_depends_on`. It is resolved only when its target has a
resident exact body in `exact_kernel`. The kernel is exactly all current active
`core_required` records, every active record incident to a hard edge, and the
transitive target closure of those edges. An unresolved hard edge is one whose
source or target fails to resolve to exactly one such resident exact body with
a matching commitment; it invalidates the candidate. During a future V2
migration, every V2 `depends_on` edge is hard; it may not be reclassified
foldable by inference or by a later V2.1 declaration.

A segment may cover only noncore records outside that exact closure. Any
proposed fold touching an unresolved hard edge is invalid; the affected record
must remain exact or the whole transition rolls back. A segment's bridge vector
therefore records bounded *non-hard* continuity anchors only and cannot be used
to satisfy or manufacture a hard dependency.

`parent_ids` are canonically sorted, unique, and bounded by a frame limit. Each
is a direct resident segment of `base_state`, must precede the new segment
generation, and is atomically consumed (nonresident) when named by a child; a
child never copies transitive ancestors. The resulting lineage must be acyclic.
A segment's `input_commitment_root` domain-separates the complete canonical
bytes of each direct parent, newly folded commitment root, generation range, and
fold-policy hash. Reencoding may retain or lose information, but never promote
fidelity: `EXACT` body may come only from a pre-transition resident exact body
or a new post-barrier envelope body, never from a segment, dictionary, barrier,
ledger, parent id, or commitment. Parent-id bytes and the single loss-ledger
record are also covered by frozen frame bounds.

## Public Outcome Semantics

Every completed query over a nonempty, declared required contribution/key set
has exactly one label. Empty or malformed requests are rejected before
projection and do not receive a projection label. Query labels use this strict
priority order:

- `RELEASED_MISS` when any required contribution has neither resident nor
  segment support.
- Otherwise `CAPSULE_APPROX` when any required contribution uses a bounded
  segment feature, even if other required contributions are exact.
- Otherwise `EXACT` only when every required contribution is a resident exact
  body and no segment participates.
- A delta/reencoding attempt returns `STALE_REJECTED` when *any incoming
  record's* namespace, incarnation, and `as_of` fall at or below an applicable
  fold barrier. An envelope target high-water cannot mask such a record. The
  operation returns no query projection and preserves the prior state.

A non-stale delta/reencoding operation reports its `ReencodingResultV21`
commit/rollback fields, not a query label; it cannot be relabeled `EXACT`,
`CAPSULE_APPROX`, or `RELEASED_MISS`.

Neither a source id, receipt, dictionary entry, parent id, nor commitment root
may be treated as recoverable source text. `CAPSULE_APPROX` must never be
reported as `EXACT`.

## Reencoding Contract

```text
reencode_capsule_v21(
    base_state, envelope, requested_budget, reencoding_policy,
) -> ReencodingResultV21
```

For every eligible object exactly one decision applies: `EXACT`, `SEGMENT`, or
`DROP`. Core and hard-dependency objects are always `EXACT`. A segment may be
reencoded into a later segment; its parents then become nonresident. The
transition is atomic: any invalid barrier, hard bridge break, budget breach,
stale delta, codec failure, or invariant breach preserves the prior state.

## Non-Negotiable Invariants

1. `high_water`, incarnation, fold barriers, segment lineage, and the rolling
   loss root are monotonic; lineage is acyclic and parents belong to the base
   state.
2. A delta at or below an applicable fold barrier returns `STALE_REJECTED`;
   it cannot revive prior body text.
3. Exact kernel coverage is 100%; every transitive `hard_depends_on` target has
   resident exact body, and no unresolved hard edge is folded or dropped.
4. Resident canonical bytes include dictionary, barriers, sparse weights,
   segments, and frame fields; source-text byte counts are a separate metric.
5. Segment count, representative count, coverage-vector size, and bridge
   vector size, parent count/bytes, dictionary entries/bytes, hot records/bytes,
   barrier count, sparse override count, and loss-ledger bytes are frozen policy
   bounds, so old control history remains bounded.
6. No selection/reencoding path may read sealed query gold or shadow payload.
7. `migrate_v2_to_v21` imports only V2-resident text. Previously released V2
   text becomes a miss/fold commitment, never reconstructed body text.
8. Canonical serialization includes every field above and validates the frame
   before a resident-byte, state-hash, or query outcome is reported.

## Evaluation Boundary

First measure V2.1 full-control reachability before smoke or arms. Report
factorization-only savings separately from folding savings, and report exact,
approximate, miss, stale, and fold-loss outcomes separately. The target
4,608-byte K is aspirational until measured under schema 3.
