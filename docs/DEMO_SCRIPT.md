# Demonstration scenarios — not release claims

This page is a future demonstration and acceptance outline. It does not prove that the listed
scenarios are already exposed by v0.3.0. In particular, standard background compaction does not
call a reorganization engine, there is no public rollback UI, and the Sylanne adapter is not an
active runtime integration.

## Million-token continuity

Seed a repository name, user prohibition, rejected decision, and unfinished task early in a
conversation. After several context windows, recover them correctly and show a Source Trace and
Token Map. This is a future evaluation scenario, not a current performance claim.

## A 30-second compiler does not block replies

Use a mock compiler provider with a 30-second delay. Continue sending messages while the bot
responds normally. The intended inspector view shows a committed Snapshot, a running job, and a
growing Delta before an atomic switch. This requires a controlled test harness.

## Kill the worker

Raise an exception after candidate generation and before commit. The expected invariant is that
the old Snapshot stays active, new requests use the old Snapshot plus Delta, and restart recovers
durable work. This describes a failure-injection test, not an end-user command.

## Decision reversal

Exercise a decision sequence such as “synchronous compaction → rejected → shadow compaction”.
The expected final state is that the superseded plan cannot become active. This remains a scenario
for a controlled test rather than a published time-travel interface.

## Sylanne coexistence

If a future verified Sylanne adapter is supplied, compare assembly traces before and after it to
prove no duplicate injection or privacy boundary breach; confirm that disabling Sylanne leaves the
standalone path functional. The current adapter boundary does not claim this integration is wired.
