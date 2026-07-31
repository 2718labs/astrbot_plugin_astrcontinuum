from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from hashlib import sha256
from typing import get_type_hints

import pytest

from crm_experiment.canonical import (
    canonical_json,
    canonical_value,
    semantic_hash,
    sha256_text,
    utf8_bytes,
)
from crm_experiment.contracts import (
    AtomRole,
    AtomStatus,
    Candidate,
    CapsuleState,
    ClaimV2Result,
    KernelSchema,
    KernelSelection,
    KernelSlot,
    LossPolicy,
    LossReport,
    MatrixBundle,
    OutcomeState,
    ProjectionResult,
    QueryGold,
    QuerySpec,
    RecompositionRequest,
    RecompositionResult,
    Selection,
    SemanticAtom,
)

EXPECTED_RECORDS: dict[type[object], tuple[tuple[str, object], ...]] = {
    SemanticAtom: (
        ("atom_id", str),
        ("semantic_keys", tuple[str, ...]),
        ("role", AtomRole),
        ("text", str),
        ("status", AtomStatus),
        ("revision", int),
        ("as_of", int),
        ("provenance", tuple[str, ...]),
        ("exact", bool),
        ("depends_on", tuple[str, ...]),
        ("weight", float),
        ("core_required", bool),
        ("merge_depth", int),
        ("covered_atom_ids", tuple[str, ...]),
    ),
    Candidate: (
        ("candidate_id", str),
        ("role", AtomRole),
        ("text", str),
        ("covers", tuple[str, ...]),
        ("semantic_keys", tuple[str, ...]),
        ("byte_cost", int),
        ("error_risk", float),
        ("depends_on", tuple[str, ...]),
        ("exact", bool),
        ("core_required", bool),
        ("merge_depth", int),
    ),
    CapsuleState: (
        ("generation", int),
        ("high_water", int),
        ("accepted_budget", int),
        ("kernel", tuple[SemanticAtom, ...]),
        ("body", tuple[SemanticAtom, ...]),
        ("weight_version", str),
    ),
    KernelSlot: (
        ("role", AtomRole),
        ("max_items", int),
        ("max_text_bytes", int),
    ),
    KernelSchema: (
        ("slots", tuple[KernelSlot, ...]),
        ("metadata_reserve_bytes", int),
    ),
    KernelSelection: (
        ("atoms", tuple[SemanticAtom, ...]),
        ("byte_ceiling", int),
        ("valid", bool),
        ("reasons", tuple[str, ...]),
    ),
    MatrixBundle: (
        ("atom_ids", tuple[str, ...]),
        ("candidate_ids", tuple[str, ...]),
        ("features", tuple[tuple[float, ...], ...]),
        ("coverage", tuple[tuple[int, ...], ...]),
        ("redundancy", tuple[tuple[float, ...], ...]),
        ("conflicts", tuple[tuple[int, ...], ...]),
        ("dependencies", tuple[tuple[int, ...], ...]),
        ("costs", tuple[int, ...]),
        ("risks", tuple[float, ...]),
        ("weights", tuple[float, ...]),
        ("budget", int),
    ),
    LossPolicy: (
        ("gamma", float),
        ("rho", float),
        ("risk_ceiling", float),
        ("exact_threshold", int),
    ),
    Selection: (
        ("candidate_ids", tuple[str, ...]),
        ("covered_atom_ids", tuple[str, ...]),
        ("objective", float),
        ("byte_cost", int),
        ("solver", str),
    ),
    LossReport: (
        ("retained_atom_ids", tuple[str, ...]),
        ("merged_atom_ids", tuple[str, ...]),
        ("released_atom_ids", tuple[str, ...]),
        ("omission_weight", float),
        ("error_risk", float),
        ("continuity_break", bool),
        ("stale_current", bool),
    ),
    RecompositionRequest: (
        ("base_state", CapsuleState | None),
        ("delta", tuple[SemanticAtom, ...]),
        ("byte_budget", int),
        ("kernel_schema", KernelSchema),
        ("loss_policy", LossPolicy),
        ("weight_version", str),
    ),
    RecompositionResult: (
        ("state", CapsuleState),
        ("outcome", OutcomeState),
        ("loss", LossReport),
        ("matrix_hash", str),
        ("semantic_hash", str),
        ("strict_eligible", bool),
        ("consumed_delta", bool),
        ("stage_ns", tuple[tuple[str, int], ...]),
    ),
    QuerySpec: (
        ("query_id", str),
        ("role", AtomRole),
        ("semantic_key", str),
    ),
    ProjectionResult: (
        ("query_id", str),
        ("text", str),
        ("selected_atom_ids", tuple[str, ...]),
        ("selected_covered_ids", tuple[str, ...]),
        ("byte_cost", int),
        ("supported", bool),
    ),
    QueryGold: (
        ("query_id", str),
        ("required_atom_ids", tuple[str, ...]),
        ("forbidden_atom_ids", tuple[str, ...]),
        ("required_text", tuple[str, ...]),
        ("forbidden_text", tuple[str, ...]),
        ("core_query", bool),
    ),
    ClaimV2Result: (
        ("query_id", str),
        ("passed", bool),
        ("omission", bool),
        ("stale_current", bool),
        ("missing", tuple[str, ...]),
        ("contradictions", tuple[str, ...]),
    ),
}


