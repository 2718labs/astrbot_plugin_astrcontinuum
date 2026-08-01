# IMPL-008B Auditable Metric Provenance

Owner: sol-ultra-code-writer
Depends on: impl-008a

## Goal

Replace ambiguous or unavailable runner metric values with auditable,
content-free provenance before Task 9 aggregates or renders them.

## Context

- The full Task 8a rerun produced 51,840 fixed-denominator query rows and 8,640
  checkpoints with no budget overshoot or raw-content leakage.
- Independent review found that non-CRM arms encode unavailable loss values as
  zero and that `kernel_bytes + body_bytes` omits the state-frame overhead.
- Task 9 also needs a weight denominator, query-read provenance, independent
  kernel coverage evidence, churn counts, and explicit gold labels.
- Production root `G:\AstrContinuum` remains read-only. No model, model API,
  local inference runtime, or real conversation data may be introduced.

## Locked Metric Semantics

1. The measurement universe is the latest active atom per semantic key after
   consuming all deltas through the current high-water mark. It is measurement
   only and must never influence advancement or projection.
2. `semantic_weight_denominator` is the sum of weights in that universe.
   `weighted_retained_weight` counts each source atom at most once through
   state coverage. `weighted_omitted_weight` is their exact difference.
3. `kernel_bytes` is the mandatory resident partition: serialized kernel
   payload plus state-frame overhead. `body_bytes` is the elastic partition.
   They must sum exactly to `persistent_bytes`. Also record
   `kernel_payload_bytes` and `state_overhead_bytes` explicitly.
4. `net_released_bytes = step_input_bytes - persistent_bytes`; do not clamp a
   negative value. `released_count` counts step-input source IDs absent from
   the frozen new state, with a matching `release_denominator_count`.
5. `coverage_intersection_count` and `coverage_union_count` compare expanded
   source coverage of the previous and frozen new state. They contain no IDs.
6. Structural pre-gold diagnostics are computed uniformly for every arm.
   Unavailable model-specific `error_risk` is JSON null, never numeric zero.
   Final continuity and stale-current gates remain sealed-gold judgments in
   Task 9, not self-certification by these internal diagnostics.
7. Query-phase provenance records only the frozen state source actually read.
   `fallback_reads` counts forbidden reads of old Capsule, delta, canonical
   truth, sealed gold, or another arm. Advancement reads are not fallbacks.
8. Gold rows explicitly carry role, exact-anchor, and topic-return labels.

## Write Scope

- `src/crm_experiment/contracts.py`
- `src/crm_experiment/protocol.py`
- `src/crm_experiment/baselines.py`
- `src/crm_experiment/runner.py`
- `tests/test_protocol.py`
- `tests/test_runner.py`
- `tests/test_scoring.py`
- `data/input-manifest.json`
- `tasks/impl-008b.md`

Generated ignored files below `data/runtime`, `data/public-queries`,
`data/sealed-gold`, and `results` may be regenerated for verification but are
not committed.

## Steps

1. TDD RED: first require exact byte partitions, weight conservation, honest
   nullability, uniform nonzero omission cases, churn counts, query-source
   provenance, and explicit gold labels.
2. Preserve existing public baseline `advance` behavior; add a diagnostic
   result path for CRM-based ablations so the runner no longer discards their
   recomposition result.
3. Maintain a content-free measurement ledger from already-seen deltas. Keep
   it outside arm state and prove it is never passed into advancement or
   projection decisions.
4. Emit only numbers, booleans, enums, and one-way coverage digests in
   learning evidence. Do not retain released text, old Capsule bodies, raw
   deltas, query text, or gold in runtime evidence.
5. Materialize inputs again, run smoke and full protocol, then verify fixed
   denominator, invariant fields, no overshoot, deterministic state hashes,
   and no content leakage.

## Acceptance

```powershell
uv run pytest tests/test_protocol.py tests/test_runner.py tests/test_scoring.py -q
uv run python -m crm_experiment.cli materialize --config config/protocol-v1.json --output data
uv run python -m crm_experiment.cli run --config config/protocol-v1.json --runtime data/runtime/protocol.json --queries data/public-queries/queries.json --output results
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv lock --check
uv run python -m crm_experiment.evidence --repo G:\AstrContinuum --manifest evidence\protected-source-hashes.json --verify
uv run pre-commit run --all-files
```

Expected: every metric value is either proven or explicitly unavailable; all
partition and weight invariants hold; the full protocol remains content-free,
phase-separated, fixed-denominator, deterministic, and budget-safe.

## Review Fix Gate

Independent red-team review found three correctness gaps and two evidence
efficiency gaps. They are part of this task's acceptance, not deferred work.

1. Record `transition_committed` and `consumed_delta`. For an exception, both
   are false. For a CRM result, use the actual `consumed_delta`. Generic step
   compression/release fields are nullable and must be null whenever the
   transition was not committed or the delta was not consumed. Separately
   record rejected-delta bytes and any committed base-state reduction; never
   label rejected input as released cache content.
2. Measurement latest-resolution must use the same `(revision, atom_id)`
   winner rule as `atomize`. Cover inverse `as_of`/revision order, retraction,
   same-round duplicates, and multi-key partial supersession.
3. Replace self-declared query provenance with a `FrozenStateView` and mutable
   read trace. Projection receives only that view; legal reads increment the
   actual frozen source, forbidden-read attempts increment the trace and fail
   the query. `fallback_reads` is derived from the trace, never a literal.
4. Normalize evidence: checkpoint metrics and coverage digests live once in
   the content-free checkpoint sidecar. Query records carry a stable
   `checkpoint_id` plus query-only fields. Task 9 must join explicitly. Domain
   separate ID digests with the runtime/source manifest hash and never call
   them anonymization.
5. Zero-delta rows carry the initial hash, all round hashes, final hash, and
   first unstable round (if any), so all 16 rounds can be audited rather than
   trusting one boolean.

Add failure, below-K admission-block, resolver parity, multi-key, retraction,
normal/missing/exception/forbidden query-read, normalized-sidecar, and
zero-delta-chain tests before re-running the full acceptance sequence.

## Final Review Fix Gate

Sol final review added two fail-closed postconditions before acceptance:

1. A projection is rejected as `FORBIDDEN_QUERY_READ` whenever the completed
   `QueryReadTrace` contains any forbidden read, even if projection code caught
   the original exception and returned a supported answer.
2. `committed_base_state_reduction_bytes` is only available for a committed
   transition that did not consume delta. A consumed transition cannot
   attribute the net state-size change to old base bytes alone and records
   JSON null instead.

Both defects were captured as failing regression tests before the minimal
runner fix. The final focused suite contains 54 passing tests; the full suite
contains 139 passing and one skipped test.

## Return

Return RED/GREEN evidence, changed files, full-run counts, invariant audit,
exact gate outputs, and blockers. Do not commit.
