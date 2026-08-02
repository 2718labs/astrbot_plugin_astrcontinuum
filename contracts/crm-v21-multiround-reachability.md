# CRM V21 Multi-round Evidenced Reachability Contract

## Scope

`multiround_v21` is a pure, bounded evaluator for an already materialised
sequence of V21 reencoding requests.  It performs no I/O, storage write,
history lookup, CAS operation, solver call, model call, or mutation of caller
objects.  It is a reachability wrapper over V21-006 only:

```python
evaluate_evidenced_multiround_v21(
    initial_state,
    initial_head,
    rounds,
    *,
    expected_initial_state_hash,
    expected_initial_advance_head_root,
) -> EvidencedMultiroundResultV21
```

For each reachable round it calls exactly one public V21-006 operation:

```python
plan_evidenced_reencoding_v21(...)
```

It must not call V21-005's verifier directly or repeatedly.

The maximum number of rounds is exactly 12.  A 13th round is rejected during
preflight, before any planner invocation.  A zero-round tuple is legal and
returns `COMPLETED`.

## Concrete input boundary

`rounds` must be an exact built-in `tuple` whose elements are exact nominal
`RoundInputV21` instances in contiguous order `0..n-1`.  A round stores:

```python
RoundInputV21(
    round_index: int,
    source_envelope: SourceEnvelopeV21,
    policy: EvidencedReencodingPolicyV21,
    expected_base_state_hash: str,
    expected_source_envelope_root: str,
    expected_policy_root: str,
    expected_advance_head_root: str,
)
```

No factory, callable, generator, path, lazy summary, or source-like stand-in
is accepted.  The source envelope and policy are revalidated by their public
V21 validators, and their current roots must equal the supplied expected
roots.  The evaluator repeats this validation immediately before each round
is used, so bypassing frozen dataclasses through nested mutation fails closed.

The initial wrapper and head are nominal public V21 values.  The evaluator
validates the wrapper with `validate_evidenced_capsule_state_v21`, validates
and rebuilds the head with public `validate_advance_chain_head_v21` and
`advance_chain_head_root_v21`, and ensures the head names the current state,
barrier projections, ledger projection, and generation.

## Preflight and stale boundary

Before planning, preflight validates the tuple cap, every nominal input,
contiguous indexes, all source-envelope roots, and all policy roots.  It then
checks the initial state/head anchors.

At each round, the following must name the currently held, last verified
predecessor before V21-006 may be called:

- `expected_base_state_hash`;
- `expected_advance_head_root`;
- `source_envelope.base_state_hash`.

The first mismatch is terminal `STALE`.  The evaluator does not call that
round's planner, does not run any later round, and does not advance its local
state/head.  The result retains only the identities of the last verified
prefix.

## Accepted advance boundary

After V21-006 returns, the evaluator independently revalidates the returned
plan without invoking V21-005.  An advance is accepted only when all of these
hold:

- the planned wrapper, matrix, candidate, and verifier result are exact
  nominal public V21 values;
- the verifier result is `VERIFIED` and `consumed_delta is True`;
- candidate envelope/policy equal the round inputs and bind the current roots;
- candidate record, segment, authorization, barrier, and ledger roots rebuild
  from public data;
- the sparse matrix is revalidated and binds the base/envelope/policy,
  candidate roots, proposed hash, generation, high-water, and objective;
- the sparse matrix equals the pure canonical projection of the current base,
  envelope, policy, and candidate: solver/evaluation bound, source/segment
  axes, decision-derived disjoint children, H/M/F/G/P/X, and all six widths;
- the candidate proposed hash, transition root, and public rebuilt head agree
  with the verifier output;
- generation advances by exactly one, high-water is the real source maximum
  over the predecessor, and the successor head links to the predecessor head;
- real `evidenced_resident_layout_v21` C/B/R accounting equals the matrix
  objective, including resident saving and ledger loss delta.

Only after every check succeeds is the successor made the next local
predecessor.  `release_count` is then derived from the accepted candidate's
barrier advances only.  Released rows themselves are never retained.

## Terminal statuses

`MultiroundStatusV21` is closed:

| Status | Closed exit reason | Planner behavior | Trace behavior |
| --- | --- | --- | --- |
| `COMPLETED` | `COMPLETED` | all requested rounds accepted | one accepted trace per round |
| `STALE` | `STALE_INITIAL_ANCHOR` or `STALE_ROUND_ANCHOR` | no call for stale round | verified prefix only |
| `REJECTED` | `PLANNER_REJECTED` | V21-006 raised `EvidencedPlanningRejectedV21` | verified prefix only |
| `POSTCONDITION_REJECTED` | `POSTCONDITION_REJECTED` | return failed independent checks | verified prefix only |

Unexpected exceptions are not converted into a successful result.  In
particular, only V21-006's declared `EvidencedPlanningRejectedV21` becomes
`REJECTED`; unrelated coding failures propagate.

Failures do not create synthetic trace rows.  `traces` contains only the
accepted `VERIFIED` prefix.  Failure information lives in the compact terminal
fields `status`, `exit_reason`, `failed_round_index`, and a fixed body-free
`reason` text.  This avoids presenting an unaccepted attempt as experimental
data.

## Compact report and root

`EvidencedMultiroundResultV21` intentionally stores only:

- initial/final state and advance-head roots;
- body-free `RoundAnchorV21` input roots;
- accepted `MultiroundTraceV21` rows;
- terminal status/reason/failed index; and
- `run_root`.

An accepted trace contains only compact roots/anchors, before/after C/B/R,
resident delta, loss delta, generation, high-water, release count, matrix
root, transition root, and `ADVANCED`.  It contains no source body, envelope,
candidate, released source row, full state, or full head.

`run_root` is a deterministic SHA-256 domain commitment
`crm-v21-multiround-run-s3/v1`.  Its non-self-referential payload contains
only input roots, policy roots, compact accepted traces, terminal metadata,
and final root identities; it never contains itself, bodies, envelopes,
candidates, or full states.  Result validation recomputes the root and checks
that trace chaining, terminal shape, and final identities match the accepted
prefix.

## Required verification evidence

The implementation must demonstrate:

- three real chained verified rounds;
- a stale second round stopping before a third planner call;
- planner rejection and postcondition rejection with no accepted trace or
  release;
- rootless segment and parent-compaction fixtures;
- zero rounds, 12 byte-identical rounds, and 13-round preflight with zero
  planner calls;
- malformed/lazy inputs and nested frozen-mutation revalidation;
- trace and run-root tamper rejection;
- exact C/B/R, generation, high-water, state-root, and head-root chaining;
- focused tests, repository tests, static checks, lock validation, and a
  protected-file hash audit.
