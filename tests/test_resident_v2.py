"""Exact canonical resident-byte tests for Capsule state v2."""

from __future__ import annotations

from dataclasses import replace

import pytest

from crm_experiment.canonical import canonical_json, utf8_bytes
from crm_experiment.contracts import AtomRole, AtomStatus
from crm_experiment.contracts_v2 import (
    ActiveRecordV2,
    CapsuleStateV2,
    DirectBlockV2,
    KeyWinnerV2,
    LogicalAtomV2,
    PackingPolicyV2,
    SourceReceiptV2,
    SourceWeightV2,
    WeightPolicyV2,
)
from crm_experiment.resident_v2 import (
    logical_semantic_hash_v2,
    resident_breakdown_v2,
    resident_bytes_v2,
    resident_hash_v2,
)


def _atom(label: str, text: str, *, core: bool = False) -> LogicalAtomV2:
    return LogicalAtomV2.create(
        source_label=label,
        semantic_keys=(label,),
        role=AtomRole.ROOT_GOAL if core else AtomRole.CONTEXT,
        text=text,
        status=AtomStatus.ACTIVE,
        revision=1,
        as_of=12,
        provenance=(f"provenance-{label}",),
        exact=False,
        depends_on=(),
        core_required=core,
    )


def _record(atom: LogicalAtomV2) -> ActiveRecordV2:
    return ActiveRecordV2(atom, atom.semantic_keys)


def _policy(
    frontier: tuple[KeyWinnerV2, ...],
    *,
    version: str = "theta0",
    weight: float = 1.0,
) -> WeightPolicyV2:
    source_ids = sorted(
        {winner.source_id for winner in frontier if winner.status is AtomStatus.ACTIVE}
    )
    return WeightPolicyV2.create(
        version=version,
        source_weights=tuple(
            SourceWeightV2(source_id, weight) for source_id in source_ids
        ),
    )


def _state(body_count: int = 2) -> CapsuleStateV2:
    goal = _atom("goal", '目标：保留\\"quoted\\" continuity', core=True)
    body_atoms = tuple(
        _atom(
            f"topic-{index}",
            ("多语言 context / café" if index == 0 else f"nul\u0000-tab\t-{index}"),
        )
        for index in range(body_count)
    )
    all_atoms = (goal,) + body_atoms
    frontier = tuple(
        sorted(
            (KeyWinnerV2.from_atom(atom, atom.semantic_keys[0]) for atom in all_atoms),
            key=lambda winner: winner.semantic_key,
        )
    )
    return CapsuleStateV2(
        schema_version=2,
        generation=12,
        high_water=12,
        accepted_budget=36864,
        key_registry_limit=64,
        max_semantic_key_bytes=64,
        frontier=frontier,
        receipts=tuple(
            sorted(
                (SourceReceiptV2.from_atom(atom) for atom in all_atoms),
                key=lambda receipt: receipt.source_id,
            )
        ),
        weight_policy=_policy(frontier),
        kernel=(_record(goal),),
        body=tuple(
            DirectBlockV2(_record(atom))
            for atom in sorted(body_atoms, key=lambda item: item.source_id)
        ),
    )


def test_exact_additive_breakdown_matches_full_canonical_state() -> None:
    state = _state()
    breakdown = resident_breakdown_v2(state)

    assert breakdown.persistent_bytes == utf8_bytes(canonical_json(state))
    assert breakdown.persistent_bytes == resident_bytes_v2(state)
    assert breakdown.persistent_bytes == (
        breakdown.fixed_bytes
        + sum(breakdown.body_item_bytes)
        + breakdown.body_separator_bytes
    )
    assert breakdown.body_separator_bytes == 1


@pytest.mark.parametrize("body_count", [0, 1, 2, 4])
def test_breakdown_is_exact_for_body_cardinality_and_control_text(
    body_count: int,
) -> None:
    state = _state(body_count)
    breakdown = resident_breakdown_v2(state)

    assert breakdown.persistent_bytes == utf8_bytes(canonical_json(state))
    assert breakdown.body_separator_bytes == max(0, body_count - 1)


def test_empty_body_has_no_hidden_separator_cost() -> None:
    state = _state(0)
    breakdown = resident_breakdown_v2(state)

    assert breakdown.body_item_bytes == ()
    assert breakdown.body_separator_bytes == 0
    assert breakdown.fixed_bytes == breakdown.persistent_bytes


def test_resident_and_logical_hashes_have_distinct_domains() -> None:
    state = _state()
    reframed = replace(state, generation=13, accepted_budget=18432)

    assert resident_hash_v2(state) != resident_hash_v2(reframed)
    assert logical_semantic_hash_v2(state) == logical_semantic_hash_v2(reframed)


