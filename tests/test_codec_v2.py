from __future__ import annotations

from dataclasses import replace
from itertools import permutations

import pytest

from crm_experiment.canonical import canonical_json
from crm_experiment.codec_v2 import (
    CodecDecodeErrorV2,
    attempt_pack_group_v2,
    decode_frozen_state_v2,
    direct_view_v2,
)
from crm_experiment.contracts import AtomRole, AtomStatus
from crm_experiment.contracts_v2 import (
    ActiveRecordV2,
    CapsuleStateV2,
    DeltaEnvelopeV2,
    DirectBlockV2,
    KeyWinnerV2,
    LogicalAtomV2,
    PackedContextBlockV2,
    PackingPolicyV2,
    SourceReceiptV2,
    SourceWeightV2,
    WeightPolicyV2,
)
from crm_experiment.logical_v2 import resolve_latest_v2
from crm_experiment.resident_v2 import (
    logical_semantic_hash_v2,
    resident_breakdown_v2,
    resident_bytes_v2,
    resident_hash_v2,
)


def _atom(
    label: str,
    text: str,
    *,
    semantic_keys: tuple[str, ...] | None = None,
    role: AtomRole = AtomRole.CONTEXT,
    exact: bool = False,
    core: bool = False,
    depends_on: tuple[str, ...] = (),
    revision: int = 1,
    as_of: int = 1,
) -> LogicalAtomV2:
    return LogicalAtomV2.create(
        source_label=label,
        semantic_keys=semantic_keys or (f"topic:{label}",),
        role=role,
        text=text,
        status=AtomStatus.ACTIVE,
        revision=revision,
        as_of=as_of,
        provenance=(f"fixture:{label}",),
        exact=exact,
        depends_on=depends_on,
        core_required=core,
    )


def _state(
    *atoms: LogicalAtomV2,
    immutable_ids: tuple[str, ...] | None = None,
    max_records_per_block: int = 8,
    max_decoded_block_bytes: int = 65_536,
) -> CapsuleStateV2:
    records = tuple(
        sorted(
            (ActiveRecordV2(atom, atom.semantic_keys) for atom in atoms),
            key=lambda record: record.atom.source_id,
        )
    )
    frontier = tuple(
        sorted(
            (
                KeyWinnerV2.from_atom(atom, key)
                for atom in atoms
                for key in atom.semantic_keys
            ),
            key=lambda winner: winner.semantic_key,
        )
    )
    receipts = tuple(
        sorted(
            (SourceReceiptV2.from_atom(atom) for atom in atoms),
            key=lambda receipt: receipt.source_id,
        )
    )
    source_weights = tuple(
        SourceWeightV2(source_id, 1.0)
        for source_id in sorted({winner.source_id for winner in frontier})
    )
    policy = PackingPolicyV2(
        codec="dmc1-lcp-lcs-v1",
        immutable_source_ids=tuple(
            sorted(
                immutable_ids
                if immutable_ids is not None
                else (atom.source_id for atom in atoms if not atom.core_required)
            )
        ),
        max_records_per_block=max_records_per_block,
        max_decoded_block_bytes=max_decoded_block_bytes,
    )
    return CapsuleStateV2(
        schema_version=2,
        generation=1,
        high_water=max((atom.as_of for atom in atoms), default=0),
        accepted_budget=65_536,
        key_registry_limit=64,
        max_semantic_key_bytes=128,
        frontier=frontier,
        receipts=receipts,
        weight_policy=WeightPolicyV2.create(
            version="fixture-v1", source_weights=source_weights
        ),
        kernel=tuple(record for record in records if record.atom.core_required),
        body=tuple(
            DirectBlockV2(record) for record in records if not record.atom.core_required
        ),
        packing_policy=policy,
    )


def _shared_atoms() -> tuple[LogicalAtomV2, LogicalAtomV2]:
    prefix = "laboratory observation with stable apparatus and calibration: " * 8
    return _atom("a", f"{prefix}alpha-tail"), _atom("b", f"{prefix}beta-tail")


def _packed_block(state: CapsuleStateV2) -> PackedContextBlockV2:
    blocks = tuple(
        block for block in state.body if isinstance(block, PackedContextBlockV2)
    )
    assert len(blocks) == 1
    return blocks[0]


