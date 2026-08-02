# V21-004 Evidenced Reencoder Contract Primitives

Owner: one Terra-max implementation writer
Depends on: accepted V21-003 current-support wrapper

## Routing record

Risk class is High: a false commitment could authorize context loss. The scope
is a new bounded multi-type contract with high interface ambiguity and high
verification cost, one writer and a disjoint scope. Terra-max implements the
pure module; Sol supplied independent design and will conduct final review. No
Spark gate applies because there is no narrow severe reproducible blocker.

## Goal

Implement only the transient evidence rows and domain-root helpers frozen in
`contracts/crm-v21-evidenced-reencoder.md`. This is a foundation for controlled
lossy reorganization, not a Summary, archive, candidate verifier or optimizer.

## Exact write scope

- `contracts/crm-v21-evidenced-reencoder.md` (frozen input after registration)
- `src/crm_experiment/reencoding_contracts_v21.py`
- `tests/test_reencoding_contracts_v21.py`
- `tasks/v21-004-evidenced-contracts.md` (frozen input after registration)

## Required behavior

Create the two closed enums, all graph/contribution/envelope/decision/
authorization/advance/policy types, `canonical_segment_hash_v21`, and each
root helper named by the contract. Use public V21/V21-003 canonical helpers;
do not copy or relax private V21-002 logic. Tuples are nominal, sorted and
unique. Source envelopes reject any graph node/edge view that is not exactly
the supplied validated record universe.

## Strict TDD

1. RED: missing module, then nominal/domain/order/duplicate cases fail.
2. RED: retain a record's body/source commitment but add/remove/retarget a hard
   edge; the old graph/envelope root must reject or change.
3. RED: tamper every decision/authorization/advance/policy field and replay a
   root across another domain; each must reject or change.
4. GREEN: smallest pure types, validators and root helpers only.

## Acceptance

- Envelope binds incoming canonical records, all nodes, edges and contribution
  rows; source commitment alone cannot hide a hard-edge change.
- Decision roots bind all decision fields and complete canonical parent hashes.
- First-fold/barrier/ledger roots bind every prior/root/generation input.
- Policy root binds frame policy and all work/loss bounds.
- Required command:
  `uv run pytest tests/test_reencoding_contracts_v21.py tests/test_evidenced_state_v21.py tests/test_contracts_v21.py tests/test_resident_v21.py -q -p no:cacheprovider`.

## Prohibited areas

Do not modify `contracts_v21.py`, `resident_v21.py`, `evidenced_state_v21.py`
or existing V21-002/V21-003 tests. Do not add a verifier, optimizer, state
writer, persistent history, source archive, Summary/model/API/network path,
runner/config/data/results/figures, git stage/commit, or workflow completion.

## Temporary root

`D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\.git\codex-tmp\v21-evidenced-contracts`

Set `CODEX_TASK_TEMP`, `TEMP`, `TMP`, `TMPDIR`, and `PYTHONPYCACHEPREFIX`
under that root before commands that create temporary artifacts.

## Amendment 001 — authorized safety closure

This append-only amendment supersedes conflicting wording in this task card
and the frozen contract. Freeze the exact domains:
`crm-v21-canonical-segment-s3/v1`, `crm-v21-hard-graph-s3/v1`,
`crm-v21-fold-contributions-s3/v1`, `crm-v21-source-envelope-s3/v1`,
`crm-v21-evidenced-policy-s3/v1`, `crm-v21-record-decisions-s3/v1`,
`crm-v21-segment-decisions-s3/v1`, `crm-v21-first-fold-auth-s3/v1`,
`crm-v21-barrier-advance-s3/v1`, `crm-v21-ledger-advance-s3/v1`, and
`crm-v21-evidenced-transition-s3/v1`; the base domain remains
`crm-v21-evidenced-state-s3/v1`.

Every helper hashes fields excluding its own claimed root, while its nominal
validator recomputes and equality-compares that root. Add only a narrow public
source-record facade in the new module: it uses public `source_commitment_v21`
with closed-field and domain checks, but makes no base-state closure claim and
does not change `contracts_v21.py`.

