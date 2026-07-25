# AstrContinuum v1.0.0

## Goal

Deliver an installable AstrBot Context Runtime and Context Compiler that keeps very long
conversations usable, recoverable, source-verifiable, and within a fixed model budget.
The release target is v1.0.0, not a public 0.x preview.

## Scope

Included: immutable Journal, structured multi-resolution Capsules, committed Snapshots,
raw Delta, query-aware reconstruction, hard token budgeting, exact anchors, durable
background compaction, loss audit, reversible AstrBot projection, Inspector commands,
crash recovery, and release verification.

Standalone operation is mandatory. When Sylanne is installed, AstrContinuum composes
with its existing one-time injection but does not call recall, access private memory, or
persist injected content.

Excluded: replacing AstrBot's conversation database, deleting raw history, building a
second long-term personal-memory system, modifying Sylanne, or presenting a narrative
summary as the durable context model.

## Direction

The request lane reads only a committed Snapshot plus raw uncovered Delta and performs
bounded local assembly. Compression runs behind a durable SQLite lease and publishes
Capsules and a Snapshot through a fenced pointer CAS.

Provider history is projected only inside the agent run and restored before AstrBot
saves native history. With Sylanne installed, restoration is nested in LIFO order and
AstrContinuum budgets after Sylanne injection using ephemeral opaque token accounting.

## Risk Gate

Stop before any production release, remote write, destructive history operation, or
database migration against user data. Projection remains disabled for unverified AstrBot
runners or versions. No public v1.0.0 metadata is set until the full compatibility,
crash-recovery, privacy, million-token, and packaging gates pass.

## Done

The main request never waits for compaction; coverage has no gaps; critical anchors and
sources are complete; stale workers cannot publish; projected bytes never enter native
history; standalone and Sylanne-enhanced suites pass; AstrBot 4.24.2, 4.24.5, and 4.26.7
are verified; and all release artifacts consistently identify v1.0.0.
