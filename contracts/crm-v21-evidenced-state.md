# CRM V2.1 Evidenced Current-Support State Contract

Input control contract: `sha256:f999609df7402cffb084a996436c8faf82b95a97a831ea9c345ca8f8bd4c5849`.
Schema-3 guard input: V21-002 result
`sha256:907273309413b8c561e444b749715ffc6857fb32dea5f9f14938066635a24c31`.

## Purpose

`EvidencedCapsuleStateV21` is a bounded wrapper around one valid
`CapsuleStateV21`.  It introduces a stable, current contribution key so that a
single declared requirement can conservatively move from `EXACT` to
`CAPSULE_APPROX` to `RELEASED_MISS`.  It is a context-cache control plane, not
a Summary, source archive, migration path, or model interface.

The wrapper does not weaken `validate_reencoding_candidate_v21`.  In
particular, the existing V21-002 default guard continues to reject rootless
first folds, resident-body release, and barrier/ledger mutation without the
future evidence-gated reencoder path.

## Stable Current-Support Index

The stable contribution key is an existing `DictionaryEntryV21.key` in the
`v21-dictionary-id-s3` domain.  A key may occur at most once in the bounded
`contribution_index` and must still occur in the wrapped state's dictionary.

```text
ContributionLocatorV21(
    contribution_key,
    support = EXACT | SEGMENT,
    resident_id,
    support_commitment,
)
```

- `EXACT` is valid only when `resident_id` names one current exact/hot
  `ExactRecordV21` and `support_commitment` equals that record's verified
  source commitment.
- `SEGMENT` is valid only when `resident_id` names one current
  `CapsuleSegmentV21` and `support_commitment` equals its verified folded root.
- A locator never carries source text, a decision receipt, a parent id, or a
  historical root.  It cannot mint `EXACT` from a segment, dictionary,
  barrier, ledger, or digest.
- `DROP` is represented by removal of the current locator.  An absent key is
  intentionally a `RELEASED_MISS`; no tombstone/event log is retained.

The index is current-state control only.  It is not a release history: a
compaction replaces one segment locator in place, and a drop removes it.

## Bounded Accounting and Hashing

The index shares the frozen dictionary capacity rather than adding a new
unbounded collection:

```text
len(dictionary) + len(contribution_index) <= max_dictionary_entries
canonical_bytes(dictionary) + canonical_bytes(contribution_index)
    <= max_dictionary_bytes
```

For the wrapper's resident accounting, let `S0` replace every exact/hot body
in `state` with the empty string.

```text
C = utf8_len(canonical_json({state: S0, contribution_index}))
B = sum(utf8_len(record.body) for exact and hot records)
R = C + B
```

The complete wrapper hash is domain-separated and includes the complete state
and the canonical index.  Full source body bytes remain visible to that hash;
the index never contains a recoverable body.

## Query Semantics

`ContributionRequirementV21(contribution_key)` is nominal, valid only for the
dictionary-key domain, and evaluated by `query_evidenced_v21` over a nonempty
tuple.  Every locator is revalidated against the wrapped state at query time.

- any missing locator -> `RELEASED_MISS`;
- otherwise any `SEGMENT` locator -> `CAPSULE_APPROX`;
- otherwise all `EXACT` locators -> `EXACT`.

The function retains the existing strict priority and rejects malformed or
tampered requirements before emitting a label.  The old `QueryRequirementV21`
and `query_label_v21` remain unchanged.

## Deferred Evidence-Gated Reencoder

V21-004+ will add an independent `EvidencedReencodingCandidateV21`, complete
decision manifest, source-envelope root, first-fold authorizations,
parent-compaction witnesses, and previous-root rolling chains.  Those inputs
are transient and must not be persisted in this index.  That future verifier
may authorize `EXACT`, `SEGMENT`, or `DROP` only after it rechecks the V21-002
safety base and this wrapper's index invariants.

## Amendment 001 — Query Current-Head Binding

The current-support query must be anchored to a trusted external current-head
value.  Its required API is:

```text
query_evidenced_v21(wrapper, requirements, *, expected_state_hash: str)
```

`expected_state_hash` is a CAS, transition, or current-head input supplied by
the caller.  It must be a `crm-v21-evidenced-state-s3/v1` domain digest.  The
query validates the complete wrapper, validates that expected digest domain,
computes the wrapper's actual hash only for comparison, then uses a constant-
time comparison.  A mismatch raises `EvidencedStateMismatchV21` before the
query parses requirements or yields a label.

The query has no default, fallback, or internal fabrication of
`expected_state_hash`; it must never recompute the wrapper and pass that value
as its own trusted expectation.

This prevents coherent but unauthorized current-index rewrites from projecting
a new label: both an `EXACT` locator repointed to a different valid current
exact/hot record and an `EXACT` locator repointed to a valid current `SEGMENT`
must fail under the previously saved expected hash.