The envelope carries sorted, unique `source_records` plus sorted unique
`incoming_record_ids` as their subset, and roots the entire canonical source
universe. Graph nodes must equal that universe; hard edges must equal the
flattened complete `hard_depends_on` relation with target commitment matching.
Cycles are allowed. Add structured sorted rows
`FirstFoldSourceV21(record_id, source_commitment, contribution_keys)` and
`ReleasedSourceV21(record_id, source_commitment)` in place of parallel IDs or
bare releases. Enforce the exact outcome/disposition child rules, effective
source-record uniqueness by record ID, and a closed solver enum
`EXACT_SMALL | DETERMINISTIC_GREEDY`.

Freeze finite module caps for transient rows, edges and contribution keys;
policy bounds must be nonnegative plain integers within their applicable caps.
Barrier and ledger advances bind base/envelope/policy/decision/barrier context,
exclude their claimed `new_root` from their helper payloads, and recompute
before validator comparison. The transition root must bind base, envelope,
policy, matrix, record and segment decision roots, sorted authorization and
barrier roots, ledger root, proposed hash, target generation and target
high-water.

RED tests must cover each amendment, including same-source-commitment hard-edge
mutation and cap failures, before implementation begins.

## Amendment 002 — exact caps and helper payloads

Freeze these module constants exactly:
`MAX_EVIDENCED_SOURCE_RECORDS_V21=256`,
`MAX_EVIDENCED_HARD_EDGES_V21=1024`,
`MAX_EVIDENCED_CONTRIBUTION_KEYS_PER_ROW_V21=64`,
`MAX_EVIDENCED_REPRESENTATIVE_CANDIDATES_V21=64`,
`MAX_EVIDENCED_ROOT_ROWS_V21=256`,
`MAX_EVIDENCED_PLAN_EVALUATIONS_V21=65536`,
`MAX_EVIDENCED_LOSS_UNITS_V21=1000000`, and
`MAX_EVIDENCED_NEW_SEGMENTS_V21=64`. Policy bounds are nonnegative plain
integers that do not exceed their matching caps.

Freeze exact canonical helper payloads (each excluding its claimed root):
envelope=(base, source_records, incoming_ids, hard_root, contribution_root);
record/segment decision=(base, envelope, policy, rows);
firstfold=(base, envelope, policy, namespace, incarnation, generation, child,
structured sources); barrier=(base, envelope, policy, decision, namespace,
incarnation, prev_root, prev_high_water, new_high_water, generation, released
rows); ledger=(base, envelope, policy, decision, barrier_roots, prev_root,
prev counts, delta counts, new counts, prev gen, new gen, loss units);
transition is exactly the Amendment 001 tuple. Hard graph and contributions
continue to bind complete ordered rows; canonical segment binds complete bytes.

## Amendment 003 — reserved matrix, dual decisions, state-resolved segments

Reserve `crm-v21-evidenced-matrix-s3/v1` for a future matrix root. V21-004
only strict-domain-validates it and creates no matrix helper/planner/verifier.
`proposed_hash` is exactly a `crm-v21-evidenced-state-s3/v1` digest. Replace
every Barrier/Ledger singular decision root with both typed
`record_decision_root` (`crm-v21-record-decisions-s3/v1`) and
`segment_decision_root` (`crm-v21-segment-decisions-s3/v1`); swapping them
must fail.

Freeze `canonical_segment_hash_v21(state: CapsuleStateV21, segment_id: str)`:
call public `validate_capsule_state_v21`, resolve exactly the current named
segment, and hash its complete canonical bytes. Do not add a standalone/private
segment validator or edit `contracts_v21.py`.

Freeze `MAX_EVIDENCED_COVERAGE_VECTOR_V21=64` and
`MAX_EVIDENCED_BRIDGE_VECTOR_V21=64`. Contribution vectors are nominal tuples
of binary plain integers within those caps. RED must cover their caps and bad
bits, wrong matrix/proposed domains, record/segment root swap, and state lookup.

## Amendment 004 — anchored advance chain and byte ceilings

