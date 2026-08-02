# CRM V2.1 Evidenced Reencoder Contract Primitives

Inputs: V21-001 `sha256:f999609df7402cffb084a996436c8faf82b95a97a831ea9c345ca8f8bd4c5849`; V21-002 `sha256:907273309413b8c561e444b749715ffc6857fb32dea5f9f14938066635a24c31`; V21-003 `sha256:783dbc2f37aef030db364bf62eae5b1c440b1f93723a8e522e2bfa290be6e83b`.

## Purpose and boundary

These are transient, bounded evidence objects for a future controlled Capsule
reencoder. They authorize a source to remain `EXACT`, become `SEGMENT`, or be
explicitly `DROP`ped. They are neither Summary nor a source archive, model
interface, or production migration. Only `EvidencedCapsuleStateV21` and its
current index are durable; manifests, envelopes, authorizations and rolling
roots are transition-scoped and must never be added as resident history.

V21-004 creates pure nominal types and roots only. V21-005 will verify a
candidate and V21-006 will build bounded matrix plans. Existing V21-002 guard,
V21-003 wrapper/query and their tests remain unchanged.

## Trusted external anchors

The later verifier has mandatory keyword-only external current heads:

```text
expected_base_state_hash
expected_source_envelope_root
expected_policy_root
```

They come from a CAS/current head or transition receipt, never from a candidate
recomputation. They bind respectively the complete evidenced wrapper, source
envelope (including incoming canonical records and hard graph), and full
reencoding policy.

## Closed evidence rows

```text
ReencodingOutcomeV21 = EXACT | SEGMENT | DROP
SegmentDispositionV21 = RETAIN | COMPACT | DROP
SourceGraphNodeV21(record_id, source_commitment)
HardGraphEdgeV21(source_id, source_commitment, target_id, target_commitment)
FoldContributionEvidenceV21(record_id, source_commitment, contribution_keys,
                            role, coverage_vector, bridge_vector,
                            representative_candidates)
SourceEnvelopeV21(base_state_hash, incoming_records, graph_nodes, hard_edges,
                  contribution_evidence, hard_graph_root, contribution_root,
                  envelope_root)
RecordDecisionV21(record_id, source_commitment, outcome, child_segment_id,
                  contribution_keys)
SegmentDecisionV21(segment_id, canonical_segment_hash, disposition,
                   child_segment_id, contribution_keys)
FirstFoldAuthorizationV21(base_state_hash, source_envelope_root, policy_root,
                          namespace, incarnation, generation, child_segment_id,
                          record_ids, source_commitments, contribution_keys,
                          authorization_root)
BarrierAdvanceV21(namespace, incarnation, previous_root, previous_high_water,
                  new_high_water, released_record_ids, new_root)
LossLedgerAdvanceV21(previous_root, previous_role_counts, delta_role_counts,
                     new_role_counts, previous_generation, new_generation,
                     loss_units_delta, decision_root, barrier_roots, new_root)
EvidencedReencodingPolicyV21(frame_policy_hash, max_matrix_rows,
                             max_hard_edges, max_decisions,
                             max_plan_evaluations, max_new_segments,
                             max_loss_units, per_role_loss_caps,
                             segment_loss_units, drop_loss_units,
                             compaction_loss_units, solver_mode)
```

Rows are nominal tuples, canonically sorted and unique by their declared
identity. `canonical_segment_hash` binds the complete segment canonical bytes,
not merely its folded root. The source commitment omits hard dependencies, so
the separately rooted complete graph is mandatory.

## Root helpers

Each helper first validates and then hashes compact canonical JSON in a unique
domain:

```text
canonical_segment_hash_v21
hard_graph_root_v21
contribution_root_v21
source_envelope_root_v21
evidenced_reencoding_policy_root_v21
record_decision_root_v21
segment_decision_root_v21
first_fold_authorization_root_v21
barrier_advance_root_v21
loss_ledger_advance_root_v21
evidenced_transition_root_v21
```

