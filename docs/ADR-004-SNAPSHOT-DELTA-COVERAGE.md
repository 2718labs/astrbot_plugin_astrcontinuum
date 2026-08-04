# ADR-004: Snapshot, Delta, and Coverage Semantics

- Status: Accepted for Phase 0
- Scope: request assembly and compaction inputs
- Update: v0.3.0 的发布写入顺序、账本可见性和冲突分类由 ADR-008 局部修订；本 ADR 的其余覆盖语义保持有效。

## Definitions

For one `session_key_hash`:

- `H` is the `TX_READ_REQUEST_VIEW` transaction high-water mark: the greatest committed `journal_events.sequence`, or `0` when no event exists.
- `S` is the committed active Snapshot selected through `active_snapshots`, or the logical `EMPTY_BASE` before the first Snapshot is published.
- `EMPTY_BASE` is not a Snapshot Schema envelope or database row, has no `snapshot_id` or active pointer, and defines coverage end `0` only for bootstrap assembly.
- `C` is `S.covered_event_end` for a committed Snapshot and `0` for `EMPTY_BASE`.
- request Delta `Dread` is the ordered set of committed Journal events with `C < sequence <= H`.
- a compaction target `T` is the claimed job's immutable `target_high_water_mark` for that attempt.
- compaction Delta `Djob` is the ordered contiguous set with `C < sequence <= T`.

A Snapshot covers the contiguous Journal prefix `[1, covered_event_end]`. Coverage is a storage and recoverability guarantee. It MUST NOT be interpreted as a guarantee that the rendered form of every covered raw token appears in every prompt.

## Request View

`TX_READ_REQUEST_VIEW` MUST read the active pointer, its Snapshot, and `H` from one database read transaction. With an active pointer, the returned view MUST contain exactly that one committed active Snapshot plus `Dread`. Without an active pointer, it MUST contain `EMPTY_BASE` plus events `1..H`. It MUST NOT fabricate a persisted Snapshot, include a candidate Snapshot, include an event above `H`, or include an event already covered by `S`.

`EMERGENCY_ASSEMBLY` MAY trim, summarize, or omit prompt material to satisfy a prompt budget. It MUST NOT delete or mutate `journal_events`, change `active_snapshots`, update `covered_event_end`, or represent omitted material as newly covered.

## Candidate Construction

A compaction candidate MUST consume its complete base input (`S` is the previous committed Snapshot, or `EMPTY_BASE` for bootstrap) and every event in the contiguous `Djob`. The worker MUST verify:

- `T > C` and `T <=` the committed Journal high-water mark observed for the job, so active coverage strictly advances;
- the first Delta sequence is `C + 1`, the last is `T`, and adjacent sequences differ by exactly one;
- `base_snapshot_id` equals the active Snapshot used as compiler input with `base_pointer_version >= 1`, or is null only for `EMPTY_BASE` bootstrap with `base_pointer_version=0`;
- candidate `covered_event_end` and `source_high_water_mark` both equal `T`;
- prior active semantics and required exact anchors are present;
- output is structurally valid, identity-consistent, mechanically valid, and non-empty.

A candidate that drops prior active semantics, skips a Journal event, covers beyond `T`, uses a non-active base, or reports a non-contiguous prefix MUST NOT publish. These checks are mechanical and MUST run when `strict_audit=false`.

## Publish

The wire Snapshot `state` is `CANDIDATE` or `COMMITTED`. Candidate content remains worker-local while the job is `READY_TO_COMMIT`; it is Schema-validated as `state=CANDIDATE` but is not inserted into the COMMITTED-only `snapshots` table. `candidate_snapshot_id` is a preallocated opaque id, not reader visibility. `TX_PUBLISH_SNAPSHOT` MUST, in one outer transaction:

1. revalidate the lease owner and fencing `lease_epoch`;
2. open a savepoint (or use equivalent statement-level rollback semantics) and insert the immutable Snapshot row as `COMMITTED`;
3. CAS `active_snapshots` while requiring new `covered_event_end` to be strictly greater than active coverage: for bootstrap (`base_snapshot_id=null` and `base_pointer_version=0`), conditionally create the absent pointer row at version `1`; for an existing base, conditionally update the row matching both expected `snapshot_id` and `pointer_version >= 1`, then increment the version;
4. on CAS success, set the job to `COMMITTED`, clear its active lease, release the savepoint, and commit;
5. if Snapshot insert hits `UNIQUE(session_key_hash, covered_event_end)` or the pointer CAS conflicts, treat either as the same publish-conflict path: roll back only to the savepoint so the unpublished insert is removed, retain `candidate_snapshot_id`, set the job to `SUPERSEDED`, and clear its active lease;
6. before either branch commits, compare durable `intent_target_high_water_mark` with the winning active Snapshot coverage. If intent is greater, atomically leave or create `PENDING` follow-up work based on the winning `snapshot_id` and pointer version; then commit the outer transaction.

Readers cannot observe the inserted row through `active_snapshots` unless the success branch commits. Pointer coverage MUST strictly increase on every successful publish. A same-prefix Snapshot uniqueness conflict, bootstrap pointer race, or update conflict is an expected isolated publish conflict; it MUST NOT escape as an unhandled worker exception, roll back the durable `SUPERSEDED` transition, overwrite the winning pointer, or discard a higher durable intent.

A `COMMITTED` Snapshot MUST have `audit_outcome.mechanical_passed=true`, `semantic_status` equal to `NOT_RUN` or `PASSED`, and an empty `failure_codes` array. The permanent validator MUST enforce `covered_event_end = source_high_water_mark = compaction_jobs.target_high_water_mark` at publish time; standard JSON Schema does not express this cross-record equality.

Events appended after `T` remain Journal Delta for the next request and compaction. A trigger during an active job MUST durably raise `intent_target_high_water_mark`; both `COMMITTED` and `SUPERSEDED` completion paths MUST leave or create `PENDING` follow-up work when that intent is greater than the winning active coverage end.

## Examples

With active coverage `C=40`, read high-water `H=44`, and job target `T=43`, the request view is Snapshot `[1,40]` plus events `41..44`; the candidate consumes the Snapshot plus events `41..43`; event `44` remains uncovered after publish. A Delta containing `41,43` MUST fail before publish.