Supersede prior advance-root semantics. Freeze raw source caps
`MAX_EVIDENCED_SOURCE_BODY_BYTES_PER_RECORD_V21=65_536`,
`MAX_EVIDENCED_SOURCE_BODY_BYTES_TOTAL_V21=8_388_608`, and canonical envelope
cap `MAX_EVIDENCED_CANONICAL_ENVELOPE_BYTES_V21=67_108_864`. Validate per-body
raw UTF-8, then aggregate raw bytes and aggregate hard edges before graph/root
work, then canonical envelope bytes before its root.

Add domains `crm-v21-barrier-advance-seed-s3/v1`,
`crm-v21-ledger-advance-seed-s3/v1`, and `crm-v21-advance-head-s3/v1`; retain
literal durable domains `v21-barrier-root-s3`/`v21-ledger-root-s3` and never
cross-compare them with transient roots. Add closed `AdvanceCursorKindV21`,
barrier/ledger cursor rows, and `AdvanceChainHeadV21`. Seed genesis only via
`seed_advance_chain_head_v21(base, expected_state_hash=...)`, validating the
public evidenced wrapper and constant-time matching its computed external hash.

Replace advance `previous_root` with a prior advance-head root and typed prior
cursor; add recomputed `new_durable_root` through the frozen barrier/ledger
projection payloads. Barrier and ledger payloads bind both record and segment
decision roots plus all Amendment 004 listed cursor/head/generation fields.
Add a next-head helper that binds old head, proposed state hash, target
generation, complete next cursor set, and only carries forward unaffected
cursors. V21-004 remains shape/root-only: no verifier, planner, writer,
history list, or transition execution.

Required RED/Green attacks include durable/transient domain confusion, seed
swaps/tampering, unanchored or wrong-hash genesis, existing null seed, prior
head/cursor tampering, projection mismatch, incomplete next-head binding, and
65,537-byte, >8 MiB raw, >64 MiB canonical, and exact-boundary cases.

## Amendment 005 — reachable canonical-envelope ceiling

This append-only amendment supersedes only Amendment 004's canonical-envelope
ceiling: `MAX_EVIDENCED_CANONICAL_ENVELOPE_BYTES_V21=33_554_432`. The 32 MiB
bound is deliberate fourfold headroom above the fixed 8 MiB raw-body aggregate,
so canonical JSON escaping has a reachable, tested ceiling. It replaces the
unreachable 64 MiB value; do not weaken other bounds or pad inputs to fake the
attack.

RED/Green coverage now uses 96 records of `"\\x00" * 65_536`, zero hard
edges and minimal legal contribution rows: 6,291,456 raw bytes pass the raw
aggregate bound while canonical escaping exceeds 32 MiB and must reject. An
ordinary raw-valid envelope under the 32 MiB canonical ceiling must pass.

## Amendment 006 — root validation precedes envelope measurement

Validate envelope base-state, hard-graph, contribution, and claimed envelope
root domains before canonical-envelope byte serialization, and pass only the
checked values to that measurement. The focused RED must show an oversized
invalid root failing domain validation rather than triggering the byte cap.

## Amendment 007 — full-payload source-root gate

Make `source_envelope_root_v21` the sole full-payload root gate with required
keyword-only arguments `base_state_hash`, `source_records`,
`incoming_record_ids`, `graph_nodes`, `hard_edges`,
`contribution_evidence`, `hard_graph_root`, and `contribution_root`. Remove
the old compact-only overload entirely: no optional rows or bypass remain.
Normalize full rows, enforce the 32 MiB canonical pre-root bound, then
recompute/compare hard and contribution subroots before producing the compact,
unchanged root payload `(base_state_hash, source_records, incoming_record_ids,
hard_graph_root, contribution_root)`. `SourceEnvelopeV21` delegates and only
compares its claimed root.

Required RED/Green: the 96-record NUL attack cannot call source-envelope
`_domain_hash_v21`; missing a required row argument raises `TypeError`;
mismatched full rows/subroots reject; and the pre-Amendment-007 legal fixture
root remains byte-for-byte unchanged.
