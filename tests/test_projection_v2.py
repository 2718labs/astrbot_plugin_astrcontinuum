from __future__ import annotations

from crm_experiment.codec_v2 import attempt_pack_group_v2, direct_view_v2
from crm_experiment.contracts import AtomRole, AtomStatus, QuerySpec
from crm_experiment.contracts_v2 import (
    ActiveRecordV2,
    CapsuleStateV2,
    DirectBlockV2,
    FrozenStateViewV2,
    KeyWinnerV2,
    LogicalAtomV2,
    PackedContextBlockV2,
    PackingPolicyV2,
    SourceReceiptV2,
    SourceWeightV2,
    WeightPolicyV2,
)
from crm_experiment.projection_v2 import UNKNOWN_V2, project_query_v2


def _atom(label: str, text: str) -> LogicalAtomV2:
    return LogicalAtomV2.create(
        source_label=label,
        semantic_keys=(f"topic:{label}",),
        role=AtomRole.CONTEXT,
        text=text,
        status=AtomStatus.ACTIVE,
        revision=1,
        as_of=1,
        provenance=(f"fixture:{label}",),
        exact=False,
        depends_on=(),
        core_required=False,
    )


def _state(*atoms: LogicalAtomV2) -> CapsuleStateV2:
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
    source_ids = tuple(sorted(atom.source_id for atom in atoms))
    return CapsuleStateV2(
        schema_version=2,
        generation=1,
        high_water=1,
        accepted_budget=65_536,
        key_registry_limit=64,
        max_semantic_key_bytes=128,
        frontier=frontier,
        receipts=tuple(
            sorted(
                (SourceReceiptV2.from_atom(atom) for atom in atoms),
                key=lambda receipt: receipt.source_id,
            )
        ),
        weight_policy=WeightPolicyV2.create(
            version="fixture-v1",
            source_weights=tuple(
                SourceWeightV2(source_id, 1.0) for source_id in source_ids
            ),
        ),
        kernel=(),
        body=tuple(DirectBlockV2(record) for record in records),
        packing_policy=PackingPolicyV2(
            codec="dmc1-lcp-lcs-v1",
            immutable_source_ids=source_ids,
            max_records_per_block=8,
            max_decoded_block_bytes=65_536,
        ),
    )


def _packed_pair() -> tuple[CapsuleStateV2, CapsuleStateV2, LogicalAtomV2]:
    prefix = "projection-shared-laboratory-context-" * 30
    first = _atom("a", f"{prefix}alpha")
    second = _atom("b", f"{prefix}beta")
    direct = _state(first, second)
    packed = attempt_pack_group_v2(direct, (first.source_id, second.source_id)).state
    return direct, packed, first


def test_direct_and_packed_projection_are_source_bound_and_identical() -> None:
    direct, packed, queried = _packed_pair()
    query = QuerySpec(query_id="q-a", role=AtomRole.CONTEXT, semantic_key="topic:a")

    direct_result = project_query_v2(FrozenStateViewV2(direct), query, 65_536)
    packed_result = project_query_v2(FrozenStateViewV2(packed), query, 65_536)

    assert packed_result == direct_result
    assert packed_result.supported
    assert packed_result.reason == "supported"
    assert packed_result.text == queried.text
    assert packed_result.selected_source_ids == (queried.source_id,)
    assert tuple(record.source_id for record in packed_result.records) == (
        queried.source_id,
    )
    assert packed_result.records[0].text == queried.text
    assert packed_result.records[0].active_keys == ("topic:a",)


def test_projection_budget_uses_original_decoded_text_not_residual() -> None:
    _, packed, queried = _packed_pair()
    query = QuerySpec(query_id="q-a", role=AtomRole.CONTEXT, semantic_key="topic:a")

    result = project_query_v2(FrozenStateViewV2(packed), query, len(b"alpha"))

    assert not result.supported
    assert result.text == UNKNOWN_V2
    assert result.records == ()
    assert result.selected_source_ids == ()
    assert result.reason == "injection_budget"
    assert len(queried.text.encode("utf-8")) > len(b"alpha")