The source envelope root binds its base hash, full incoming canonical record
bytes, graph nodes, every hard edge, and every contribution row. Decision roots
bind all IDs, commitments, outcomes, child IDs and keys. First-fold, barrier,
ledger and transition roots bind every listed prior/root/generation field.
Roots are not interchangeable across domains.

## Future rules frozen now

- Every source record will have exactly one outcome; core records and both ends
  of every hard-edge transitive closure must be `EXACT`.
- Every base segment will have one `RETAIN`, `COMPACT` or `DROP` disposition;
  a parent is consumed once and its full hash is evidence-bound.
- A rootless child needs a single-use authorization binding base, envelope,
  policy, generation, source IDs, commitments and contribution keys.
- Release later requires monotonic barrier and ledger advances rooted in the
  exact previous values. Parent history cannot be charged twice.
- The later matrix is sparse `H/M/F/G/P/X`: hard adjacency, mandatory closure,
  contribution coverage, bridges, parent incidence and one-hot outcomes. It
  uses real wrapper C/B/R and deterministic lexicographic objectives, never an
  unbounded dense estimate or epsilon tie break.

## Acceptance boundary

V21-004 proves strict type/domain/order/uniqueness checks and full commitment
coverage. It does not plan, mutate a Capsule state, authorize a transition,
perform I/O, use a model/network, or persist source/history evidence.

## Amendment 001 — frozen evidence closure and root domains

This amendment supersedes any conflicting V21-004 wording above. It is part of
the frozen V21-004 contract and is intentionally append-only.

### Exact independent digest domains

```text
canonical segment: crm-v21-canonical-segment-s3/v1
hard graph:        crm-v21-hard-graph-s3/v1
contributions:     crm-v21-fold-contributions-s3/v1
envelope:          crm-v21-source-envelope-s3/v1
policy:            crm-v21-evidenced-policy-s3/v1
record decisions:  crm-v21-record-decisions-s3/v1
segment decisions: crm-v21-segment-decisions-s3/v1
first fold:        crm-v21-first-fold-auth-s3/v1
barrier advance:   crm-v21-barrier-advance-s3/v1
ledger advance:    crm-v21-ledger-advance-s3/v1
transition:        crm-v21-evidenced-transition-s3/v1
base state:        crm-v21-evidenced-state-s3/v1
```

Every root helper hashes a compact canonical payload that excludes that
object's claimed root. Its corresponding nominal validator recomputes the
helper and equality-compares the claimed root. No root is self-referential and
roots are rejected across domains.

### Transient source universe and exact hard graph

Add a narrow public V21-004 source-record facade using only public
`source_commitment_v21`, nominal types, strict digest-domain checks and
closed-field checks. It validates source fields and hard edges but does not
claim base-state closure. Do not modify `contracts_v21.py`.

`SourceEnvelopeV21` contains `source_records` (the sorted, unique effective
transient universe) and `incoming_record_ids` (a sorted unique subset).
`source_records` replaces the earlier `incoming_records` root input; the
source-envelope root commits complete canonical `source_records`. V21-005,
not V21-004, resolves equality against base plus incoming or same-ID
supersession.

Graph nodes are exactly the source records' `(record_id, source_commitment)`
pairs. Hard edges are exactly the flattening of every
`source_records[*].hard_depends_on`; each target occurs in `source_records`
with the same commitment. Cycles are allowed. Missing, extra or retargeted
nodes/edges are rejected even where the source commitment did not change.

### Structured rows and closed solver mode

Add `FirstFoldSourceV21(record_id, source_commitment, contribution_keys)`;
first-fold source rows are sorted uniquely by `record_id` and replace the
parallel record-ID/source-commitment/contribution-key fields.

Add `ReleasedSourceV21(record_id, source_commitment)`; barrier releases are
sorted unique structured rows and replace bare released IDs.

`RecordDecisionV21` requires `child_segment_id is None` for `EXACT` and
`DROP`, and requires one for `SEGMENT`. `SegmentDecisionV21` requires
`child_segment_id is None` for `RETAIN` and `DROP`, and requires a non-self
child for `COMPACT`. Record identity is `(record_id, source_commitment)`;
effective source records are unique by `record_id`.