def _atom(
    *,
    atom_id: str = "goal-1",
    text: str = "完成 Capsule 实验🙂",
) -> SemanticAtom:
    return SemanticAtom(
        atom_id=atom_id,
        semantic_keys=("goal",),
        role=AtomRole.ROOT_GOAL,
        text=text,
        status=AtomStatus.ACTIVE,
        revision=2,
        as_of=7,
        provenance=("delta-7",),
        exact=True,
        depends_on=(),
        weight=4.5,
        core_required=True,
        merge_depth=0,
        covered_atom_ids=(atom_id,),
    )


def _state(
    *,
    atom: SemanticAtom | None = None,
    generation: int = 3,
    high_water: int = 7,
    accepted_budget: int = 2048,
    weight_version: str = "theta-v1",
) -> CapsuleState:
    return CapsuleState(
        generation=generation,
        high_water=high_water,
        accepted_budget=accepted_budget,
        kernel=(atom or _atom(),),
        body=(),
        weight_version=weight_version,
    )


def test_enum_values_are_exact_and_ordered() -> None:
    assert [(member.name, member.value) for member in AtomRole] == [
        ("ROOT_GOAL", "root_goal"),
        ("CURRENT_FOCUS", "current_focus"),
        ("OPEN_LOOP", "open_loop"),
        ("HARD_CONSTRAINT", "hard_constraint"),
        ("DECISION", "decision"),
        ("EXACT_ANCHOR", "exact_anchor"),
        ("CONTEXT", "context"),
    ]
    assert [(member.name, member.value) for member in AtomStatus] == [
        ("ACTIVE", "active"),
        ("SUPERSEDED", "superseded"),
        ("RETRACTED", "retracted"),
    ]
    assert [(member.name, member.value) for member in OutcomeState] == [
        ("NORMAL", "normal"),
        ("KERNEL_ONLY", "kernel_only"),
        ("ADMISSION_BLOCKED", "admission_blocked"),
    ]


@pytest.mark.parametrize(
    ("record_type", "expected_fields"),
    EXPECTED_RECORDS.items(),
)
def test_record_field_order_and_types_are_exact(
    record_type: type[object],
    expected_fields: tuple[tuple[str, object], ...],
) -> None:
    assert is_dataclass(record_type)
    assert tuple(field.name for field in fields(record_type)) == tuple(
        name for name, _ in expected_fields
    )
    assert get_type_hints(record_type) == dict(expected_fields)