def test_multibyte_projection_has_an_exact_utf8_budget_boundary() -> None:
    prefix = "共享实验上下文🙂" * 40
    queried = _atom("a", f"{prefix}甲")
    other = _atom("b", f"{prefix}乙")
    direct = _state(queried, other)
    packed = attempt_pack_group_v2(direct, (queried.source_id, other.source_id)).state
    query = QuerySpec(query_id="q-a", role=AtomRole.CONTEXT, semantic_key="topic:a")
    exact_budget = len(queried.text.encode("utf-8"))

    exact = project_query_v2(FrozenStateViewV2(packed), query, exact_budget)
    short = project_query_v2(FrozenStateViewV2(packed), query, exact_budget - 1)
    wrong_role = project_query_v2(
        FrozenStateViewV2(packed),
        QuerySpec(query_id="q-role", role=AtomRole.DECISION, semantic_key="topic:a"),
        exact_budget,
    )

    assert exact.supported and exact.byte_cost == exact_budget
    assert not short.supported and short.reason == "injection_budget"
    assert not wrong_role.supported and wrong_role.reason == "no_matching_source"


def test_corrupted_frozen_packed_state_projects_unknown_without_fallback() -> None:
    _, packed, _ = _packed_pair()
    block = packed.body[0]
    assert isinstance(block, PackedContextBlockV2)
    object.__setattr__(block, "common_prefix", f"{block.common_prefix}corrupt")
    query = QuerySpec(query_id="q-a", role=AtomRole.CONTEXT, semantic_key="topic:a")

    result = project_query_v2(FrozenStateViewV2(packed), query, 65_536)

    assert not result.supported
    assert result.text == UNKNOWN_V2
    assert result.records == ()
    assert result.selected_source_ids == ()
    assert result.reason == "malformed_frozen_state"


def test_nested_packed_entry_projects_unknown_instead_of_crashing() -> None:
    _, packed, _ = _packed_pair()
    block = packed.body[0]
    assert isinstance(block, PackedContextBlockV2)
    object.__setattr__(block, "entries", (block, *block.entries[1:]))
    query = QuerySpec(query_id="q-a", role=AtomRole.CONTEXT, semantic_key="topic:a")

    result = project_query_v2(FrozenStateViewV2(packed), query, 65_536)

    assert not result.supported
    assert result.text == UNKNOWN_V2
    assert result.reason == "malformed_frozen_state"


def test_mutated_released_receipt_projects_unknown() -> None:
    direct, _, queried = _packed_pair()
    released = direct_view_v2(direct)
    released = CapsuleStateV2(
        schema_version=released.schema_version,
        generation=released.generation,
        high_water=released.high_water,
        accepted_budget=released.accepted_budget,
        key_registry_limit=released.key_registry_limit,
        max_semantic_key_bytes=released.max_semantic_key_bytes,
        frontier=released.frontier,
        receipts=released.receipts,
        weight_policy=released.weight_policy,
        kernel=released.kernel,
        body=tuple(
            block
            for block in released.body
            if block.record.atom.source_id != queried.source_id
        ),
        packing_policy=released.packing_policy,
    )
    receipt = next(
        item for item in released.receipts if item.source_id == queried.source_id
    )
    object.__setattr__(receipt, "payload_hash", "0" * 64)
    query = QuerySpec(query_id="q-a", role=AtomRole.CONTEXT, semantic_key="topic:a")

    result = project_query_v2(FrozenStateViewV2(released), query, 65_536)

    assert not result.supported
    assert result.reason == "malformed_frozen_state"


def test_mutated_nested_weight_projects_unknown() -> None:
    direct, _, _ = _packed_pair()
    source_weight = direct.weight_policy.source_weights[0]
    object.__setattr__(source_weight, "weight", -1.0)
    query = QuerySpec(query_id="q-a", role=AtomRole.CONTEXT, semantic_key="topic:a")

    result = project_query_v2(FrozenStateViewV2(direct), query, 65_536)

    assert not result.supported
    assert result.reason == "malformed_frozen_state"


def test_projection_never_revives_a_released_payload_from_receipt() -> None:
    direct, _, queried = _packed_pair()
    released = direct_view_v2(direct)
    released = CapsuleStateV2(
        schema_version=released.schema_version,
        generation=released.generation,
        high_water=released.high_water,
        accepted_budget=released.accepted_budget,
        key_registry_limit=released.key_registry_limit,
        max_semantic_key_bytes=released.max_semantic_key_bytes,
        frontier=released.frontier,
        receipts=released.receipts,
        weight_policy=released.weight_policy,
        kernel=released.kernel,
        body=tuple(
            block
            for block in released.body
            if block.record.atom.source_id != queried.source_id
        ),
        packing_policy=released.packing_policy,
    )
    query = QuerySpec(query_id="q-a", role=AtomRole.CONTEXT, semantic_key="topic:a")

    result = project_query_v2(FrozenStateViewV2(released), query, 65_536)

    assert not result.supported
    assert result.text == UNKNOWN_V2
    assert result.selected_source_ids == ()
    assert result.reason == "no_matching_source"