`SolverModeV21` is closed: `EXACT_SMALL | DETERMINISTIC_GREEDY`.
The module freezes finite hard caps for transient rows, hard edges and
contribution keys. All policy bounds are nonnegative plain integers and must
not exceed their relevant module caps; validation rejects cap violations.

### Advances and transition binding

`BarrierAdvanceV21` includes `base_state_hash`, `source_envelope_root`,
`policy_root`, `decision_root`, `generation`, and structured released sources,
in addition to its namespace/incarnation/previous-root/high-water fields. Its
root excludes `new_root` and the validator recomputes it before checking the
claimed `new_root` domain.

`LossLedgerAdvanceV21` likewise includes base/envelope/policy/decision/barrier
roots and excludes `new_root` from its helper payload. Its validator
recomputes the advance root before checking the claimed `new_root` domain.

`evidenced_transition_root_v21` binds exactly: base-state hash, source-envelope
root, policy root, matrix root, record-decision root, segment-decision root,
sorted authorization roots, sorted barrier roots, ledger root, proposed hash,
target generation, and target high-water. Each member is independently
domain-validated.

## Amendment 002 — frozen caps and exact helper payloads

This amendment further supersedes conflicting V21-004 wording above. The
following finite module constants are frozen exactly:

```text
MAX_EVIDENCED_SOURCE_RECORDS_V21 = 256
MAX_EVIDENCED_HARD_EDGES_V21 = 1024
MAX_EVIDENCED_CONTRIBUTION_KEYS_PER_ROW_V21 = 64
MAX_EVIDENCED_REPRESENTATIVE_CANDIDATES_V21 = 64
MAX_EVIDENCED_ROOT_ROWS_V21 = 256
MAX_EVIDENCED_PLAN_EVALUATIONS_V21 = 65536
MAX_EVIDENCED_LOSS_UNITS_V21 = 1000000
MAX_EVIDENCED_NEW_SEGMENTS_V21 = 64
```

Policy fields are nonnegative plain integers and may not exceed their matching
module caps. Root-row collections use `MAX_EVIDENCED_ROOT_ROWS_V21`.

The exact canonical helper payloads, all excluding a claimed root field, are:

```text
source envelope = (base_state_hash, source_records, incoming_record_ids,
                   hard_graph_root, contribution_root)
record decision  = (base_state_hash, source_envelope_root, policy_root, rows)
segment decision = (base_state_hash, source_envelope_root, policy_root, rows)
first fold       = (base_state_hash, source_envelope_root, policy_root,
                    namespace, incarnation, generation, child_segment_id,
                    structured_sources)
barrier advance  = (base_state_hash, source_envelope_root, policy_root,
                    decision_root, namespace, incarnation, previous_root,
                    previous_high_water, new_high_water, generation,
                    released_sources)
ledger advance   = (base_state_hash, source_envelope_root, policy_root,
                    decision_root, barrier_roots, previous_root,
                    previous_role_counts, delta_role_counts, new_role_counts,
                    previous_generation, new_generation, loss_units_delta)
transition       = (base_state_hash, source_envelope_root, policy_root,
                    matrix_root, record_decision_root, segment_decision_root,
                    authorization_roots, barrier_roots, ledger_root,
                    proposed_hash, target_generation, target_high_water)
```

The hard-graph and contribution helper payloads remain their complete
canonically ordered rows. The canonical-segment helper payload remains the
complete canonical segment bytes. `SourceEnvelopeV21` validates and compares
its hard, contribution and envelope roots; `FirstFoldAuthorizationV21`
validates and compares its authorization root. Advance helper payloads exclude
`new_root` as listed above, and advance validators never accept a root from an
unrelated domain.

## Amendment 003 — matrix reservation, dual decisions and state-resolved segment hash