def test_shared_prefix_emits_strictly_smaller_lossless_state() -> None:
    atoms = _shared_atoms()
    direct = _state(*atoms)

    outcome = attempt_pack_group_v2(
        direct, tuple(atom.source_id for atom in reversed(atoms))
    )

    assert outcome.emitted
    assert outcome.reason == "packed"
    assert outcome.risk == 0.0
    assert outcome.direct_bytes == resident_bytes_v2(direct)
    assert outcome.packed_bytes == resident_bytes_v2(outcome.state)
    assert outcome.savings_bytes == outcome.direct_bytes - outcome.packed_bytes
    assert outcome.savings_bytes > 0
    breakdown = resident_breakdown_v2(outcome.state)
    assert breakdown.persistent_bytes == outcome.packed_bytes
    assert len(breakdown.body_item_bytes) == len(outcome.state.body)
    assert decode_frozen_state_v2(outcome.state) == decode_frozen_state_v2(direct)
    assert logical_semantic_hash_v2(outcome.state) == logical_semantic_hash_v2(direct)
    assert resident_hash_v2(outcome.state) != resident_hash_v2(direct)


def test_canonical_prefix_then_suffix_factoring_is_exact() -> None:
    atoms = (
        _atom("a", "ab" + "shared-payload-" * 20 + "Xcd"),
        _atom("b", "ab" + "shared-payload-" * 20 + "Ycd"),
    )
    direct = _state(*atoms)
    outcome = attempt_pack_group_v2(direct, tuple(atom.source_id for atom in atoms))

    block = _packed_block(outcome.state)
    assert block.common_prefix == "ab" + "shared-payload-" * 20
    assert block.common_suffix == "cd"
    expected_middle = {atoms[0].source_id: "X", atoms[1].source_id: "Y"}
    assert tuple(entry.text_middle for entry in block.entries) == tuple(
        expected_middle[entry.source_id] for entry in block.entries
    )
    assert decode_frozen_state_v2(outcome.state) == decode_frozen_state_v2(direct)


def test_equal_text_has_one_canonical_full_prefix_representation() -> None:
    text = '相同内容🙂\\\n"' * 30
    atoms = _atom("a", text), _atom("b", text)
    direct = _state(*atoms)
    outcome = attempt_pack_group_v2(direct, tuple(atom.source_id for atom in atoms))

    block = _packed_block(outcome.state)
    assert block.common_prefix == text
    assert block.common_suffix == ""
    assert all(entry.text_middle == "" for entry in block.entries)
    assert decode_frozen_state_v2(outcome.state) == decode_frozen_state_v2(direct)


@pytest.mark.parametrize(
    "left,right",
    [
        ("", "nonempty"),
        ("alpha", "bravo"),
        ("e\u0301-A", "é-B"),
    ],
)
def test_no_shared_affix_stays_direct(left: str, right: str) -> None:
    atoms = _atom("a", left), _atom("b", right)
    outcome = attempt_pack_group_v2(
        _state(*atoms), tuple(atom.source_id for atom in atoms)
    )

    assert not outcome.emitted
    assert outcome.reason == "no_shared_affix"
    assert all(isinstance(block, DirectBlockV2) for block in outcome.state.body)
    assert outcome.savings_bytes == 0


def test_unicode_and_json_control_corpus_roundtrips_byte_exactly() -> None:
    prefix = '论文数据🙂\x00\n"\\e\u0301-' * 30
    atoms = (
        _atom("a", f"{prefix}甲-tail"),
        _atom("b", f"{prefix}乙-tail"),
        _atom("c", f"{prefix}é-tail"),
    )
    direct = _state(*atoms)
    outcome = attempt_pack_group_v2(direct, tuple(atom.source_id for atom in atoms))

    assert outcome.emitted
    assert decode_frozen_state_v2(outcome.state) == decode_frozen_state_v2(direct)
    assert tuple(
        record.atom.text for record in decode_frozen_state_v2(outcome.state)
    ) == tuple(record.atom.text for record in decode_frozen_state_v2(direct))


def test_input_permutations_produce_identical_canonical_state() -> None:
    prefix = "deterministic-common-prefix-" * 30
    atoms = tuple(_atom(label, f"{prefix}{label}") for label in ("a", "b", "c"))
    expected: str | None = None
    for ordering in permutations(atoms):
        direct = _state(*ordering)
        outcome = attempt_pack_group_v2(
            direct, tuple(atom.source_id for atom in ordering)
        )
        assert outcome.emitted
        encoded = canonical_json(outcome.state)
        expected = encoded if expected is None else expected
        assert encoded == expected


