# V21-008 preregistered V21-007 stress data

## Frozen arm

The only V21-008 workload uses protocol
`crm-v21-008-preregistered-stress-s3/v1`.  It contains exactly twelve rounds
with batch sizes `(1, 2, 3, 4)` repeated three times.  Incoming records have
distinct round/slot identities, ASCII bodies, and these exact per-record byte
sizes:

- rounds 1-4: `3072`;
- rounds 5-8: `4096`;
- rounds 9-12: `5120`.

Each source universe contains one core exact source plus that round's incoming
batch, so it has at most five source rows.  H is empty.  The initial dictionary
pre-registers the core key and all thirty incoming keys.  Its shared
dictionary-plus-locator bound is at least `62`, and the segment bound is at
least `12`.

The hot frontier remains empty (`max_hot_frontier_entries=0`), but its frozen
byte bound is `2`: the canonical encoding of the empty tuple is two bytes.

The only policy is `DETERMINISTIC_GREEDY` with `max_new_segments=1`,
`max_plan_evaluations=16`, `max_loss_units=4`, and `segment_loss_units=1`.
The loss policy admits the thirty cumulative context contributions.

### Measurement feasibility amendment

Before any V21-007 measurement artifact was produced, the observed verifier
semantics established that `segment_loss_units=1` is charged once per source in
the round.  The frozen single-round loss cap is therefore `4`: every accepted
trace has `loss_delta=batch_size`, and `cumulative_loss` is the running sum of
the twelve batch sizes.  This amendment changes neither the batch schedule nor
the segment-loss unit, and no pre-amendment measurement artifact exists.

## Execution boundary

Setup may call V21-006 only to materialize the twelve concrete chained
`RoundInputV21` values.  Measurement invokes V21-007 from the original state
and head.  A second identical V21-007 run is used only as a canonical-byte
replay integrity gate.  No setup candidate, matrix plan, body, envelope, full
state, or full head is retained in the report.

Every accepted trace must be `COMPLETED`, release exactly its batch size, and
have explicit `loss_delta=batch_size`.  The report additionally checks C+B=R, roots,
generation chaining, and high-water chaining for all twelve compact records.

## Body-free canonical data

`run_preregistered_stress_v21`,
`validate_preregistered_stress_data_v21`, and
`canonical_preregistered_stress_json_v21` are public pure boundaries.
`python -m crm_experiment.stress_data_v21` writes only the canonical JSON plus
one trailing newline to stdout.

The canonical top-level schema contains only fixed schema/protocol/workload,
one first-class `workload_root`, the V21-007 evaluator run root, replay result,
terminal fields, `data_root`, and exactly twelve compact records.  A record
retains only raw C/B/R fields and deltas, incoming count/body bytes, resident
growth, avoided incremental bytes, integer ppm fields, explicit/cumulative
loss, roots, generation, high-water, and release count.

`workload_root` is body-free and independently recomputable with domain
`crm-v21-008-workload-root-s3/v1`: it hashes canonical
`{"protocol_id": protocol_id, "workload": workload}` under that domain.  It
is validated before `data_root`, and `data_root` binds it.  Public parsing and
validation fail closed if either the workload or its root is tampered.

With `P=1_000_000`, every record uses these integer-only definitions:

- `unfolded = before_resident_bytes + incoming_body_bytes`;
- `avoided_incremental_bytes = unfolded - resident_bytes`;
- `retention_ppm = resident_bytes * P // unfolded`;
- `reduction_ppm = avoided_incremental_bytes * P // unfolded`;
- `release_ppm = release_count * P // incoming_count`;
- `loss_per_avoided_ppm = loss_delta * P // avoided_incremental_bytes`.

All denominators and avoided bytes must be positive.  Validation fails closed
for noncanonical JSON, malformed schema, incomplete/failed terminal status,
broken chains, invalid arithmetic, or a mismatched data root.

The evidence artifact is captured only after Green as
`evidence/v21-008-stress-data.json`; it is canonical data, never pytest output.
