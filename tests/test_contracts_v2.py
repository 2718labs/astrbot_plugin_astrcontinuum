"""Contract tests for the self-contained CRM v2 logical/resident model."""

from __future__ import annotations

from dataclasses import fields, replace
from typing import Any

import pytest

from crm_experiment.contracts import AtomRole, AtomStatus
from crm_experiment.contracts_v2 import (
    ActiveRecordV2,
    CapsuleStateV2,
    DeltaEnvelopeV2,
    DirectBlockV2,
    KernelSchemaV2,
    KernelSlotV2,
    KeyWinnerV2,
    LogicalAtomV2,
    SourceReceiptV2,
    SourceWeightV2,
    WeightPolicyV2,
    source_record_hash_v2,
)


def _logical_atom(label: str = "source-a", **overrides: object) -> LogicalAtomV2:
    values: dict[str, object] = {
        "semantic_keys": ("topic-a",),
        "role": AtomRole.CONTEXT,
        "text": "context A",
        "status": AtomStatus.ACTIVE,
        "revision": 1,
        "as_of": 1,
        "provenance": ("fixture",),
        "exact": False,
        "depends_on": (),
        "core_required": False,
    }
    values.update(overrides)
    return LogicalAtomV2.create(source_label=label, **values)  # type: ignore[arg-type]


def _active(atom: LogicalAtomV2, *keys: str) -> ActiveRecordV2:
    return ActiveRecordV2(atom, keys or atom.semantic_keys)


def _winner(atom: LogicalAtomV2, key: str | None = None) -> KeyWinnerV2:
    return KeyWinnerV2.from_atom(atom, key or atom.semantic_keys[0])


def _state(
    *,
    frontier: tuple[KeyWinnerV2, ...],
    kernel: tuple[ActiveRecordV2, ...] = (),
    body: tuple[DirectBlockV2, ...] = (),
    source_atoms: tuple[LogicalAtomV2, ...] = (),
    high_water: int = 1,
    key_registry_limit: int = 64,
) -> CapsuleStateV2:
    record_atoms = tuple(record.atom for record in kernel) + tuple(
        block.record.atom for block in body
    )
    atom_by_source = {atom.source_id: atom for atom in record_atoms + source_atoms}
    frontier_source_ids = {winner.source_id for winner in frontier}
    receipts = tuple(
        sorted(
            (
                SourceReceiptV2.from_atom(atom_by_source[source_id])
                for source_id in frontier_source_ids
            ),
            key=lambda receipt: receipt.source_id,
        )
    )
    active_source_ids = sorted(
        {winner.source_id for winner in frontier if winner.status is AtomStatus.ACTIVE}
    )
    weight_policy = WeightPolicyV2.create(
        version="theta0",
        source_weights=tuple(
            SourceWeightV2(source_id, 1.0) for source_id in active_source_ids
        ),
    )
    return CapsuleStateV2(
        schema_version=2,
        generation=1,
        high_water=high_water,
        accepted_budget=4608,
        key_registry_limit=key_registry_limit,
        max_semantic_key_bytes=64,
        frontier=frontier,
        receipts=receipts,
        weight_policy=weight_policy,
        kernel=kernel,
        body=body,
    )