This amendment further supersedes conflicting V21-004 wording above.
`crm-v21-evidenced-matrix-s3/v1` is the reserved matrix-root domain.
V21-004 only performs its strict domain validation; it creates no matrix helper,
matrix, verifier or planner. `proposed_hash` in a transition is exactly a
`crm-v21-evidenced-state-s3/v1` digest.

Replace every singular `decision_root` in `BarrierAdvanceV21`,
`LossLedgerAdvanceV21`, and their helper payloads with both
`record_decision_root` (`crm-v21-record-decisions-s3/v1`) and
`segment_decision_root` (`crm-v21-segment-decisions-s3/v1`). They are separate
typed inputs and cannot be swapped.

Freeze the canonical-segment helper signature as
`canonical_segment_hash_v21(state: CapsuleStateV21, segment_id: str)`. It must
call public `validate_capsule_state_v21`, resolve exactly the named current
segment, and hash that segment's complete canonical bytes. It must not employ
a standalone/private segment validator or modify `contracts_v21.py`.

Freeze two additional module constants:

```text
MAX_EVIDENCED_COVERAGE_VECTOR_V21 = 64
MAX_EVIDENCED_BRIDGE_VECTOR_V21 = 64
```

`FoldContributionEvidenceV21` vectors are nominal tuples of binary plain
integers whose lengths do not exceed the corresponding cap. RED coverage must
include vector cap/nonbinary cases, wrong matrix/proposed root domains,
record-vs-segment decision-root swaps, and state-resolved canonical-segment
lookup.

## Amendment 004 — anchored advance chain and bounded envelope bytes

This append-only amendment supersedes conflicting advance-chain and body-cap
wording above. Freeze these exact source limits:

```text
MAX_EVIDENCED_SOURCE_BODY_BYTES_PER_RECORD_V21 = 65_536
MAX_EVIDENCED_SOURCE_BODY_BYTES_TOTAL_V21 = 8_388_608
MAX_EVIDENCED_CANONICAL_ENVELOPE_BYTES_V21 = 67_108_864
```

Validate each raw UTF-8 source body first, then aggregate raw body bytes and
aggregate hard edges before graph materialization, commitment or root hashing,
then validate the full canonical envelope byte bound before its root exists.

### Advance domains and cursors

Add the independent domains
`crm-v21-barrier-advance-seed-s3/v1`,
`crm-v21-ledger-advance-seed-s3/v1`, and
`crm-v21-advance-head-s3/v1`. Durable domains remain the literal
`v21-barrier-root-s3` and `v21-ledger-root-s3`; existing transient advance
domains remain unchanged and are never cross-compared to durable roots.

Add closed `AdvanceCursorKindV21 = SEED | ADVANCE`,
`BarrierAdvanceCursorV21(namespace, incarnation, durable_root, high_water,
kind, transient_root)`, and
`LedgerAdvanceCursorV21(durable_root, role_counts, generation, kind,
transient_root)`. Barrier durable-root/high-water fields are both null or both
present. Canonical cursors are sorted uniquely by identity. A seed kind accepts
only the matching seed domain and an advance kind accepts only its matching
transient advance domain.

Add `AdvanceChainHeadV21(state_hash, generation, barrier_cursors,
ledger_cursor, previous_head_root, head_root)`. Its root binds every listed
field; an initial head has `previous_head_root=null`, while a successor names a
prior `crm-v21-advance-head-s3/v1` root.

`seed_advance_chain_head_v21(base: EvidencedCapsuleStateV21,
*, expected_state_hash: str)` must validate its public wrapper, compute its
evidenced state hash, constant-time compare that external expected hash, and
derive seed rows for every current durable barrier and ledger. It must not
manufacture trusted genesis from an unanchored wrapper. Barrier seed payload is
`{base_state_hash, prior_advance_head_root:null, namespace, incarnation,
durable_root, high_water}`. A future on-demand null seed uses the same payload
with null durable fields and a current prior-head root; V21-005 alone may
authorize that missing-base identity. Ledger seed payload is
`{base_state_hash, durable_root, role_counts, last_fold_generation}`.

