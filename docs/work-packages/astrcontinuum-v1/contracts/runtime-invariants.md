# Runtime Invariants

## Request Availability

The request lane never waits for compiler, auditor, scheduler completion, remote model
work, or an unbounded scan. Short local capture/read/intent transactions are permitted.

If enhancement fails, AstrBot continues through its verified native path. Fail-open does
not mean pretending that Journal capture, coverage advance, or projection succeeded.

## Durable Source of Truth

Journal events are append-only. Compaction never updates or deletes them. Readers use
only the active committed Snapshot selected by the active pointer; worker-local candidates
are invisible.

For active coverage `C` and read high-water `H`:

```text
Dread = ordered { e | C < sequence(e) <= H }
[1, C] union sequence(Dread) = [1, H]
```

Emergency assembly may omit provider material but cannot alter Journal rows, active
pointer, or coverage.

## Compiler Completeness

For claimed target `T`:

```text
Djob = ordered { e | C < sequence(e) <= T }
len(Djob) = T - C
candidate.covered_event_end = candidate.source_high_water_mark = T
```

The candidate consumes the full committed base and the complete contiguous Delta. It
retains prior active semantics and required exact anchors.

## Mechanical Publication Gate

These checks are permanent:

```text
source_coverage = 1
anchor_recall = 1
coverage_gap = 0
unsupported_critical_claims = 0
```

`strict_audit=false` skips only optional semantic model review.

## Fencing and CAS

Every claim increments `lease_epoch`; every worker mutation matches owner and epoch.
Stale ownership changes zero rows. Successful active coverage strictly increases.

Candidate Capsules, Snapshot membership, and Snapshot insertion share one savepoint.
A same-prefix or pointer conflict rolls all candidate content back, then records the
fenced losing job as `SUPERSEDED` in the outer transaction.

## Privacy

Sylanne content is never a Journal event, Capsule source, Snapshot field, log field, crash
artifact, or stable content hash owned by AstrContinuum. AstrContinuum initiates zero
Sylanne recalls.
