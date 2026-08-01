"""Cross-generation latest-resolution tests for CRM v2."""

from __future__ import annotations

from dataclasses import replace

import pytest

from crm_experiment.contracts import AtomRole, AtomStatus
from crm_experiment.contracts_v2 import (
    ActiveRecordV2,
    CapsuleStateV2,
    DeltaEnvelopeV2,
    DirectBlockV2,
    LogicalAtomV2,
    SourceWeightV2,
    WeightPolicyV2,
)
from crm_experiment.logical_v2 import (
    active_weight_v2,
    decode_logical_state_v2,
    resolve_latest_v2,
)


def _atom(
    label: str,
    keys: tuple[str, ...],
    *,
    revision: int = 1,
    as_of: int = 1,
    status: AtomStatus = AtomStatus.ACTIVE,
    text: str | None = None,
    depends_on: tuple[str, ...] = (),
) -> LogicalAtomV2:
    return LogicalAtomV2.create(
        source_label=label,
        semantic_keys=keys,
        role=AtomRole.CONTEXT,
        text=text or label,
        status=status,
        revision=revision,
        as_of=as_of,
        provenance=("fixture",),
        exact=False,
        depends_on=depends_on,
        core_required=False,
    )


def _initial(*atoms: LogicalAtomV2, key_limit: int = 64):
    return resolve_latest_v2(
        None,
        DeltaEnvelopeV2(0, 1, tuple(sorted(atoms, key=lambda atom: atom.source_id))),
        key_registry_limit=key_limit,
        max_semantic_key_bytes=64,
    )


def _state(resolution, *, generation: int = 1) -> CapsuleStateV2:
    active_source_ids = sorted(
        {
            winner.source_id
            for winner in resolution.frontier
            if winner.status is AtomStatus.ACTIVE
        }
    )
    return CapsuleStateV2(
        schema_version=2,
        generation=generation,
        high_water=resolution.high_water,
        accepted_budget=8192,
        key_registry_limit=64,
        max_semantic_key_bytes=64,
        frontier=resolution.frontier,
        receipts=resolution.receipts,
        weight_policy=WeightPolicyV2.create(
            version="theta0",
            source_weights=tuple(
                SourceWeightV2(source_id, 1.0) for source_id in active_source_ids
            ),
        ),
        kernel=(),
        body=tuple(
            DirectBlockV2(record)
            for record in sorted(
                resolution.active_records,
                key=lambda record: record.atom.source_id,
            )
        ),
    )


def _advance(state: CapsuleStateV2, *atoms: LogicalAtomV2):
    target = state.high_water + 1
    return resolve_latest_v2(
        state,
        DeltaEnvelopeV2(
            state.high_water,
            target,
            tuple(sorted(atoms, key=lambda atom: atom.source_id)),
        ),
    )


def _by_label(records: tuple[ActiveRecordV2, ...], text: str) -> ActiveRecordV2:
    return next(record for record in records if record.atom.text == text)


def test_one_key_update_preserves_unrelated_physical_sibling() -> None:
    topic_a = _atom("topic-a-v1", ("topic-a",))
    topic_b = _atom("topic-b-v1", ("topic-b",))
    base = _state(_initial(topic_a, topic_b))
    update = _atom("topic-a-v2", ("topic-a",), revision=2, as_of=2)

    resolved = _advance(base, update)

    assert {record.atom.text for record in resolved.active_records} == {
        "topic-a-v2",
        "topic-b-v1",
    }


def test_partial_supersession_never_rewrites_the_original_source_record() -> None:
    shared = _atom("shared-v1", ("alpha", "beta"))
    base = _state(_initial(shared))
    alpha_v2 = _atom("alpha-v2", ("alpha",), revision=2, as_of=2)

    resolved = _advance(base, alpha_v2)
    residual = _by_label(resolved.active_records, "shared-v1")

    assert residual.atom is shared
    assert residual.atom.semantic_keys == ("alpha", "beta")
    assert residual.active_keys == ("beta",)


def test_two_generation_partial_supersession_and_exact_replay_are_stable() -> None:
    shared = _atom("shared-v1", ("alpha", "beta"))
    first = _state(_initial(shared))
    alpha_v2 = _atom("alpha-v2", ("alpha",), revision=2, as_of=2)
    second_resolution = _advance(first, alpha_v2)
    second = _state(second_resolution, generation=2)

    replay = resolve_latest_v2(
        second,
        DeltaEnvelopeV2(second.high_water, second.high_water, (shared,)),
    )

    assert replay == second_resolution
    assert replay.high_water == second.high_water


def test_fully_superseded_source_exact_replay_is_a_noop() -> None:
    old = _atom("topic-old", ("topic",), revision=1)
    first = _state(_initial(old))
    new = _atom("topic-new", ("topic",), revision=2, as_of=2)
    second_resolution = _advance(first, new)
    second = _state(second_resolution, generation=2)

    replay = resolve_latest_v2(
        second,
        DeltaEnvelopeV2(second.high_water, second.high_water, (old,)),
    )

    assert replay == second_resolution


def test_retraction_tombstone_blocks_lower_rank_revival_across_generations() -> None:
    current = _atom("topic-v1", ("topic",), revision=1)
    first = _state(_initial(current))
    retracted = _atom(
        "topic-v2-retracted",
        ("topic",),
        revision=2,
        as_of=2,
        status=AtomStatus.RETRACTED,
    )
    second_resolution = _advance(first, retracted)
    second = _state(second_resolution, generation=2)
    stale_new_arrival = _atom(
        "topic-stale-arrival",
        ("topic",),
        revision=1,
        as_of=3,
    )

    third = _advance(second, stale_new_arrival)

    assert third.active_records == ()
    assert third.frontier == second.frontier
    assert third.high_water == 3


