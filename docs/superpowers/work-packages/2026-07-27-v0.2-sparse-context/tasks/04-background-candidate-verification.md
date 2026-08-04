# SC-04 Background Candidate Verification

Owner: background-verification-implementer
Depends on: SC-01R, SC-02

## Goal

Reject an unverified compiled candidate before `READY_TO_COMMIT` without affecting the active
Snapshot or live request lane.

This task consumes the already frozen `build.py`, `closure.py`, and `live.py` slice produced
during SC-03; it does not depend on the disjoint AstrBot adapter wiring finishing first.

## Write Scope

- `astrcontinuum/context_graph/candidate_verification.py`
- `astrcontinuum/compaction/worker.py`
- `tests/context_graph/test_candidate_verification.py`
- `tests/test_compaction_worker.py`

## Requirements

- Reuse SC-03 graph construction, closure, constraints, and the frozen numerical engine; do not
  introduce a second graph interpretation.
- Convert audited candidate memberships into bounded candidate blocks without a model call or
  narrative retelling.
- Run fixed content-free probe activations covering required goals/constraints/anchors/tasks,
  exact provenance, dependencies, and recent linkage.
- Verify numerical certificate, required inclusion, provenance, dependency closure, and probe
  equivalence.
- Insert the call strictly after `audit_semantic` and before transition to
  `READY_TO_COMMIT`.
- Failure raises one stable content-free exception code and follows the existing durable retry
  path. It must not publish, mutate the active pointer, or block live requests.
- Preserve lease fencing, heartbeat cleanup, cancellation precedence, storage-authentication
  escalation, compiler deferral, and retry limits.

## Acceptance

- Focused tests prove verified candidates publish and every injected graph/provenance/closure
  failure remains before `READY_TO_COMMIT`.
- Existing cancellation and storage-security worker tests remain green.
- Ruff, format, mypy, and `git diff --check` pass.

## Return

Return changed files, RED/GREEN evidence, stable codes, exact checks, conclusion, and blockers.
Do not commit.