### Amended advances, projections and successor heads

`BarrierAdvanceV21` removes `previous_root` and adds
`prior_advance_head_root`, `prior_cursor: BarrierAdvanceCursorV21`, and
`new_durable_root`. Its root payload is:

```text
{base_state_hash, source_envelope_root, policy_root,
 record_decision_root, segment_decision_root, prior_advance_head_root,
 prior_cursor, namespace, incarnation, previous_high_water, new_high_water,
 generation, released_sources}
```

`LossLedgerAdvanceV21` likewise removes `previous_root` and adds
`prior_advance_head_root`, `prior_cursor: LedgerAdvanceCursorV21`, and
`new_durable_root`; its root payload is:

```text
{base_state_hash, source_envelope_root, policy_root,
 record_decision_root, segment_decision_root, barrier_roots,
 prior_advance_head_root, prior_cursor, previous_role_counts,
 delta_role_counts, new_role_counts, previous_generation, new_generation,
 loss_units_delta}
```

V21-004 validates only shape and roots; V21-005 matches these cursors to a
trusted head/base state. New durable roots recompute through projection helpers
only: barrier payload
`{projection:"evidenced-advance-v1", previous_durable_root,
barrier_advance_root, namespace, incarnation, new_high_water, generation}`;
ledger payload
`{projection:"evidenced-advance-v1", previous_durable_root,
ledger_advance_root, new_role_counts, new_generation}`.

The next-head helper binds old head root, proposed state hash, target
generation, complete next barrier cursors and ledger cursor. Affected barriers
become ADVANCE/new-root cursors and unaffected values carry forward. V21-005
returns only the latest head, never a history list.

## Amendment 005 — reachable canonical-envelope ceiling

This append-only amendment supersedes only the prior canonical-envelope byte
ceiling. Freeze the exact value:

```text
MAX_EVIDENCED_CANONICAL_ENVELOPE_BYTES_V21 = 33_554_432
```

The 32 MiB ceiling supplies four times the 8 MiB aggregate raw-body allowance:
enough headroom for ordinary UTF-8 envelope metadata while still bounding the
reachable JSON escaping expansion. It replaces the unreachable 64 MiB value;
no other cap is relaxed or padded to manufacture an attack.

Coverage must include 96 records with `body="\\x00" * 65_536`, zero hard
edges, and minimal legal contribution rows. Their 6,291,456 raw body bytes are
under the aggregate cap, but their canonical JSON escapes exceed 32 MiB and
the source envelope rejects. The corresponding ordinary raw-valid envelope
must pass under the same 32 MiB ceiling.

## Amendment 006 — root validation precedes envelope measurement

Before serializing an envelope for the canonical byte ceiling, validate the
base-state, hard-graph, contribution, and claimed envelope roots as their
exact domains. The measurement uses those checked values. Thus an oversized or
otherwise invalid claimed root fails domain validation rather than consuming
unbounded canonical serialization work.

## Amendment 007 — full-payload source-root gate

`source_envelope_root_v21` is the only source-envelope root gate. Its required
keyword-only signature is exactly `base_state_hash`, `source_records`,
`incoming_record_ids`, `graph_nodes`, `hard_edges`, `contribution_evidence`,
`hard_graph_root`, and `contribution_root`; there is no legacy overload or
optional full-row bypass. It first validates and normalizes every full payload
field, enforces the full canonical pre-root 32 MiB bound, then recomputes and
compares the hard and contribution roots before emitting the source envelope
root.

The emitted compact root payload remains unchanged:
`{base_state_hash, source_records, incoming_record_ids, hard_graph_root,
contribution_root}`. Thus legal pre-Amendment-007 root hashes remain stable.
`SourceEnvelopeV21` delegates its complete field validation to this helper and
only compares its claimed envelope root.

Coverage must prove that the 96-record NUL body attack cannot invoke the
source-envelope `_domain_hash_v21`, omitting any required full-row parameter
raises `TypeError`, mismatched rows or subroots reject, and the fixed legal
fixture root remains unchanged.