@pytest.mark.parametrize(
    ("record_type", "expected_fields"),
    EXPECTED_RECORDS.items(),
)
def test_records_are_frozen_and_slotted(
    record_type: type[object],
    expected_fields: tuple[tuple[str, object], ...],
) -> None:
    dataclass_params = getattr(record_type, "__dataclass_params__")
    assert dataclass_params.frozen
    assert tuple(getattr(record_type, "__slots__")) == tuple(
        name for name, _ in expected_fields
    )

    atom = _atom()
    assert not hasattr(atom, "__dict__")
    with pytest.raises(FrozenInstanceError):
        atom.text = "不可修改"  # type: ignore[misc]


def test_locked_public_function_annotations_are_exact() -> None:
    assert get_type_hints(utf8_bytes) == {"value": str, "return": int}
    assert get_type_hints(canonical_value) == {"value": object, "return": object}
    assert get_type_hints(canonical_json) == {"value": object, "return": str}
    assert get_type_hints(sha256_text) == {"value": str, "return": str}
    assert get_type_hints(semantic_hash) == {
        "state": CapsuleState,
        "return": str,
    }


def test_utf8_bytes_counts_multibyte_text() -> None:
    assert utf8_bytes("A桥🙂") == 8


def test_canonical_json_is_utf8_preserving_compact_and_mapping_ordered() -> None:
    left = {"z": "🙂", "a": [1, "桥"]}
    right = {"a": [1, "桥"], "z": "🙂"}

    assert canonical_json(left) == '{"a":[1,"桥"],"z":"🙂"}'
    assert canonical_json(left) == canonical_json(right)


def test_canonical_value_recursively_converts_dataclasses_and_enums() -> None:
    converted = canonical_value(_atom())

    assert isinstance(converted, dict)
    assert converted == {
        "atom_id": "goal-1",
        "semantic_keys": ["goal"],
        "role": "root_goal",
        "text": "完成 Capsule 实验🙂",
        "status": "active",
        "revision": 2,
        "as_of": 7,
        "provenance": ["delta-7"],
        "exact": True,
        "depends_on": [],
        "weight": 4.5,
        "core_required": True,
        "merge_depth": 0,
        "covered_atom_ids": ["goal-1"],
    }
    assert type(converted["role"]) is str
    assert type(converted["status"]) is str


@pytest.mark.parametrize(
    "value",
    [
        {1: "not-a-string-key"},
        {"nested": {"valid": {2: "not-a-string-key"}}},
    ],
)
def test_canonical_value_rejects_non_string_mapping_keys(value: object) -> None:
    with pytest.raises(TypeError, match="mapping keys must be strings"):
        canonical_value(value)


@pytest.mark.parametrize(
    "value",
    [
        b"raw-bytes",
        {"unsupported": {"set-value"}},
        object(),
    ],
)
def test_canonical_value_rejects_unsupported_values(value: object) -> None:
    with pytest.raises(TypeError, match="unsupported canonical value type"):
        canonical_value(value)


def test_sha256_text_hashes_utf8_bytes() -> None:
    assert sha256_text("语义🙂") == sha256("语义🙂".encode()).hexdigest()


def test_semantic_hash_excludes_generation_watermark_and_budget_metadata() -> None:
    state = _state()
    metadata_changed = replace(
        state,
        generation=99,
        high_water=1000,
        accepted_budget=8192,
    )

    assert semantic_hash(state) == semantic_hash(metadata_changed)
    assert semantic_hash(state) == sha256_text(
        canonical_json(
            {
                "kernel": state.kernel,
                "body": state.body,
                "weight_version": state.weight_version,
            }
        )
    )


def test_semantic_hash_changes_with_content_and_weight_version() -> None:
    state = _state()
    content_changed = replace(
        state,
        kernel=(replace(state.kernel[0], text="不同的当前目标"),),
    )
    weight_changed = replace(state, weight_version="theta-v2")

    assert semantic_hash(state) != semantic_hash(content_changed)
    assert semantic_hash(state) != semantic_hash(weight_changed)
