# CRM V21 Evidenced Sparse Matrix Planner

## Scope

V21-006 is a pure, transient planner over an already validated
`EvidencedCapsuleStateV21`, `AdvanceChainHeadV21`, `SourceEnvelopeV21`, and
`EvidencedReencodingPolicyV21`. It creates no history, storage, CAS entry,
network request, model request, external-solver request, or state mutation.
The only acceptance authority is the public V21-005 verifier.

The public operation is:

```python
plan_evidenced_reencoding_v21(
    base, current_head, source_envelope, policy,
    *,
    expected_base_state_hash,
    expected_source_envelope_root,
    expected_policy_root,
    expected_advance_head_root,
) -> EvidencedPlannedCandidateV21
```

The four expected anchors are forwarded unchanged to
`verify_evidenced_reencoding_candidate_v21`. A selected candidate is passed to
that verifier exactly once. The planner returns only when its status is
`VERIFIED`; stale anchors, work-limit exhaustion, a malformed/infeasible
candidate, or an oracle refusal raises `EvidencedPlanningRejectedV21` and
returns no candidate.

## Sparse witness

`EvidencedMatrixPlanV21` is a canonical sparse witness, not a dense numerical
matrix. It contains these ordered axes:

- source: `(record_id, commitment, role, core_required, contribution_keys)`;
- current segment: `(segment_id, canonical_hash, role_counts,
  contribution_keys)`;
- planned child: `(child_id, kind, source_rows, parent_rows)`.

`kind` is only `ROOTLESS_SEGMENT` or `PARENT_COMPACT`. Rootless children
consume one or more source rows and no parent rows. A compact child consumes
exactly one parent row and no source row.

The sparse relations are:

- `H`: directed source-row hard edges;
- `M`: mandatory source rows;
- `F`: sparse nonzero source-coverage cells;
- `G`: sparse nonzero source-bridge cells;
- `P`: directed current-parent-row to planned-child-row edges;
- `X`: one canonical, one-hot outcome row for every source and every current
  segment.

`M` is exactly the source rows whose `core_required` bit is true, union both
endpoints of every `H` edge. The matrix contract rejects any missing or extra
`M` row, and rejects an `M` row that does not choose `EXACT` in `X`.

`F` and `G` consist only of canonical `SparseBinaryCellV21(row_index,
column_index)` nonzero cells: a present cell is one and every absent coordinate
is an implicit zero. Dense vector rows and explicit values are not part of the
public shape. Their widths remain explicit so vector domains are bound without
zero padding; cells are sorted, unique, and range-checked by `(row, column)`.

`P` is exactly the current-parent-to-`PARENT_COMPACT` child incidence declared
by `child.parent_rows`. `X` is ordered as all source rows followed by all
segment rows. A source X row selects exactly one of `EXACT`, `SEGMENT`, or
`DROP`; only `SEGMENT` names a consuming rootless child. A segment X row
selects exactly one of `RETAIN`, `COMPACT`, or `DROP`; only `COMPACT` names a
consuming compact child. This relation is bidirectional: every rootless
`child.source_rows` entry must select that child through its source `SEGMENT`
X row, and every compact `child.parent_rows` entry must select that child
through its segment `COMPACT` X row. Conversely, a child-referencing X row
must name a child of the matching kind that owns exactly that source or parent
row. The public matrix boundary rejects any mismatch before rooting.

## Frozen public bounds

The root rejects payloads exceeding the frozen V21 limits: at most 256 source
rows, at most 256 current-segment rows, and at most 256 combined X decisions;
at most 64 planned children; at most 1,024 H edges; and at most 64 contribution
keys per source or segment axis row. `F` and `G` widths are each at most 64 and
their sparse cell counts cannot exceed `source_count * width`. `M` cannot
exceed its source axis, `P` is the exact compact-child incidence and cannot
exceed the segment axis, and `X` is exactly the canonical source-plus-segment
axis. The positive evaluation count is at most 65,536 and objective loss is at
most 1,000,000 units. At every public root or plan boundary, each exact-nominal
axis, relation, sparse cell, decision, and objective row is revalidated from
its current fields before cross-row relations are trusted; a runtime mutation
of a frozen dataclass cannot bypass these checks.

## Matrix commitment

The root domain is `crm-v21-evidenced-matrix-s3/v1`. Its canonical payload
binds all of the following:

- base-state hash, source-envelope root, policy root, and closed solver mode;
- the source, segment, and child axes;
- `H/M/F/G/P/X` plus all declared widths;
- positive evaluation count, bounded by the public global cap of `65_536`;
- integer objective values;
- record-decision root and segment-decision root;
- proposed evidenced-state hash, target generation, and target high-water.

The integer objective is `(control_bytes, source_body_bytes, resident_bytes,
loss_units, saved_bytes)`. C/B/R comes from the validated evidenced wrapper's
real resident layout; `resident_bytes == control_bytes + source_body_bytes`.
There are no dense rows, float scores, epsilon comparisons, quadratic terms,
or solver-optimality claims.

The matrix root is the actual `matrix_root` in the V21-005 transition input;
it is not a placeholder or an opaque fixture digest.

## Canonical candidate actions

Every action ID is a domain-separated hash of the base hash, envelope root,
policy root, action kind, and its canonical inputs. Child segment IDs and fold
policy hashes derive only from the resulting action ID and the child namespace;
they never depend on the matrix root or proposed hash, preventing a root cycle.

Rootless actions group eligible (non-mandatory) source records by
`(namespace, incarnation)` in canonical order. They materialize a rootless
child, exact first-fold authorization, release barrier, durable barrier
projection, contribution locators, and ledger charge. Coverage and bridge
vectors are deterministic bounded aggregates of the group evidence.

Parent actions start with exactly one current parent and create exactly one
child. The child binds its complete parent bytes through
`segment_input_commitment_root_v21`; its parent is consumed once and all
segment locators are repointed to that child. Parent compaction has no
rootless authorization or source-release barrier.

Every materialized choice includes canonical record and segment decisions,
children, authorizations, barriers, ledger advance, proposed wrapper/index,
proposed hash, matrix root, and transition root before the verifier receives
it.

## Bounded deterministic selection

For `EXACT_SMALL`, the planner enumerates every action subset permitted by
`max_new_segments`. It rejects before searching if that exact finite count is
greater than `max_plan_evaluations`; it never truncates a claimed exhaustive
search. Feasible candidates use the frozen lexicographic objective:

```text
(resident_bytes, loss_units, canonical_action_id_tuple)
```

For `DETERMINISTIC_GREEDY`, the all-`EXACT`/`RETAIN` baseline is first. Only a
candidate with a strictly positive real C/B/R resident-byte saving is applied.
Competing positive actions compare integer `loss / saved` ratios by cross
multiplication; an equal ratio uses the complete canonical action-ID tuple as
the tie-break. Every sweep first requires enough remaining work budget to score
the complete canonical set of eligible one-step proposals; otherwise planning
rejects before selecting a canonical prefix. The work cap counts scored
materializations (baseline plus proposals). Rebuilding the already selected
deterministic candidate for finalization is non-scoring and does not consume an
additional evaluation.

Both modes enforce policy source-row, hard-edge, decision, new-segment, loss,
per-role-loss, capacity, and resident-budget limits. V21-006 makes no claim
about feature quality, semantic utility, or unconstrained global optimality.