def test_payload_release_changes_resident_hash_not_logical_frontier_hash() -> None:
    state = _state()
    released = replace(state, body=())

    assert resident_hash_v2(state) != resident_hash_v2(released)
    assert resident_bytes_v2(state) > resident_bytes_v2(released)
    assert logical_semantic_hash_v2(state) == logical_semantic_hash_v2(released)


def test_logical_hash_distinguishes_never_seen_from_retracted_empty() -> None:
    empty = CapsuleStateV2(
        schema_version=2,
        generation=1,
        high_water=1,
        accepted_budget=4608,
        key_registry_limit=64,
        max_semantic_key_bytes=64,
        frontier=(),
        receipts=(),
        weight_policy=WeightPolicyV2.create(version="theta0", source_weights=()),
        kernel=(),
        body=(),
    )
    retracted = LogicalAtomV2.create(
        source_label="topic-retracted",
        semantic_keys=("topic",),
        role=AtomRole.CONTEXT,
        text="withdraw topic",
        status=AtomStatus.RETRACTED,
        revision=2,
        as_of=1,
        provenance=("fixture",),
        exact=False,
        depends_on=(),
        core_required=False,
    )
    tombstoned = replace(
        empty,
        frontier=(KeyWinnerV2.from_atom(retracted, "topic"),),
        receipts=(SourceReceiptV2.from_atom(retracted),),
    )

    assert tombstoned.body == ()
    assert logical_semantic_hash_v2(empty) != logical_semantic_hash_v2(tombstoned)


def test_logical_hash_is_sensitive_to_winner_and_weight_policy() -> None:
    state = _state()
    old = state.frontier[-1]
    replacement = LogicalAtomV2.create(
        source_label="replacement",
        semantic_keys=(old.semantic_key,),
        role=AtomRole.CONTEXT,
        text="changed",
        status=AtomStatus.RETRACTED,
        revision=2,
        as_of=12,
        provenance=("fixture",),
        exact=False,
        depends_on=(),
        core_required=False,
    )
    changed_frontier = tuple(
        sorted(
            state.frontier[:-1]
            + (KeyWinnerV2.from_atom(replacement, old.semantic_key),),
            key=lambda winner: winner.semantic_key,
        )
    )
    changed_body = tuple(
        block
        for block in state.body
        if old.semantic_key not in block.record.active_keys
    )
    changed_receipts = tuple(
        sorted(
            tuple(
                receipt
                for receipt in state.receipts
                if receipt.source_id != old.source_id
            )
            + (SourceReceiptV2.from_atom(replacement),),
            key=lambda receipt: receipt.source_id,
        )
    )
    changed = replace(
        state,
        frontier=changed_frontier,
        receipts=changed_receipts,
        weight_policy=_policy(changed_frontier),
        body=changed_body,
    )

    assert logical_semantic_hash_v2(state) != logical_semantic_hash_v2(changed)
    assert logical_semantic_hash_v2(state) != logical_semantic_hash_v2(
        replace(
            state, weight_policy=_policy(state.frontier, version="theta1", weight=2.0)
        )
    )


def test_frontier_bytes_are_mandatory_resident_bytes() -> None:
    state = _state(1)
    released = replace(state, body=())
    kept_frontier = tuple(
        winner for winner in released.frontier if winner.core_required
    )
    kept_source_ids = {winner.source_id for winner in kept_frontier}
    without_noncore_frontier = replace(
        released,
        frontier=kept_frontier,
        receipts=tuple(
            receipt
            for receipt in released.receipts
            if receipt.source_id in kept_source_ids
        ),
        weight_policy=_policy(kept_frontier),
    )

    assert resident_bytes_v2(released) > resident_bytes_v2(without_noncore_frontier)


def test_text_bytes_are_not_a_proxy_for_resident_bytes() -> None:
    state = _state()
    text_bytes = sum(utf8_bytes(block.record.atom.text) for block in state.body) + sum(
        utf8_bytes(record.atom.text) for record in state.kernel
    )

    assert resident_bytes_v2(state) > text_bytes


def test_physical_packing_policy_changes_only_resident_identity() -> None:
    state = _state()
    source_ids = tuple(
        sorted(
            winner.source_id for winner in state.frontier if not winner.core_required
        )
    )
    changed = replace(
        state,
        packing_policy=PackingPolicyV2(
            codec="dmc1-lcp-lcs-v1",
            immutable_source_ids=source_ids,
            max_records_per_block=4,
            max_decoded_block_bytes=4096,
        ),
    )

    assert logical_semantic_hash_v2(changed) == logical_semantic_hash_v2(state)
    assert resident_hash_v2(changed) != resident_hash_v2(state)
    assert resident_bytes_v2(changed) != resident_bytes_v2(state)
