# CRM V2.1 Evidenced Reencoding Verifier

V21-005 is a pure, transition-scoped verifier over the frozen V21-003 wrapper
and V21-004 evidence roots. It does not plan, solve, persist, model, perform
I/O, retain history, or modify a production state.

The verifier accepts `base`, its trusted `current_head`, and an
`EvidencedReencodingCandidateV21`. Callers must provide these required
keyword-only anchors: `expected_base_state_hash`,
`expected_source_envelope_root`, `expected_policy_root`, and
`expected_advance_head_root`.

An accepted result is `VERIFIED`; stale external/current-head evidence is
`STALE_REJECTED`; malformed or unauthorized candidate evidence is
`ROLLED_BACK`. Every non-verified result returns the original `base` and
`current_head` objects, sets `consumed_delta=False`, and exposes no transition
root.

The implementation is introduced test-first. Its later checks proceed in the
frozen order: anchors and current cursor correspondence; source/policy; source
universe and stale barriers; decision closure; proposed wrapper and conversion;
rootless/parent authorization; barrier and ledger advances; final roots and
successor head. The matrix root is an opaque typed input reserved for V21-006.

## Public boundary

```text
verify_evidenced_reencoding_candidate_v21(
    base, current_head, candidate, *,
    expected_base_state_hash,
    expected_source_envelope_root,
    expected_policy_root,
    expected_advance_head_root,
) -> EvidencedReencodingResultV21
```

`EvidencedReencodingCandidateV21` carries the complete transient evidence:
the proposed wrapper, source envelope, policy, record/segment decisions,
first-fold authorizations, barrier advances, ledger advance, opaque matrix
root, decision/authorization/barrier/ledger roots, proposed hash, target
generation/high-water, and transition root. Its dataclass deliberately does
not validate on construction: the verifier is the sole admission boundary.

`EvidencedReencodingResultV21` contains only the accepted wrapper/current
advance head, status, `consumed_delta`, transition root, and a diagnostic
reason. It never returns an evidence history list.

## Frozen admission rules

1. Base and supplied current head are nominal/valid. Their state hashes,
   generation, durable barrier cursors, and ledger cursor exactly name the
   current wrapper. Each external anchor has its exact domain and is compared
   with `hmac.compare_digest`.
2. Envelope and policy roots are independently valid and externally anchored.
   The envelope base hash names the actual base; policy frame hash names the
   base frame. Matrix root is checked only as the reserved matrix digest domain.
3. The effective source universe is exactly every resident base body plus the
   declared incoming IDs, with same-ID incoming rows superseding base rows.
   Non-incoming rows must equal their base records byte-for-field. Incoming
   rows must be newer than base high-water, non-stale for the highest known
   namespace barrier/incarnation, and no newer than target high-water.
4. There is exactly one record decision per effective source and one segment
   decision per base segment. Both decision roots are recomputed. Core sources
   and every endpoint of the full hard graph are `EXACT`.
5. A transition advances generation exactly once and target high-water equals
   the maximum of base high-water and all effective source timestamps. Frozen
   bounds, dictionary, frontier, sparse policy and frame policy cannot change.
6. `EXACT` retains only the matching source body and produces an exact locator;
   `SEGMENT` removes that body and repoints exactly its decision keys to the
   named **new** child segment; `DROP` removes body and all such locators. No
   duplicate decision key may claim a current locator.
7. Retained base segments are byte-identical. A compacted parent disappears
   exactly once into its declared child, whose input root binds the full direct
   base-parent bytes. A rootless new child needs exactly one complete
   first-fold authorization matching its `SEGMENT` sources, namespace,
   incarnation and target generation.
8. Every non-exact source appears exactly once in a release barrier. Existing
   barriers consume their current head cursor. A new rootless first fold may
   use only a same-transition null seed bound to the actual base hash, current
   head root, and new namespace/incarnation when its target is the exactly-once
   authorized rootless-new child; its successor cursor is an
   `ADVANCE` cursor. The resulting durable barrier projection must be the
   proposed state value.
9. Ledger delta is derived only from `SEGMENT`/`DROP` record outcomes and
   `COMPACT`/`DROP` segment dispositions. Segment loss contributes the
   consumed segment's full role counts; `DROP` uses `drop_loss_units`.
   The result is checked against total and per-role policy caps, then matched
   to its advance and durable projection. Finally the verifier recomputes proposed and
   transition roots and returns a single successor head.

## Explicit exclusions

V21-005 does not construct or inspect H/M/F/G/P/X matrices, enumerate plans,
choose features/vectors, solve an objective, determine optimality, or apply a
tie-break. Those capabilities remain deferred to V21-006. No state writer,
CAS/history persistence, model, network, filesystem I/O, or source archive is
part of this verifier.