def test_higher_rank_can_reactivate_after_tombstone() -> None:
    current = _atom("topic-v1", ("topic",), revision=1)
    first = _state(_initial(current))
    retracted = _atom(
        "topic-v2-retracted",
        ("topic",),
        revision=2,
        as_of=2,
        status=AtomStatus.RETRACTED,
    )
    second = _state(_advance(first, retracted), generation=2)
    reactivated = _atom("topic-v3", ("topic",), revision=3, as_of=3)

    third = _advance(second, reactivated)

    assert tuple(record.atom for record in third.active_records) == (reactivated,)
    assert third.frontier[0].source_id == reactivated.source_id


def test_released_active_payload_keeps_dominance_and_replay_does_not_rehydrate() -> (
    None
):
    current = _atom("topic-v3", ("topic",), revision=3)
    initial = _initial(current)
    released = CapsuleStateV2(
        schema_version=2,
        generation=1,
        high_water=1,
        accepted_budget=8192,
        key_registry_limit=64,
        max_semantic_key_bytes=64,
        frontier=initial.frontier,
        receipts=initial.receipts,
        weight_policy=WeightPolicyV2.create(
            version="theta0",
            source_weights=(SourceWeightV2(current.source_id, 1.0),),
        ),
        kernel=(),
        body=(),
    )
    stale = _atom("stale-v2", ("topic",), revision=2, as_of=2)

    after_stale = _advance(released, stale)
    replay = resolve_latest_v2(
        released,
        DeltaEnvelopeV2(1, 1, (current,)),
    )

    assert after_stale.active_records == ()
    assert after_stale.frontier == initial.frontier
    assert replay.active_records == ()
    assert replay.frontier == initial.frontier
    assert replay.high_water == 1


def test_unknown_content_addressed_historical_record_is_an_inert_noop() -> None:
    current = _atom("topic-v1", ("topic",))
    base = _state(_initial(current))
    unknown = _atom("unknown", ("other",), as_of=1)

    replay = resolve_latest_v2(base, DeltaEnvelopeV2(1, 1, (unknown,)))

    assert replay.frontier == base.frontier
    assert replay.high_water == base.high_water
    assert base.high_water == 1
    assert base.frontier[0].source_id == current.source_id


def test_source_id_conflict_fails_at_record_construction_after_source_is_gone() -> None:
    original = _atom("stable", ("topic",))

    with pytest.raises(ValueError, match="source_id"):
        replace(original, text="different payload")


def test_same_revision_uses_content_bound_source_id_tie_break_in_both_orders() -> None:
    left = _atom("left", ("topic",), revision=3)
    right = _atom("right", ("topic",), revision=3)
    expected = max((left, right), key=lambda atom: atom.source_id)

    forward = _initial(left, right)
    reverse = resolve_latest_v2(
        None,
        DeltaEnvelopeV2(0, 1, tuple(sorted((right, left), key=lambda a: a.source_id))),
        key_registry_limit=64,
        max_semantic_key_bytes=64,
    )

    assert tuple(record.atom for record in forward.active_records) == (expected,)
    assert reverse == forward


def test_resolver_fails_closed_on_dangling_active_dependency() -> None:
    dependency = _atom("b-old", ("b",), revision=1)
    dependent = _atom(
        "a",
        ("a",),
        revision=1,
        depends_on=(dependency.source_id,),
    )
    base = _state(_initial(dependent, dependency))
    replacement = _atom("b-new", ("b",), revision=2, as_of=2)

    with pytest.raises(ValueError, match="dependency"):
        _advance(base, replacement)


def test_active_dependency_can_be_logically_present_with_payload_released() -> None:
    dependency = _atom("dependency", ("dependency",))
    dependent = _atom(
        "dependent",
        ("dependent",),
        depends_on=(dependency.source_id,),
    )
    initial = _initial(dependency, dependent)
    full = _state(initial)
    released_dependency = replace(
        full,
        body=tuple(
            block
            for block in full.body
            if block.record.atom.source_id != dependency.source_id
        ),
    )
    unrelated = _atom("unrelated", ("unrelated",), as_of=2)

    advanced = _advance(released_dependency, unrelated)

    assert {record.atom.source_id for record in advanced.active_records} == {
        dependent.source_id,
        unrelated.source_id,
    }


def test_released_dependent_receipt_still_enforces_logical_dependency() -> None:
    dependency = _atom("dependency", ("dependency",))
    dependent = _atom(
        "dependent",
        ("dependent",),
        depends_on=(dependency.source_id,),
    )
    released = replace(_state(_initial(dependency, dependent)), body=())
    replacement = _atom("replacement", ("dependency",), revision=2, as_of=2)

    with pytest.raises(ValueError, match="dependency"):
        _advance(released, replacement)


def test_frontier_limit_fails_closed_without_silent_tombstone_eviction() -> None:
    first = _atom("first", ("first",))
    second = _atom("second", ("second",))

    with pytest.raises(ValueError, match="key_registry_limit"):
        _initial(first, second, key_limit=1)


def test_active_weight_and_decoder_use_resident_source_records_once() -> None:
    shared = _atom("shared", ("a", "b"))
    resolution = _initial(shared)
    state = _state(resolution)

    assert active_weight_v2(state) == 1.0
    assert decode_logical_state_v2(state) == resolution.active_records
