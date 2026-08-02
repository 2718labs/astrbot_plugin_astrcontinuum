# V21-003 Evidenced Current-Support State

Owner: one Terra-max implementation writer
Depends on: V21-001 control contract and accepted V21-002 schema guard

Contract inputs:

- `sha256:f999609df7402cffb084a996436c8faf82b95a97a831ea9c345ca8f8bd4c5849`
- `sha256:907273309413b8c561e444b749715ffc6857fb32dea5f9f14938066635a24c31`

## Goal

Create the smallest bounded `EvidencedCapsuleStateV21` wrapper and stable
current-support query layer.  One existing dictionary key must remain the same
requirement identity while its support moves `EXACT -> CAPSULE_APPROX ->
RELEASED_MISS`; a missing locator is an explicit miss, not silent recovery.

## Exact Write Scope

- `contracts/crm-v21-evidenced-state.md`
- `src/crm_experiment/evidenced_state_v21.py`
- `tests/test_evidenced_state_v21.py`
- `tasks/v21-003-evidenced-state.md`

## Required Semantics

- Wrap, never mutate or relax, a valid `CapsuleStateV21`.
- Reuse only current `DictionaryEntryV21.key` values as stable contribution
  keys.  Enforce canonical ordering and uniqueness.
- `EXACT` locator binds a current exact/hot record id and its source
  commitment.  `SEGMENT` locator binds a current segment id and its folded
  root.  No other control object can satisfy a locator.
- The index is current-state control only: no source body, decision manifest,
  envelope, parent history, root history, receipt, or event log.
- Its entry and byte counts share the existing dictionary capacity.  Wrapper
  `C/B/R` accounting and hash include the index while keeping exact/hot source
  bodies separate exactly once.
- `query_evidenced_v21` revalidates nominal requirements and locators on every
  call and uses `RELEASED_MISS > CAPSULE_APPROX > EXACT` priority.
- Leave `contracts_v21.py`, `resident_v21.py`, V21-002 tests, and all existing
  V21-002 functions unchanged.

## TDD Order

1. RED: dangling/foreign/duplicate/unsorted key or locator; a segment/digest
   attempting `EXACT`; shared dictionary capacity breach; tampered query
   requirement.
2. RED: the same `ContributionRequirementV21` over three valid wrappers yields
   `EXACT`, then `CAPSULE_APPROX`, then `RELEASED_MISS` without changing its
   key.
3. Implement nominal types, deterministic validation, C/B/R layout and
   domain-separated wrapper hash.
4. GREEN: focused tests plus static checks.

## Acceptance

- A locator's support commitment is rechecked against the current wrapped
  object, not trusted from construction.
- Every invalid wrapper/query fails closed; no existing V21 state is changed.
- `C/B/R` for the wrapper is deterministic and capacity admission uses the
  same `R` metric.
- Required command:
  `uv run pytest tests/test_evidenced_state_v21.py tests/test_contracts_v21.py tests/test_resident_v21.py -q -p no:cacheprovider`.
- Report RED, GREEN, Ruff, Pyright, changed paths, and remaining V21-004
  boundary.

## Explicitly Deferred to V21-004+

- Decision matrix and `EXACT|SEGMENT|DROP` planner.
- Source-envelope evidence, first-fold authorization, parent-compaction
  witnesses, barrier/ledger previous-root chains, root updates, and release.
- Codec/decoder, V2 migration, protocol/runner/config/data/results/figures,
  post-learning, Summary runtime, model/API/network or production changes.

## Temporary Root

`D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\.git\codex-tmp\v21-evidenced-state`

Set `CODEX_TASK_TEMP`, `TEMP`, `TMP`, `TMPDIR`, and
`PYTHONPYCACHEPREFIX` under that root before commands that create temporary
artifacts.

## Amendment 001 — Sol P1 Current-Head Query Binding

This append-only, final-review-authorized remediation changes only the query
boundary.  `query_evidenced_v21` now requires the keyword-only external input
`expected_state_hash: str`; there is no default or fallback.  The caller must
pass a trusted CAS, transition, or current-head value in the existing
`crm-v21-evidenced-state-s3/v1` domain.  The query validates the complete
wrapper, validates the expected hash domain, compares the actual wrapper hash
with `hmac.compare_digest`, and raises `EvidencedStateMismatchV21` before it
projects requirements or returns a label.

Acceptance additions:

- Missing `expected_state_hash` raises `TypeError`; malformed or foreign hash
  domains fail before malformed requirements are projected.
- A coherent `EXACT` locator repoint to another current exact/hot record, and
  a coherent `EXACT` to current `SEGMENT` repoint, both fail under the old
  saved hash.
- Untampered wrappers queried with a separately saved external hash retain the
  existing `EXACT`, `CAPSULE_APPROX`, and `RELEASED_MISS` labels.

This does not authorize V21-004 planning, evidence envelopes, history chains,
or reencoding transitions.