def test_repacking_a_packed_state_is_idempotent_and_never_nests() -> None:
    atoms = _shared_atoms()
    first = attempt_pack_group_v2(
        _state(*atoms), tuple(atom.source_id for atom in atoms)
    )
    second = attempt_pack_group_v2(first.state, tuple(atom.source_id for atom in atoms))

    assert first.emitted and second.emitted
    assert canonical_json(second.state) == canonical_json(first.state)
    assert resident_hash_v2(second.state) == resident_hash_v2(first.state)
    block = _packed_block(second.state)
    assert all(not isinstance(entry, PackedContextBlockV2) for entry in block.entries)


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        ("middle", "payload"),
        ("prefix", "payload"),
        ("suffix", "payload"),
        ("provenance", "payload"),
        ("active_keys", "active_keys"),
    ],
)
def test_corrupted_packed_payload_fails_closed(mutator: str, expected: str) -> None:
    atoms = _shared_atoms()
    outcome = attempt_pack_group_v2(
        _state(*atoms), tuple(atom.source_id for atom in atoms)
    )
    block = _packed_block(outcome.state)
    entry = block.entries[0]
    if mutator == "middle":
        object.__setattr__(entry, "text_middle", f"{entry.text_middle}!")
    elif mutator == "prefix":
        object.__setattr__(block, "common_prefix", f"{block.common_prefix}!")
    elif mutator == "suffix":
        object.__setattr__(block, "common_suffix", f"{block.common_suffix}!")
    elif mutator == "provenance":
        object.__setattr__(entry, "provenance", ("forged",))
    else:
        object.__setattr__(entry, "active_keys", ("topic:forged",))

    with pytest.raises(CodecDecodeErrorV2, match=expected):
        decode_frozen_state_v2(outcome.state)
    with pytest.raises(ValueError):
        outcome.state.__post_init__()


def test_noncanonical_factoring_and_duplicate_residency_are_rejected() -> None:
    atoms = _shared_atoms()
    direct = _state(*atoms)
    outcome = attempt_pack_group_v2(direct, tuple(atom.source_id for atom in atoms))
    block = _packed_block(outcome.state)
    moved = block.common_prefix[-1]
    with pytest.raises(ValueError, match="canonical"):
        replace(
            block,
            common_prefix=block.common_prefix[:-1],
            entries=tuple(
                replace(entry, text_middle=f"{moved}{entry.text_middle}")
                for entry in block.entries
            ),
        )
    duplicate = next(
        direct_block
        for direct_block in direct.body
        if direct_block.record.atom.source_id == block.source_ids[0]
    )
    with pytest.raises(ValueError, match="resident source IDs"):
        replace(outcome.state, body=(duplicate, block))


@pytest.mark.parametrize(
    "change",
    ["not_immutable", "exact", "core", "dependency", "wrong_role"],
)
def test_ineligible_records_never_pack(change: str) -> None:
    prefix = "shared immutable-looking context " * 30
    first = _atom("a", f"{prefix}a")
    if change == "exact":
        second = _atom("b", f"{prefix}b", exact=True)
    elif change == "core":
        second = _atom("b", f"{prefix}b", core=True, role=AtomRole.CURRENT_FOCUS)
    elif change == "dependency":
        second = _atom("b", f"{prefix}b", depends_on=(first.source_id,))
    elif change == "wrong_role":
        second = _atom("b", f"{prefix}b", role=AtomRole.DECISION)
    else:
        second = _atom("b", f"{prefix}b")
    immutable = (
        (first.source_id,)
        if change == "not_immutable"
        else (first.source_id, second.source_id)
    )
    direct = _state(first, second, immutable_ids=immutable)

    outcome = attempt_pack_group_v2(direct, (first.source_id, second.source_id))

    assert not outcome.emitted
    assert outcome.reason == "ineligible_source"


def test_policy_bounds_fail_closed_without_partial_pack() -> None:
    prefix = "bounded-common-prefix-" * 30
    atoms = tuple(_atom(label, f"{prefix}{label}") for label in ("a", "b", "c"))
    count_limited = _state(*atoms, max_records_per_block=2)
    byte_limited = _state(*atoms, max_decoded_block_bytes=10)

    count = attempt_pack_group_v2(
        count_limited, tuple(atom.source_id for atom in atoms)
    )
    size = attempt_pack_group_v2(byte_limited, tuple(atom.source_id for atom in atoms))

    assert not count.emitted and count.reason == "record_limit"
    assert not size.emitted and size.reason == "decoded_byte_limit"


def test_packed_sibling_survives_another_sources_update() -> None:
    old_a, old_b = _shared_atoms()
    packed = attempt_pack_group_v2(
        _state(old_a, old_b), (old_a.source_id, old_b.source_id)
    ).state
    flat_base = direct_view_v2(packed)
    new_a = _atom(
        "a-v2",
        "new A payload",
        semantic_keys=old_a.semantic_keys,
        revision=2,
        as_of=2,
    )

    resolution = resolve_latest_v2(
        flat_base,
        DeltaEnvelopeV2(
            base_high_water=1,
            target_high_water=2,
            records=(new_a,),
        ),
    )

    assert {record.atom.text for record in resolution.active_records} == {
        new_a.text,
        old_b.text,
    }
    assert all(
        record.atom.source_id != old_a.source_id for record in resolution.active_records
    )