def test_v2_contracts_separate_source_identity_active_keys_and_layout() -> None:
    atom = _logical_atom()
    active = _active(atom)
    winner = _winner(atom)
    block = DirectBlockV2(active)
    state = _state(frontier=(winner,), body=(block,))

    assert tuple(field.name for field in fields(LogicalAtomV2)) == (
        "source_id",
        "semantic_keys",
        "role",
        "text",
        "status",
        "revision",
        "as_of",
        "provenance",
        "exact",
        "depends_on",
        "core_required",
    )
    assert atom.source_id == f"sha256:{source_record_hash_v2(atom)}"
    assert len(atom.source_id) == 71
    assert active.atom is atom
    assert active.active_keys == atom.semantic_keys
    assert block.record is active
    assert block.kind == "direct-v2"
    assert state.frontier == (winner,)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"semantic_keys": ()}, "semantic_keys"),
        ({"semantic_keys": ("",)}, "semantic_keys"),
        ({"semantic_keys": ("z", "a")}, "sorted"),
        ({"revision": 0}, "revision"),
        ({"as_of": -1}, "as_of"),
        ({"provenance": ()}, "provenance"),
        ({"provenance": ("",)}, "provenance"),
        ({"provenance": ("z", "a")}, "sorted"),
        ({"depends_on": ("z", "a")}, "sorted"),
        ({"depends_on": ("",)}, "depends_on"),
    ],
)
def test_logical_atom_rejects_noncanonical_or_invalid_metadata(
    overrides: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _logical_atom(**overrides)


def test_source_id_is_content_bound_without_a_seen_source_ledger() -> None:
    atom = _logical_atom()

    with pytest.raises(ValueError, match="source_id"):
        replace(atom, text="different payload")
    with pytest.raises(ValueError, match="source_id"):
        replace(atom, source_id="sha256:" + "0" * 64)

    changed = _logical_atom(text="different payload")
    assert changed.source_id != atom.source_id


def test_released_source_receipt_binds_control_header_to_source_id() -> None:
    core = _logical_atom(
        "goal",
        semantic_keys=("goal",),
        role=AtomRole.ROOT_GOAL,
        core_required=True,
    )
    receipt = SourceReceiptV2.from_atom(core)

    with pytest.raises(ValueError, match="source_id"):
        replace(receipt, core_required=False)
    with pytest.raises(ValueError, match="source_id"):
        replace(receipt, semantic_keys=("forged-key",))


def test_weight_policy_is_canonical_versioned_and_source_independent() -> None:
    atom = _logical_atom()
    theta0 = WeightPolicyV2.create(
        version="theta0",
        source_weights=(SourceWeightV2(atom.source_id, 1.0),),
    )
    theta1 = WeightPolicyV2.create(
        version="theta1",
        source_weights=(SourceWeightV2(atom.source_id, 2.0),),
    )

    assert theta0.policy_hash != theta1.policy_hash
    assert atom.source_id == theta1.source_weights[0].source_id
    with pytest.raises(ValueError, match="policy_hash"):
        replace(theta0, policy_hash="0" * 64)
    with pytest.raises(ValueError, match="weight"):
        SourceWeightV2(atom.source_id, float("inf"))

    assert source_record_hash_v2(atom) == atom.source_id.removeprefix("sha256:")


def test_active_record_preserves_original_keys_and_validates_projection() -> None:
    atom = _logical_atom(semantic_keys=("alpha", "beta"))
    projected = ActiveRecordV2(atom, ("beta",))

    assert projected.atom.semantic_keys == ("alpha", "beta")
    assert projected.active_keys == ("beta",)
    with pytest.raises(ValueError, match="active_keys"):
        ActiveRecordV2(atom, ("missing",))
    with pytest.raises(ValueError, match="active_keys"):
        ActiveRecordV2(atom, ("beta", "alpha"))


def test_frontier_record_is_canonical_and_bound_to_source_hash() -> None:
    atom = _logical_atom()
    winner = _winner(atom)

    assert winner.source_record_hash == source_record_hash_v2(atom)
    with pytest.raises(ValueError, match="source_record_hash"):
        replace(winner, source_record_hash="0" * 64)
    with pytest.raises(ValueError, match="source_id"):
        replace(winner, source_id="sha256:" + "1" * 64)


def test_state_enforces_frontier_active_record_bijection() -> None:
    atom = _logical_atom(semantic_keys=("alpha", "beta"))
    alpha = KeyWinnerV2.from_atom(atom, "alpha")
    beta = KeyWinnerV2.from_atom(atom, "beta")
    projected = ActiveRecordV2(atom, ("beta",))

    with pytest.raises(ValueError, match="active_keys"):
        _state(frontier=(alpha, beta), body=(DirectBlockV2(projected),))
    with pytest.raises(ValueError, match="frontier"):
        _state(frontier=(beta, alpha), body=(DirectBlockV2(_active(atom)),))
    with pytest.raises(ValueError, match="key_registry_limit"):
        _state(
            frontier=(alpha, beta),
            body=(DirectBlockV2(_active(atom)),),
            key_registry_limit=1,
        )
    with pytest.raises(ValueError, match="receipt keys"):
        _state(
            frontier=(beta,),
            body=(DirectBlockV2(ActiveRecordV2(atom, ("beta",))),),
        )


def test_state_rejects_inconsistent_control_marks_for_one_source() -> None:
    atom = _logical_atom(semantic_keys=("alpha", "beta"))
    alpha = KeyWinnerV2.from_atom(atom, "alpha")
    beta = replace(KeyWinnerV2.from_atom(atom, "beta"), status=AtomStatus.RETRACTED)

    with pytest.raises(ValueError, match="source receipt"):
        _state(
            frontier=(alpha, beta),
            body=(DirectBlockV2(ActiveRecordV2(atom, ("alpha",))),),
        )


def test_state_rejects_forged_released_winner_core_or_key() -> None:
    core = _logical_atom(
        "goal",
        semantic_keys=("goal",),
        role=AtomRole.ROOT_GOAL,
        core_required=True,
    )
    winner = _winner(core)

    with pytest.raises(ValueError, match="source receipt"):
        _state(
            frontier=(replace(winner, core_required=False),),
            source_atoms=(core,),
        )
    with pytest.raises(ValueError, match="receipt"):
        _state(
            frontier=(replace(winner, semantic_key="forged-key"),),
            source_atoms=(core,),
        )


def test_state_allows_released_noncore_payload_but_not_missing_core_payload() -> None:
    context = _logical_atom()
    _state(frontier=(_winner(context),), body=(), source_atoms=(context,))

    core = _logical_atom(
        "goal",
        semantic_keys=("goal",),
        role=AtomRole.ROOT_GOAL,
        core_required=True,
    )
    with pytest.raises(ValueError, match="core.*payload"):
        _state(frontier=(_winner(core),), kernel=(), source_atoms=(core,))


def test_state_rejects_wrong_frame_or_logical_partition() -> None:
    core = _logical_atom(
        "goal",
        semantic_keys=("goal",),
        role=AtomRole.ROOT_GOAL,
        core_required=True,
    )
    active = _active(core)
    winner = _winner(core)

    with pytest.raises(ValueError, match="schema_version"):
        replace(_state(frontier=(winner,), kernel=(active,)), schema_version=1)
    with pytest.raises(ValueError, match="version"):
        WeightPolicyV2.create(version="", source_weights=())
    with pytest.raises(ValueError, match="high_water"):
        replace(_state(frontier=(winner,), kernel=(active,)), high_water=0)
    with pytest.raises(ValueError, match="core_required"):
        _state(frontier=(winner,), body=(DirectBlockV2(active),))


def test_delta_envelope_locks_atomic_monotonic_watermarks() -> None:
    first = _logical_atom(as_of=1)
    second = _logical_atom("second", semantic_keys=("second",), as_of=2)

    assert DeltaEnvelopeV2(0, 1, (first,)).target_high_water == 1
    assert DeltaEnvelopeV2(1, 1, (first,)).target_high_water == 1
    with pytest.raises(ValueError, match="continuous"):
        DeltaEnvelopeV2(0, 2, (second,))
    with pytest.raises(ValueError, match="stale/new"):
        DeltaEnvelopeV2(1, 2, (first, second))
    with pytest.raises(ValueError, match="target_high_water"):
        DeltaEnvelopeV2(1, 0, ())


@pytest.mark.parametrize(
    ("max_items", "max_text_bytes"),
    [(0, 192), (1, 0)],
)
def test_kernel_slot_rejects_nonpositive_bounds(
    max_items: int,
    max_text_bytes: int,
) -> None:
    with pytest.raises(ValueError, match="positive"):
        KernelSlotV2(AtomRole.ROOT_GOAL, max_items, max_text_bytes)


def test_kernel_schema_rejects_empty_duplicate_or_nonpositive_floor() -> None:
    slot = KernelSlotV2(AtomRole.ROOT_GOAL, 1, 192)
    with pytest.raises(ValueError, match="slots"):
        KernelSchemaV2((), 4608)
    with pytest.raises(ValueError, match="unique"):
        KernelSchemaV2((slot, slot), 4608)
    with pytest.raises(ValueError, match="continuity_floor_bytes"):
        KernelSchemaV2((slot,), 0)
