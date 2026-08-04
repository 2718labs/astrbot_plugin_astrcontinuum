# v0.3 workflow and research path

English | [简体中文](WORKFLOW.zh-CN.md)

![v0.3 shipped workflow and CRM research target / v0.3 已交付工作流与 CRM 研究目标](assets/v030-workflow-rmb.svg)

## Legend

- **Solid ink green:** the publication chain implemented and bounded by current
  v0.3.0 contracts.
- **Dashed dark red:** the CRM research target. It has an isolated deterministic
  record, but is not wired into the standard CompactionWorker and is not a
  current runtime capability.
- **Warm gray:** ledger, quality, and failure boundaries. They constrain state
  transitions and cannot be bypassed by optional semantic-audit settings.

The academic palette uses dark red #A44742, ink green #1F6B5B, warm gray
#DDD6CC, and ink #263238. It uses no currency symbols, denominations, or price
imagery.

## Shipped v0.3.0 publication chain

1. Captured Journal / Delta work obtains a fenced job claim; the request path
   does not wait for compilation, audit, or a remote model.
2. The compiler works from a frozen high-water mark and produces candidate
   Capsules and a Snapshot.
3. Optional semantic audit decides only whether the candidate enters AUDITING;
   mechanical validation, the fence, and the permanent quality gate always run.
4. TX_PUBLISH_SNAPSHOT writes Capsules, Snapshot, membership, any explicitly
   supplied ledger records, and performs active-pointer CAS in one savepoint.
5. The candidate Snapshot is inserted as COMMITTED before CAS. It becomes
   active/read-visible only after CAS succeeds and the outer transaction
   commits. A CAS conflict rolls back candidate rows and returns SUPERSEDED; a
   ledger-integrity error propagates and rolls back the transaction without
   moving the active pointer.

The standard CompactionWorker currently does **not** call
reorganize_capsules(). Ordinary background publication passes an empty
reorganization-record tuple. The ledger in the diagram is an implemented
**explicit publication interface**, not a statement that automatic
reorganization is already wired.

## CRM dashed research target

The research path begins with an old Capsule and new Delta:

Atomizer → Candidate Composer → Matrix Builder (F/A/R/H/D) → budgeted CRM
Optimizer → loss-aware gate → atomic swap → query projection.

Every stage must preserve ID, state, time, polarity, and provenance. Controlled
release must pass the loss-aware gate. The target swap allows query projection
to read the new Capsule and releases consumed Delta / older material. Summary
may be used for offline comparison only; it is not a runtime main path or
degraded fallback.

This dashed path needs its own claim → compile → audit → publish/CAS worker
wiring, permanent validation, and release evidence before it can become a
production capability.

## Related pages

- [Evidence and provenance](EVIDENCE.md)
- [Evaluation scenarios, metrics, and thresholds](EVALUATION.md)
- [Normative data-flow details](DATA_FLOW.md)
- [Ledger publication contract](ADR-008-REORGANIZATION-LEDGER.md)
