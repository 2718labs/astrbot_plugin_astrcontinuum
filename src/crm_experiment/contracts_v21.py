"""Independent schema-3 control-fold contracts for CRM V2.1.

This module deliberately owns its own nominal types and canonical domains.  It
does not import V2 contracts, hashes, codecs, or runners.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import Final, cast

CONTROL_FOLD_PROTOCOL_V21: Final = "crm-capsule-stress-v2.1-control-fold-v1"
SCHEMA_VERSION_V21: Final = 3
_CAPACITY_HASH_DOMAIN: Final = "v21-capacity-s3"
_STATE_HASH_DOMAIN: Final = "v21-state-s3"
_SEGMENT_INPUT_DOMAIN: Final = "v21-segment-input-s3"
_NAMESPACE_ID_DOMAIN: Final = "v21-namespace-id-s3"
_RECORD_ID_DOMAIN: Final = "v21-record-id-s3"
_SEGMENT_ID_DOMAIN: Final = "v21-segment-id-s3"
_DICTIONARY_ID_DOMAIN: Final = "v21-dictionary-id-s3"
_REPRESENTATIVE_ID_DOMAIN: Final = "v21-representative-id-s3"
_SOURCE_COMMITMENT_DOMAIN: Final = "v21-source-commitment-s3"
_DICTIONARY_COMMITMENT_DOMAIN: Final = "v21-dictionary-commitment-s3"
_REPRESENTATIVE_COMMITMENT_DOMAIN: Final = "v21-representative-commitment-s3"
_FOLDED_COMMITMENT_DOMAIN: Final = "v21-folded-commitment-s3"
_BARRIER_ROOT_DOMAIN: Final = "v21-barrier-root-s3"
_LEDGER_ROOT_DOMAIN: Final = "v21-ledger-root-s3"
_REENCODING_POLICY_DOMAIN: Final = "v21-reencoding-policy-s3"
_FOLD_POLICY_DOMAIN: Final = "v21-fold-policy-s3"
_OPAQUE_TOKEN_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/=-]{0,255}")


def _canonical_value_v21(value: object) -> object:
    """Return a JSON-compatible, deterministic representation for V21 values."""
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonical_value_v21(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, tuple):
        return [_canonical_value_v21(item) for item in value]
    if isinstance(value, Mapping):
        converted: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("V21 canonical mapping keys must be strings")
            converted[key] = _canonical_value_v21(item)
        return converted
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported V21 canonical value: {type(value).__name__}")


def _canonical_json_v21(value: object) -> str:
    return json.dumps(
        _canonical_value_v21(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_json_v21(value: object) -> str:
    """Serialize a V21 value into deterministic, compact canonical JSON."""
    return _canonical_json_v21(value)


def canonical_bytes_v21(value: object) -> int:
    """Return exact UTF-8 bytes for a V21 canonical value."""
    return len(_canonical_json_v21(value).encode("utf-8"))


def _domain_hash_v21(domain: str, value: object) -> str:
    digest = sha256(
        _canonical_json_v21({"domain": domain, "value": value}).encode("utf-8")
    ).hexdigest()
    return f"{domain}:{digest}"


@dataclass(frozen=True, slots=True)
class ControlFoldBoundsV21:
    """Frozen limits for every resident control-plane collection."""

    max_exact_kernel_records: int = 64
    max_exact_kernel_bytes: int = 16_384
    max_hot_records: int = 128
    max_hot_bytes: int = 16_384
    max_hot_frontier_entries: int = 128
    max_hot_frontier_bytes: int = 8_192
    max_dictionary_entries: int = 512
    max_dictionary_bytes: int = 32_768
    max_segments: int = 64
    max_representatives_per_segment: int = 16
    coverage_vector_size: int = 32
    bridge_vector_size: int = 16
    max_barriers: int = 128
    max_sparse_overrides: int = 256
    max_direct_parent_ids: int = 8
    max_parent_id_bytes: int = 4_096
    loss_ledger_bytes: int = 1_024

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field.name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ControlFrameV21:
    """Schema-3 frame whose capacity hash binds one frozen bounds value."""

    protocol_id: str
    schema_version: int
    generation: int
    high_water: int
    accepted_budget: int
    reencoding_policy_hash: str
    capacity_policy_hash: str


def capacity_policy_hash_v21(bounds: ControlFoldBoundsV21) -> str:
    """Return the domain-separated canonical hash of frozen V21 bounds."""
    if type(bounds) is not ControlFoldBoundsV21:
        raise TypeError("V21 capacity bounds require ControlFoldBoundsV21")
    digest = sha256(
        _canonical_json_v21({"domain": _CAPACITY_HASH_DOMAIN, "bounds": bounds}).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"{_CAPACITY_HASH_DOMAIN}:{digest}"


def validate_control_frame_v21(
    frame: ControlFrameV21, bounds: ControlFoldBoundsV21
) -> None:
    """Fail closed unless a nominal schema-3 frame binds the supplied bounds."""
    if type(frame) is not ControlFrameV21:
        raise TypeError("V21 frame requires nominal ControlFrameV21")
    if type(bounds) is not ControlFoldBoundsV21:
        raise TypeError("V21 bounds require nominal ControlFoldBoundsV21")
    if frame.protocol_id != CONTROL_FOLD_PROTOCOL_V21:
        raise ValueError("V21 protocol id is required")
    if frame.schema_version != SCHEMA_VERSION_V21:
        raise ValueError("V21 schema version must be 3")
    for name in ("generation", "high_water", "accepted_budget"):
        value = getattr(frame, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"frame {name} must be a non-negative integer")
    _require_domain_digest_v21(
        frame.reencoding_policy_hash,
        _REENCODING_POLICY_DOMAIN,
        "V21 reencoding policy hash",
    )
    if frame.capacity_policy_hash != capacity_policy_hash_v21(bounds):
        raise ValueError(
            "V21 frame capacity policy hash does not bind the supplied bounds"
        )


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_opaque_token(value: object, label: str) -> str:
    text = _require_text(value, label)
    if _OPAQUE_TOKEN_PATTERN.fullmatch(text) is None:
        raise ValueError(f"{label} must be an opaque identifier, not source prose")
    return text


def _require_domain_digest_v21(value: object, domain: str, label: str) -> str:
    text = _require_text(value, label)
    if re.fullmatch(rf"{re.escape(domain)}:[0-9a-f]{{64}}", text) is None:
        raise ValueError(f"{label} must be a {domain} domain digest")
    return text


def _require_nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _require_bool_v21(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a bool")
    return value


def _require_finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _require_role_v21(value: object, label: str) -> CapsuleRoleV21:
    if type(value) is not CapsuleRoleV21:
        raise ValueError(f"{label} must be a closed V21 role")
    return cast(CapsuleRoleV21, value)


def _require_loss_class_v21(value: object, label: str) -> LossClassV21:
    if type(value) is not LossClassV21:
        raise ValueError(f"{label} must be a closed V21 loss class")
    return cast(LossClassV21, value)


def namespace_identity_v21(label: str) -> str:
    """Construct an opaque V21 namespace identity from nonresident source text."""
    return _domain_hash_v21(
        _NAMESPACE_ID_DOMAIN,
        {"label": _require_text(label, "namespace identity label")},
    )


def record_identity_v21(namespace: str, label: str) -> str:
    """Construct a V21 record identity scoped to one namespace identity."""
    namespace_id = _require_domain_digest_v21(
        namespace, _NAMESPACE_ID_DOMAIN, "record identity namespace"
    )
    return _domain_hash_v21(
        _RECORD_ID_DOMAIN,
        {"namespace": namespace_id, "label": _require_text(label, "record label")},
    )


def segment_identity_v21(namespace: str, label: str) -> str:
    """Construct a V21 segment identity scoped to one namespace identity."""
    namespace_id = _require_domain_digest_v21(
        namespace, _NAMESPACE_ID_DOMAIN, "segment identity namespace"
    )
    return _domain_hash_v21(
        _SEGMENT_ID_DOMAIN,
        {"namespace": namespace_id, "label": _require_text(label, "segment label")},
    )


def dictionary_identity_v21(namespace: str, label: str) -> str:
    """Construct one opaque dictionary-key identity in a V21 namespace."""
    namespace_id = _require_domain_digest_v21(
        namespace, _NAMESPACE_ID_DOMAIN, "dictionary identity namespace"
    )
    return _domain_hash_v21(
        _DICTIONARY_ID_DOMAIN,
        {
            "namespace": namespace_id,
            "label": _require_text(label, "dictionary label"),
        },
    )


def representative_identity_v21(segment_id: str, label: str) -> str:
    """Construct a representative identity scoped to one V21 segment."""
    segment_identity = _require_domain_digest_v21(
        segment_id, _SEGMENT_ID_DOMAIN, "representative identity segment_id"
    )
    return _domain_hash_v21(
        _REPRESENTATIVE_ID_DOMAIN,
        {
            "segment_id": segment_identity,
            "label": _require_text(label, "representative label"),
        },
    )


def source_commitment_v21(
    *,
    record_id: str,
    namespace: str,
    incarnation: int,
    as_of: int,
    body: str,
    core_required: bool,
    active: bool,
) -> str:
    """Bind a retained source body to all of its resident V21 identity metadata."""
    return _domain_hash_v21(
        _SOURCE_COMMITMENT_DOMAIN,
        {
            "protocol_id": CONTROL_FOLD_PROTOCOL_V21,
            "schema_version": SCHEMA_VERSION_V21,
            "record_id": _require_domain_digest_v21(
                record_id, _RECORD_ID_DOMAIN, "source commitment record_id"
            ),
            "namespace": _require_domain_digest_v21(
                namespace, _NAMESPACE_ID_DOMAIN, "source commitment namespace"
            ),
            "incarnation": _require_nonnegative_int(
                incarnation, "source commitment incarnation"
            ),
            "as_of": _require_nonnegative_int(as_of, "source commitment as_of"),
            "body": _require_text(body, "source commitment body"),
            "core_required": _require_bool_v21(
                core_required, "source commitment core_required"
            ),
            "active": _require_bool_v21(active, "source commitment active"),
        },
    )


def dictionary_commitment_v21(namespace: str, key: str) -> str:
    """Bind a dictionary commitment to its namespace/key identities."""
    return _domain_hash_v21(
        _DICTIONARY_COMMITMENT_DOMAIN,
        {
            "namespace": _require_domain_digest_v21(
                namespace, _NAMESPACE_ID_DOMAIN, "dictionary commitment namespace"
            ),
            "key": _require_domain_digest_v21(
                key, _DICTIONARY_ID_DOMAIN, "dictionary commitment key"
            ),
        },
    )


def representative_commitment_v21(feature_id: str) -> str:
    """Bind a representative commitment to its opaque representative identity."""
    return _domain_hash_v21(
        _REPRESENTATIVE_COMMITMENT_DOMAIN,
        {
            "feature_id": _require_domain_digest_v21(
                feature_id,
                _REPRESENTATIVE_ID_DOMAIN,
                "representative commitment feature_id",
            )
        },
    )


def reencoding_policy_hash_v21(label: str) -> str:
    """Construct a field-specific digest for the immutable reencoding policy."""
    return _domain_hash_v21(
        _REENCODING_POLICY_DOMAIN,
        {"label": _require_text(label, "reencoding policy label")},
    )


def fold_policy_hash_v21(label: str) -> str:
    """Construct a field-specific digest for one fold-policy selection."""
    return _domain_hash_v21(
        _FOLD_POLICY_DOMAIN,
        {"label": _require_text(label, "fold policy label")},
    )


class CapsuleRoleV21(StrEnum):
    """Closed V21 role vocabulary; roles cannot transport source prose."""

    CONTEXT = "context"
    ROOT_GOAL = "root_goal"


class LossClassV21(StrEnum):
    """Closed V21 fold-loss vocabulary."""

    BOUNDED_FOLD = "bounded-fold"
    BOUNDED_HOLD = "bounded-hold"


@dataclass(frozen=True, slots=True)
class HardDependencyV21:
    """One explicitly declared hard edge to an exact resident commitment."""

    target_id: str
    target_commitment: str


@dataclass(frozen=True, slots=True)
class ExactRecordV21:
    """The only V21 type allowed to retain a source body."""

    record_id: str
    namespace: str
    incarnation: int
    as_of: int
    commitment: str
    body: str
    core_required: bool
    active: bool
    hard_depends_on: tuple[HardDependencyV21, ...]


@dataclass(frozen=True, slots=True)
class DictionaryEntryV21:
    """An opaque semantic namespace/key entry, never a source-text receipt."""

    namespace: str
    key: str
    commitment: str


@dataclass(frozen=True, slots=True)
class CapsuleRepresentativeV21:
    """A bounded Capsule feature; it has no source-body or receipt field."""

    feature_id: str
    feature_commitment: str
    weight: float


@dataclass(frozen=True, slots=True)
class CapsuleSegmentV21:
    """A bounded, non-prose fold with direct-parent commitments only."""

    segment_id: str
    parent_ids: tuple[str, ...]
    input_commitment_root: str
    folded_commitment_root: str
    namespace: str
    generation_start: int
    generation_end: int
    source_count: int
    role_counts: tuple[tuple[CapsuleRoleV21, int], ...]
    representatives: tuple[CapsuleRepresentativeV21, ...]
    coverage_vector: tuple[int, ...]
    bridge_vector: tuple[int, ...]
    loss_class: LossClassV21
    budget_used: int
    fold_policy_hash: str


@dataclass(frozen=True, slots=True)
class FoldBarrierV21:
    """Irreversible namespace/incarnation boundary for folded history."""

    namespace: str
    incarnation: int
    folded_through_high_water: int
    cumulative_root: str


@dataclass(frozen=True, slots=True)
class SparseWeightPolicyV21:
    """One default plus bounded sparse overrides, never per-source weight rows."""

    default_weight: float
    overrides: tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class LossLedgerV21:
    """Fixed aggregate loss state without event or source-recovery history."""

    role_counts: tuple[tuple[CapsuleRoleV21, int], ...]
    cumulative_loss_root: str
    last_fold_generation: int


def folded_commitment_root_v21(
    *,
    segment_id: str,
    namespace: str,
    generation_start: int,
    generation_end: int,
    source_count: int,
    role_counts: tuple[tuple[CapsuleRoleV21, int], ...],
    representatives: tuple[CapsuleRepresentativeV21, ...],
    coverage_vector: tuple[int, ...],
    bridge_vector: tuple[int, ...],
    loss_class: LossClassV21,
    budget_used: int,
    fold_policy_hash: str,
) -> str:
    """Bind a folded root to every non-parent segment field in its V21 domain."""
    return _domain_hash_v21(
        _FOLDED_COMMITMENT_DOMAIN,
        {
            "segment_id": _require_domain_digest_v21(
                segment_id, _SEGMENT_ID_DOMAIN, "folded commitment segment_id"
            ),
            "namespace": _require_domain_digest_v21(
                namespace, _NAMESPACE_ID_DOMAIN, "folded commitment namespace"
            ),
            "generation_start": _require_nonnegative_int(
                generation_start, "folded commitment generation_start"
            ),
            "generation_end": _require_nonnegative_int(
                generation_end, "folded commitment generation_end"
            ),
            "source_count": _require_nonnegative_int(
                source_count, "folded commitment source_count"
            ),
            "role_counts": role_counts,
            "representatives": representatives,
            "coverage_vector": coverage_vector,
            "bridge_vector": bridge_vector,
            "loss_class": _require_loss_class_v21(
                loss_class, "folded commitment loss_class"
            ),
            "budget_used": _require_nonnegative_int(
                budget_used, "folded commitment budget_used"
            ),
            "fold_policy_hash": _require_domain_digest_v21(
                fold_policy_hash,
                _FOLD_POLICY_DOMAIN,
                "folded commitment fold_policy_hash",
            ),
        },
    )


def fold_barrier_root_v21(
    namespace: str, incarnation: int, folded_through_high_water: int
) -> str:
    """Construct a deterministic V21-domain sample barrier root for fixtures."""
    return _domain_hash_v21(
        _BARRIER_ROOT_DOMAIN,
        {
            "namespace": _require_domain_digest_v21(
                namespace, _NAMESPACE_ID_DOMAIN, "barrier root namespace"
            ),
            "incarnation": _require_nonnegative_int(
                incarnation, "barrier root incarnation"
            ),
            "folded_through_high_water": _require_nonnegative_int(
                folded_through_high_water, "barrier root high_water"
            ),
        },
    )


def loss_ledger_root_v21(
    role_counts: tuple[tuple[CapsuleRoleV21, int], ...], last_fold_generation: int
) -> str:
    """Construct a deterministic V21-domain sample ledger root for fixtures."""
    return _domain_hash_v21(
        _LEDGER_ROOT_DOMAIN,
        {
            "role_counts": role_counts,
            "last_fold_generation": _require_nonnegative_int(
                last_fold_generation, "loss ledger root generation"
            ),
        },
    )


@dataclass(frozen=True, slots=True)
class CapsuleStateV21:
    """Nominal schema-3 state; V2 state objects cannot inhabit this domain."""

    frame: ControlFrameV21
    bounds: ControlFoldBoundsV21
    exact_kernel: tuple[ExactRecordV21, ...]
    hot_cache: tuple[ExactRecordV21, ...]
    hot_frontier: tuple[str, ...]
    dictionary: tuple[DictionaryEntryV21, ...]
    capsule_segments: tuple[CapsuleSegmentV21, ...]
    fold_barriers: tuple[FoldBarrierV21, ...]
    sparse_weight_policy: SparseWeightPolicyV21
    loss_ledger: LossLedgerV21


@dataclass(frozen=True, slots=True)
class ResidentLayoutV21:
    """One C/B/R layout: body-elided canonical control plus raw source bytes."""

    control_bytes: int
    source_body_bytes: int
    resident_bytes: int


def _body_elided_record_v21(record: ExactRecordV21) -> ExactRecordV21:
    return replace(record, body="")


def body_elided_state_v21(state: CapsuleStateV21) -> CapsuleStateV21:
    """Return the C-layout projection; it is for byte accounting, not validation."""
    if type(state) is not CapsuleStateV21:
        raise TypeError("V21 body-elided layout requires nominal CapsuleStateV21")
    return replace(
        state,
        exact_kernel=tuple(
            _body_elided_record_v21(record) for record in state.exact_kernel
        ),
        hot_cache=tuple(_body_elided_record_v21(record) for record in state.hot_cache),
    )


def _resident_record_layout_unchecked_v21(
    records: tuple[ExactRecordV21, ...],
) -> ResidentLayoutV21:
    control_bytes = canonical_bytes_v21(
        tuple(_body_elided_record_v21(record) for record in records)
    )
    source_body_bytes = sum(len(record.body.encode("utf-8")) for record in records)
    return ResidentLayoutV21(
        control_bytes=control_bytes,
        source_body_bytes=source_body_bytes,
        resident_bytes=control_bytes + source_body_bytes,
    )


def _resident_layout_unchecked_v21(state: CapsuleStateV21) -> ResidentLayoutV21:
    control_bytes = canonical_bytes_v21(body_elided_state_v21(state))
    source_body_bytes = sum(
        len(record.body.encode("utf-8"))
        for record in state.exact_kernel + state.hot_cache
    )
    return ResidentLayoutV21(
        control_bytes=control_bytes,
        source_body_bytes=source_body_bytes,
        resident_bytes=control_bytes + source_body_bytes,
    )


def resident_record_layout_v21(
    records: tuple[ExactRecordV21, ...],
) -> ResidentLayoutV21:
    """Return C/B/R accounting for one exact or hot resident collection."""
    _validate_record_collection_v21(records, "resident layout")
    return _resident_record_layout_unchecked_v21(records)


def resident_layout_v21(state: CapsuleStateV21) -> ResidentLayoutV21:
    """Return the sole full-state C/B/R accounting primitive for V21."""
    validate_capsule_state_v21(state)
    return _resident_layout_unchecked_v21(state)


class QueryLabelV21(StrEnum):
    """Public labels, ordered from most conservative to most faithful."""

    RELEASED_MISS = "RELEASED_MISS"
    CAPSULE_APPROX = "CAPSULE_APPROX"
    EXACT = "EXACT"


@dataclass(frozen=True, slots=True)
class QueryRequirementV21:
    """One declared nonempty required contribution/key for a query."""

    record_id: str | None = None
    coverage_index: int | None = None

    def __post_init__(self) -> None:
        validate_query_requirement_v21(self)


def validate_query_requirement_v21(requirement: QueryRequirementV21) -> None:
    """Revalidate a nominal query selector at the execution boundary."""
    if type(requirement) is not QueryRequirementV21:
        raise TypeError("V21 query requirements require nominal QueryRequirementV21")
    has_record = requirement.record_id is not None
    has_coverage = requirement.coverage_index is not None
    if has_record == has_coverage:
        raise ValueError("a V21 query requirement needs exactly one selector")
    if has_record:
        _require_domain_digest_v21(
            requirement.record_id, _RECORD_ID_DOMAIN, "query record_id"
        )
    if has_coverage:
        _require_nonnegative_int(requirement.coverage_index, "query coverage_index")


class ReencodingStatusV21(StrEnum):
    """Transition-only outcome; this is intentionally not a query label."""

    COMMITTED = "COMMITTED"
    STALE_REJECTED = "STALE_REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass(frozen=True, slots=True)
class ReencodingCandidateV21:
    """A validation-only candidate; planning/encoding remain deferred."""

    base_state_hash: str
    proposed_state: CapsuleStateV21
    incoming_records: tuple[ExactRecordV21, ...]
    requested_budget: int
    target_high_water: int


@dataclass(frozen=True, slots=True)
class ReencodingResultV21:
    """Atomic V21 transition result; failures preserve the identical base object."""

    state: CapsuleStateV21
    status: ReencodingStatusV21
    consumed_delta: bool
    reason: str | None

    @property
    def committed(self) -> bool:
        return self.status is ReencodingStatusV21.COMMITTED


def _validate_record_v21(record: object, label: str) -> ExactRecordV21:
    if type(record) is not ExactRecordV21:
        raise ValueError(f"{label} records require nominal ExactRecordV21")
    nominal = cast(ExactRecordV21, record)
    record_id = _require_domain_digest_v21(
        nominal.record_id, _RECORD_ID_DOMAIN, f"{label} record_id"
    )
    namespace = _require_domain_digest_v21(
        nominal.namespace, _NAMESPACE_ID_DOMAIN, f"{label} namespace"
    )
    incarnation = _require_nonnegative_int(nominal.incarnation, f"{label} incarnation")
    as_of = _require_nonnegative_int(nominal.as_of, f"{label} as_of")
    commitment = _require_domain_digest_v21(
        nominal.commitment, _SOURCE_COMMITMENT_DOMAIN, f"{label} commitment"
    )
    body = _require_text(nominal.body, f"{label} body")
    core_required = _require_bool_v21(nominal.core_required, f"{label} core_required")
    active = _require_bool_v21(nominal.active, f"{label} active")
    if commitment != source_commitment_v21(
        record_id=record_id,
        namespace=namespace,
        incarnation=incarnation,
        as_of=as_of,
        body=body,
        core_required=core_required,
        active=active,
    ):
        raise ValueError(
            f"{label} commitment must bind source body and identity metadata"
        )
    if type(nominal.hard_depends_on) is not tuple:
        raise ValueError(f"{label} hard_depends_on must be a tuple")
    seen_targets: set[str] = set()
    for dependency in nominal.hard_depends_on:
        if type(dependency) is not HardDependencyV21:
            raise ValueError("hard dependency requires nominal HardDependencyV21")
        exact_dependency = cast(HardDependencyV21, dependency)
        _require_domain_digest_v21(
            exact_dependency.target_id, _RECORD_ID_DOMAIN, "hard dependency target_id"
        )
        _require_domain_digest_v21(
            exact_dependency.target_commitment,
            _SOURCE_COMMITMENT_DOMAIN,
            "hard dependency commitment",
        )
        if exact_dependency.target_id in seen_targets:
            raise ValueError("hard dependency target ids must be unique")
        seen_targets.add(exact_dependency.target_id)
    return nominal


def _validate_record_collection_v21(
    records: object, label: str
) -> dict[str, ExactRecordV21]:
    if type(records) is not tuple:
        raise ValueError(f"{label} must be a tuple")
    by_id: dict[str, ExactRecordV21] = {}
    for record in cast(tuple[object, ...], records):
        exact_record = _validate_record_v21(record, label)
        if not exact_record.active:
            raise ValueError(f"{label} cannot retain inactive records")
        if exact_record.record_id in by_id:
            raise ValueError(f"{label} record ids must be unique")
        by_id[exact_record.record_id] = exact_record
    return by_id


def _validate_dictionary_v21(dictionary: object, bounds: ControlFoldBoundsV21) -> None:
    if type(dictionary) is not tuple:
        raise ValueError("dictionary must be a tuple")
    entries = cast(tuple[object, ...], dictionary)
    if len(entries) > bounds.max_dictionary_entries:
        raise ValueError("dictionary entry bound exceeded")
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if type(entry) is not DictionaryEntryV21:
            raise ValueError("dictionary entries cannot contain source text")
        nominal = cast(DictionaryEntryV21, entry)
        namespace = _require_domain_digest_v21(
            nominal.namespace, _NAMESPACE_ID_DOMAIN, "dictionary namespace"
        )
        key = _require_domain_digest_v21(
            nominal.key, _DICTIONARY_ID_DOMAIN, "dictionary key"
        )
        commitment = _require_domain_digest_v21(
            nominal.commitment,
            _DICTIONARY_COMMITMENT_DOMAIN,
            "dictionary commitment",
        )
        if commitment != dictionary_commitment_v21(namespace, key):
            raise ValueError("dictionary commitment must bind namespace and key")
        identity = (namespace, key)
        if identity in seen:
            raise ValueError("dictionary entries must be unique")
        seen.add(identity)
    if canonical_bytes_v21(cast(tuple[DictionaryEntryV21, ...], dictionary)) > (
        bounds.max_dictionary_bytes
    ):
        raise ValueError("dictionary byte bound exceeded")


def _validate_role_counts_v21(value: object, label: str) -> None:
    if type(value) is not tuple:
        raise ValueError(f"{label} must be a tuple")
    items = cast(tuple[object, ...], value)
    pairs: list[tuple[CapsuleRoleV21, int]] = []
    for item in items:
        if type(item) is not tuple or len(item) != 2:
            raise ValueError(f"{label} must contain (role, count) pairs")
        pair = cast(tuple[object, object], item)
        role = _require_role_v21(pair[0], f"{label} role")
        count = _require_nonnegative_int(pair[1], f"{label} count")
        pairs.append((role, count))
    if tuple(pairs) != tuple(sorted(pairs, key=lambda item: item[0].value)):
        raise ValueError(f"{label} must be canonically sorted")
    if len({role for role, _ in pairs}) != len(pairs):
        raise ValueError(f"{label} roles must be unique")


def _validate_segment_v21(
    segment: object, bounds: ControlFoldBoundsV21, frame: ControlFrameV21
) -> CapsuleSegmentV21:
    if type(segment) is not CapsuleSegmentV21:
        raise ValueError("capsule segments require nominal CapsuleSegmentV21")
    nominal = cast(CapsuleSegmentV21, segment)
    segment_id = _require_domain_digest_v21(
        nominal.segment_id, _SEGMENT_ID_DOMAIN, "segment_id"
    )
    input_commitment_root = _require_domain_digest_v21(
        nominal.input_commitment_root,
        _SEGMENT_INPUT_DOMAIN,
        "segment input_commitment_root",
    )
    folded_commitment_root = _require_domain_digest_v21(
        nominal.folded_commitment_root,
        _FOLDED_COMMITMENT_DOMAIN,
        "segment folded_commitment_root",
    )
    namespace = _require_domain_digest_v21(
        nominal.namespace, _NAMESPACE_ID_DOMAIN, "segment namespace"
    )
    loss_class = _require_loss_class_v21(nominal.loss_class, "segment loss_class")
    fold_policy_hash = _require_domain_digest_v21(
        nominal.fold_policy_hash, _FOLD_POLICY_DOMAIN, "segment fold_policy_hash"
    )
    generation_start = _require_nonnegative_int(
        nominal.generation_start, "segment generation_start"
    )
    generation_end = _require_nonnegative_int(
        nominal.generation_end, "segment generation_end"
    )
    if generation_start > generation_end or generation_end > frame.generation:
        raise ValueError("segment generation range is invalid")
    _require_nonnegative_int(nominal.source_count, "segment source_count")
    _require_nonnegative_int(nominal.budget_used, "segment budget_used")
    if type(nominal.parent_ids) is not tuple:
        raise ValueError("segment parent_ids must be a tuple")
    for parent_id in nominal.parent_ids:
        _require_domain_digest_v21(parent_id, _SEGMENT_ID_DOMAIN, "segment parent_id")
    if nominal.parent_ids != tuple(sorted(set(nominal.parent_ids))):
        raise ValueError("segment parent ids must be canonically sorted and unique")
    if len(nominal.parent_ids) > bounds.max_direct_parent_ids:
        raise ValueError("segment direct parent bound exceeded")
    if sum(len(parent_id.encode("utf-8")) for parent_id in nominal.parent_ids) > (
        bounds.max_parent_id_bytes
    ):
        raise ValueError("segment parent-id byte bound exceeded")
    _validate_role_counts_v21(nominal.role_counts, "segment role_counts")
    if type(nominal.representatives) is not tuple:
        raise ValueError("segment representatives must be a tuple")
    if len(nominal.representatives) > bounds.max_representatives_per_segment:
        raise ValueError("segment representative bound exceeded")
    for representative in nominal.representatives:
        if type(representative) is not CapsuleRepresentativeV21:
            raise ValueError("segment representative cannot contain source text")
        feature = cast(CapsuleRepresentativeV21, representative)
        feature_id = _require_domain_digest_v21(
            feature.feature_id, _REPRESENTATIVE_ID_DOMAIN, "segment feature_id"
        )
        feature_commitment = _require_domain_digest_v21(
            feature.feature_commitment,
            _REPRESENTATIVE_COMMITMENT_DOMAIN,
            "segment feature_commitment",
        )
        if feature_commitment != representative_commitment_v21(feature_id):
            raise ValueError("segment feature_commitment must bind feature_id")
        _require_finite_number(feature.weight, "segment feature weight")
    if type(nominal.coverage_vector) is not tuple:
        raise ValueError("segment coverage vector must be a tuple")
    if len(nominal.coverage_vector) != bounds.coverage_vector_size:
        raise ValueError("segment coverage vector size differs from frozen bounds")
    for bit in nominal.coverage_vector:
        if type(bit) is not int or bit not in (0, 1):
            raise ValueError("segment coverage vector must contain binary integers")
    if type(nominal.bridge_vector) is not tuple:
        raise ValueError("segment bridge vector must be a tuple")
    if len(nominal.bridge_vector) != bounds.bridge_vector_size:
        raise ValueError("segment bridge vector size differs from frozen bounds")
    for anchor in nominal.bridge_vector:
        _require_nonnegative_int(anchor, "segment bridge vector entry")
    expected_folded_root = folded_commitment_root_v21(
        segment_id=segment_id,
        namespace=namespace,
        generation_start=generation_start,
        generation_end=generation_end,
        source_count=nominal.source_count,
        role_counts=nominal.role_counts,
        representatives=nominal.representatives,
        coverage_vector=nominal.coverage_vector,
        bridge_vector=nominal.bridge_vector,
        loss_class=loss_class,
        budget_used=nominal.budget_used,
        fold_policy_hash=fold_policy_hash,
    )
    if folded_commitment_root != expected_folded_root:
        raise ValueError("segment folded commitment must bind segment metadata")
    if not nominal.parent_ids:
        expected_input_root = segment_input_commitment_root_v21(
            (),
            folded_commitment_root=folded_commitment_root,
            generation_start=generation_start,
            generation_end=generation_end,
            fold_policy_hash=fold_policy_hash,
        )
        if input_commitment_root != expected_input_root:
            raise ValueError("segment input commitment must bind its empty parent set")
    return nominal


def _validate_barriers_v21(
    barriers: object, frame: ControlFrameV21, bounds: ControlFoldBoundsV21
) -> None:
    if type(barriers) is not tuple:
        raise ValueError("fold barriers must be a tuple")
    entries = cast(tuple[object, ...], barriers)
    if len(entries) > bounds.max_barriers:
        raise ValueError("fold barrier bound exceeded")
    seen: set[tuple[str, int]] = set()
    for barrier in entries:
        if type(barrier) is not FoldBarrierV21:
            raise ValueError("fold barriers require nominal FoldBarrierV21")
        nominal = cast(FoldBarrierV21, barrier)
        namespace = _require_domain_digest_v21(
            nominal.namespace, _NAMESPACE_ID_DOMAIN, "fold barrier namespace"
        )
        incarnation = _require_nonnegative_int(
            nominal.incarnation, "fold barrier incarnation"
        )
        high_water = _require_nonnegative_int(
            nominal.folded_through_high_water, "fold barrier high_water"
        )
        _require_domain_digest_v21(
            nominal.cumulative_root,
            _BARRIER_ROOT_DOMAIN,
            "fold barrier cumulative_root",
        )
        if high_water > frame.high_water:
            raise ValueError("fold barrier cannot exceed frame high_water")
        identity = (namespace, incarnation)
        if identity in seen:
            raise ValueError("fold barriers must be unique per namespace/incarnation")
        seen.add(identity)


def _validate_incarnation_dominance_v21(
    records: Mapping[str, ExactRecordV21], barriers: tuple[FoldBarrierV21, ...]
) -> None:
    highest_incarnation_by_namespace: dict[str, int] = {}
    for barrier in barriers:
        highest_incarnation_by_namespace[barrier.namespace] = max(
            highest_incarnation_by_namespace.get(barrier.namespace, -1),
            barrier.incarnation,
        )
    for record in records.values():
        highest_incarnation_by_namespace[record.namespace] = max(
            highest_incarnation_by_namespace.get(record.namespace, -1),
            record.incarnation,
        )
    for record in records.values():
        highest_incarnation = highest_incarnation_by_namespace[record.namespace]
        if record.incarnation < highest_incarnation:
            raise ValueError(
                "resident record incarnation is below its namespace maximum"
            )


def _validate_sparse_weights_v21(policy: object, bounds: ControlFoldBoundsV21) -> None:
    if type(policy) is not SparseWeightPolicyV21:
        raise ValueError("sparse weights require nominal SparseWeightPolicyV21")
    nominal = cast(SparseWeightPolicyV21, policy)
    _require_finite_number(nominal.default_weight, "sparse default weight")
    if type(nominal.overrides) is not tuple:
        raise ValueError("sparse overrides must be a tuple")
    if len(nominal.overrides) > bounds.max_sparse_overrides:
        raise ValueError("sparse override bound exceeded")
    pairs: list[tuple[str, float]] = []
    for override in nominal.overrides:
        if type(override) is not tuple or len(override) != 2:
            raise ValueError("sparse overrides must contain (key, weight) pairs")
        pair = cast(tuple[object, object], override)
        key = _require_domain_digest_v21(
            pair[0], _DICTIONARY_ID_DOMAIN, "sparse override key"
        )
        weight = _require_finite_number(pair[1], "sparse override weight")
        pairs.append((key, weight))
    if tuple(key for key, _ in pairs) != tuple(sorted(key for key, _ in pairs)):
        raise ValueError("sparse override keys must be canonically sorted")
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("sparse override keys must be unique")


def _validate_loss_ledger_v21(
    ledger: object, bounds: ControlFoldBoundsV21, frame: ControlFrameV21
) -> None:
    if type(ledger) is not LossLedgerV21:
        raise ValueError("loss ledger requires nominal LossLedgerV21")
    nominal = cast(LossLedgerV21, ledger)
    _validate_role_counts_v21(nominal.role_counts, "loss ledger role_counts")
    _require_domain_digest_v21(
        nominal.cumulative_loss_root,
        _LEDGER_ROOT_DOMAIN,
        "loss ledger cumulative_root",
    )
    generation = _require_nonnegative_int(
        nominal.last_fold_generation, "loss ledger last_fold_generation"
    )
    if generation > frame.generation:
        raise ValueError("loss ledger generation exceeds frame generation")
    if canonical_bytes_v21(nominal) > bounds.loss_ledger_bytes:
        raise ValueError("fixed loss ledger byte bound exceeded")


def _validate_resident_segment_lineage_v21(
    segments: tuple[CapsuleSegmentV21, ...],
) -> None:
    by_id = {segment.segment_id: segment for segment in segments}
    if len(by_id) != len(segments):
        raise ValueError("capsule segment ids must be unique")
    for segment in segments:
        if segment.segment_id in segment.parent_ids:
            raise ValueError("segment lineage cannot contain a self parent")
        for parent_id in segment.parent_ids:
            if parent_id in by_id:
                raise ValueError(
                    "resident state cannot contain both a child and its named parent"
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(segment_id: str) -> None:
        if segment_id in visited:
            return
        if segment_id in visiting:
            raise ValueError("capsule segment lineage must be acyclic")
        visiting.add(segment_id)
        segment = by_id[segment_id]
        for parent_id in segment.parent_ids:
            if parent_id in by_id:
                visit(parent_id)
        visiting.remove(segment_id)
        visited.add(segment_id)

    for segment_id in by_id:
        visit(segment_id)


def validate_capsule_state_v21(state: CapsuleStateV21) -> None:
    """Validate every schema-3 invariant before bytes, hashes, or labels exist."""
    if type(state) is not CapsuleStateV21:
        raise TypeError("V21 state requires nominal CapsuleStateV21")
    validate_control_frame_v21(state.frame, state.bounds)
    exact_by_id = _validate_record_collection_v21(state.exact_kernel, "exact kernel")
    hot_by_id = _validate_record_collection_v21(state.hot_cache, "hot cache")
    if set(exact_by_id).intersection(hot_by_id):
        raise ValueError("exact kernel and hot cache record ids must not overlap")
    all_records = {**exact_by_id, **hot_by_id}
    if any(record.as_of > state.frame.high_water for record in all_records.values()):
        raise ValueError("frame high_water must cover every resident exact record")

    required_exact_ids: set[str] = set()
    for record in all_records.values():
        if record.core_required:
            required_exact_ids.add(record.record_id)
        for dependency in record.hard_depends_on:
            required_exact_ids.add(record.record_id)
            required_exact_ids.add(dependency.target_id)
            target = exact_by_id.get(dependency.target_id)
            if target is None:
                raise ValueError("hard dependency target lacks an exact resident body")
            if target.commitment != dependency.target_commitment:
                raise ValueError("hard dependency target commitment does not match")
    if set(exact_by_id) != required_exact_ids:
        raise ValueError("exact kernel must equal the active hard dependency closure")
    if len(exact_by_id) > state.bounds.max_exact_kernel_records:
        raise ValueError("exact kernel record bound exceeded")
    if (
        _resident_record_layout_unchecked_v21(state.exact_kernel).resident_bytes
        > state.bounds.max_exact_kernel_bytes
    ):
        raise ValueError("exact kernel byte bound exceeded")
    if len(hot_by_id) > state.bounds.max_hot_records:
        raise ValueError("hot cache record bound exceeded")
    if (
        _resident_record_layout_unchecked_v21(state.hot_cache).resident_bytes
        > state.bounds.max_hot_bytes
    ):
        raise ValueError("hot cache byte bound exceeded")

    if type(state.hot_frontier) is not tuple:
        raise ValueError("hot frontier must be a tuple")
    if len(state.hot_frontier) > state.bounds.max_hot_frontier_entries:
        raise ValueError("hot frontier entry bound exceeded")
    if canonical_bytes_v21(state.hot_frontier) > state.bounds.max_hot_frontier_bytes:
        raise ValueError("hot frontier byte bound exceeded")
    if len(set(state.hot_frontier)) != len(state.hot_frontier):
        raise ValueError("hot frontier keys must be unique")
    for key in state.hot_frontier:
        _require_domain_digest_v21(key, _DICTIONARY_ID_DOMAIN, "hot frontier key")

    _validate_dictionary_v21(state.dictionary, state.bounds)
    if type(state.capsule_segments) is not tuple:
        raise ValueError("capsule segments must be a tuple")
    if len(state.capsule_segments) > state.bounds.max_segments:
        raise ValueError("capsule segment bound exceeded")
    validated_segments = tuple(
        _validate_segment_v21(segment, state.bounds, state.frame)
        for segment in state.capsule_segments
    )
    _validate_resident_segment_lineage_v21(validated_segments)
    _validate_barriers_v21(state.fold_barriers, state.frame, state.bounds)
    _validate_incarnation_dominance_v21(all_records, state.fold_barriers)
    _validate_sparse_weights_v21(state.sparse_weight_policy, state.bounds)
    _validate_loss_ledger_v21(state.loss_ledger, state.bounds, state.frame)
    if (
        _resident_layout_unchecked_v21(state).resident_bytes
        > state.frame.accepted_budget
    ):
        raise ValueError("resident state exceeds its accepted budget")


def state_hash_v21(state: CapsuleStateV21) -> str:
    """Return a full domain-separated schema-3 state hash."""
    validate_capsule_state_v21(state)
    return _domain_hash_v21(_STATE_HASH_DOMAIN, state)


def segment_input_commitment_root_v21(
    direct_parents: tuple[CapsuleSegmentV21, ...],
    *,
    folded_commitment_root: str,
    generation_start: int,
    generation_end: int,
    fold_policy_hash: str,
) -> str:
    """Bind one child to the canonical bytes of its direct base parents."""
    if type(direct_parents) is not tuple:
        raise TypeError("direct parents must be a tuple")
    parents = cast(tuple[object, ...], direct_parents)
    nominal_parents: list[CapsuleSegmentV21] = []
    for parent in parents:
        if type(parent) is not CapsuleSegmentV21:
            raise TypeError("direct parents require nominal CapsuleSegmentV21")
        nominal_parents.append(cast(CapsuleSegmentV21, parent))
    if tuple(parent.segment_id for parent in nominal_parents) != tuple(
        sorted(parent.segment_id for parent in nominal_parents)
    ):
        raise ValueError("direct parents must be canonically sorted")
    _require_domain_digest_v21(
        folded_commitment_root,
        _FOLDED_COMMITMENT_DOMAIN,
        "folded commitment root",
    )
    _require_nonnegative_int(generation_start, "segment generation_start")
    _require_nonnegative_int(generation_end, "segment generation_end")
    _require_domain_digest_v21(
        fold_policy_hash, _FOLD_POLICY_DOMAIN, "fold policy hash"
    )
    return _domain_hash_v21(
        _SEGMENT_INPUT_DOMAIN,
        {
            "parents": tuple(nominal_parents),
            "folded_commitment_root": folded_commitment_root,
            "generation_start": generation_start,
            "generation_end": generation_end,
            "fold_policy_hash": fold_policy_hash,
        },
    )


def _rollback_v21(base_state: CapsuleStateV21, reason: str) -> ReencodingResultV21:
    return ReencodingResultV21(
        state=base_state,
        status=ReencodingStatusV21.ROLLED_BACK,
        consumed_delta=False,
        reason=reason,
    )


def _stale_rejection_v21(
    base_state: CapsuleStateV21, reason: str
) -> ReencodingResultV21:
    return ReencodingResultV21(
        state=base_state,
        status=ReencodingStatusV21.STALE_REJECTED,
        consumed_delta=False,
        reason=reason,
    )


def _candidate_lineage_failure_v21(
    base_state: CapsuleStateV21, proposed_state: CapsuleStateV21
) -> str | None:
    base_segments = {
        segment.segment_id: segment for segment in base_state.capsule_segments
    }
    proposed_segments = {
        segment.segment_id: segment for segment in proposed_state.capsule_segments
    }
    shared_ids = set(base_segments).intersection(proposed_segments)
    for segment_id in shared_ids:
        if canonical_json_v21(base_segments[segment_id]) != canonical_json_v21(
            proposed_segments[segment_id]
        ):
            return "resident base segment canonical bytes changed"

    new_segments = tuple(
        segment
        for segment_id, segment in proposed_segments.items()
        if segment_id not in base_segments
    )
    consumed_parents: set[str] = set()
    for segment in new_segments:
        if not segment.parent_ids:
            return "new segment requires a direct resident base parent in V21-002"
        parents: list[CapsuleSegmentV21] = []
        for parent_id in segment.parent_ids:
            parent = base_segments.get(parent_id)
            if parent is None:
                return "segment parent is not a direct resident base parent"
            if parent_id in consumed_parents:
                return "a direct base parent may be consumed by only one new child"
            if parent_id in proposed_segments:
                return "direct base parent must be consumed by its child"
            if parent.generation_end >= segment.generation_start:
                return "direct base parent must precede its child"
            parents.append(parent)
            consumed_parents.add(parent_id)
        expected_root = segment_input_commitment_root_v21(
            tuple(parents),
            folded_commitment_root=segment.folded_commitment_root,
            generation_start=segment.generation_start,
            generation_end=segment.generation_end,
            fold_policy_hash=segment.fold_policy_hash,
        )
        if segment.input_commitment_root != expected_root:
            return "segment input commitment root does not bind direct parent canonical bytes"

    removed_segments = set(base_segments).difference(proposed_segments)
    if removed_segments != consumed_parents:
        return "base segments may only be removed when atomically consumed by a child"
    return None


def _candidate_monotonic_failure_v21(
    base_state: CapsuleStateV21, proposed_state: CapsuleStateV21
) -> str | None:
    if proposed_state.fold_barriers != base_state.fold_barriers:
        return "candidate cannot change fold barriers without V21-003 evidence"
    if proposed_state.loss_ledger != base_state.loss_ledger:
        return "candidate cannot change the loss ledger without V21-003 evidence"
    base_barriers = {
        (barrier.namespace, barrier.incarnation): barrier
        for barrier in base_state.fold_barriers
    }
    proposed_barriers = {
        (barrier.namespace, barrier.incarnation): barrier
        for barrier in proposed_state.fold_barriers
    }
    highest_base_incarnation: dict[str, int] = {}
    for barrier in base_state.fold_barriers:
        highest_base_incarnation[barrier.namespace] = max(
            highest_base_incarnation.get(barrier.namespace, -1),
            barrier.incarnation,
        )
    for barrier in proposed_state.fold_barriers:
        base_highest = highest_base_incarnation.get(barrier.namespace)
        if base_highest is not None and barrier.incarnation < base_highest:
            return "candidate cannot reintroduce a lower fold barrier incarnation"
    for identity, base_barrier in base_barriers.items():
        proposed_barrier = proposed_barriers.get(identity)
        if proposed_barrier is None:
            return "candidate cannot remove an existing fold barrier"
        if (
            proposed_barrier.folded_through_high_water
            < base_barrier.folded_through_high_water
        ):
            return "candidate fold barriers must be monotonic"

    base_ledger = base_state.loss_ledger
    proposed_ledger = proposed_state.loss_ledger
    if proposed_ledger.last_fold_generation < base_ledger.last_fold_generation:
        return "candidate loss ledger generation moved backwards"
    proposed_counts = dict(proposed_ledger.role_counts)
    for role, count in base_ledger.role_counts:
        if proposed_counts.get(role, 0) < count:
            return "candidate loss ledger role counts must be monotonic"
    return None


def _candidate_resident_record_failure_v21(
    base_state: CapsuleStateV21,
    proposed_state: CapsuleStateV21,
    incoming_by_id: dict[str, ExactRecordV21],
) -> str | None:
    base_by_id = {
        record.record_id: record
        for record in base_state.exact_kernel + base_state.hot_cache
    }
    proposed_by_id = {
        record.record_id: record
        for record in proposed_state.exact_kernel + proposed_state.hot_cache
    }
    for record_id, base_record in base_by_id.items():
        proposed_record = proposed_by_id.get(record_id)
        if proposed_record is None:
            return "candidate cannot remove a base resident body without V21 planner authority"
        if proposed_record == base_record:
            continue
        incoming_record = incoming_by_id.get(record_id)
        if incoming_record != proposed_record:
            return "candidate resident body or commitment lacks an authorized origin"
    for proposed_record in proposed_by_id.values():
        base_record = base_by_id.get(proposed_record.record_id)
        if base_record == proposed_record:
            continue
        incoming_record = incoming_by_id.get(proposed_record.record_id)
        if incoming_record == proposed_record:
            continue
        return "candidate resident body or commitment lacks an authorized origin"
    for record_id, incoming_record in incoming_by_id.items():
        if proposed_by_id.get(record_id) != incoming_record:
            return "candidate incoming record must become a proposed resident"
    return None


def validate_reencoding_candidate_v21(
    base_state: CapsuleStateV21, candidate: ReencodingCandidateV21
) -> ReencodingResultV21:
    """Validate a candidate atomically without implementing a reencoding planner."""
    if type(base_state) is not CapsuleStateV21:
        raise TypeError("V21 base requires nominal CapsuleStateV21")
    if type(candidate) is not ReencodingCandidateV21:
        raise TypeError("V21 candidate requires nominal ReencodingCandidateV21")
    validate_capsule_state_v21(base_state)
    if candidate.base_state_hash != state_hash_v21(base_state):
        return _rollback_v21(
            base_state, "candidate base hash is outside the V21 state domain"
        )
    try:
        requested_budget = _require_nonnegative_int(
            candidate.requested_budget, "candidate requested_budget"
        )
        target_high_water = _require_nonnegative_int(
            candidate.target_high_water, "candidate target_high_water"
        )
    except ValueError as error:
        return _rollback_v21(base_state, str(error))
    if type(candidate.incoming_records) is not tuple:
        return _rollback_v21(base_state, "candidate incoming records are not a tuple")
    incoming_by_id: dict[str, ExactRecordV21] = {}
    for record in candidate.incoming_records:
        try:
            incoming = _validate_record_v21(record, "candidate incoming")
        except (TypeError, ValueError) as error:
            return _rollback_v21(base_state, f"invalid incoming record: {error}")
        for barrier in base_state.fold_barriers:
            if incoming.namespace == barrier.namespace:
                if incoming.incarnation < barrier.incarnation:
                    return _stale_rejection_v21(
                        base_state,
                        "incoming record is below the namespace barrier incarnation",
                    )
                if (
                    incoming.incarnation == barrier.incarnation
                    and incoming.as_of <= barrier.folded_through_high_water
                ):
                    return _stale_rejection_v21(
                        base_state,
                        "incoming record is at or below its fold barrier",
                    )
        if incoming.as_of <= base_state.frame.high_water:
            return _rollback_v21(
                base_state, "incoming record is not newer than the base high_water"
            )
        if incoming.as_of > target_high_water:
            return _rollback_v21(
                base_state, "incoming record exceeds the candidate target high_water"
            )
        if incoming.record_id in incoming_by_id:
            return _rollback_v21(
                base_state, "candidate incoming record ids must be unique"
            )
        incoming_by_id[incoming.record_id] = incoming

    highest_incarnation_by_namespace: dict[str, int] = {}
    for resident in base_state.exact_kernel + base_state.hot_cache:
        highest_incarnation_by_namespace[resident.namespace] = max(
            highest_incarnation_by_namespace.get(resident.namespace, -1),
            resident.incarnation,
        )
    for barrier in base_state.fold_barriers:
        highest_incarnation_by_namespace[barrier.namespace] = max(
            highest_incarnation_by_namespace.get(barrier.namespace, -1),
            barrier.incarnation,
        )
    for incoming in incoming_by_id.values():
        highest_incarnation_by_namespace[incoming.namespace] = max(
            highest_incarnation_by_namespace.get(incoming.namespace, -1),
            incoming.incarnation,
        )
    for incoming in incoming_by_id.values():
        if incoming.incarnation < highest_incarnation_by_namespace[incoming.namespace]:
            return _stale_rejection_v21(
                base_state,
                "incoming record is below the known namespace incarnation",
            )

    if type(candidate.proposed_state) is not CapsuleStateV21:
        return _rollback_v21(base_state, "candidate proposed state is not nominal V21")
    proposed_state = candidate.proposed_state
    try:
        validate_capsule_state_v21(proposed_state)
    except (TypeError, ValueError) as error:
        return _rollback_v21(base_state, f"candidate state is invalid: {error}")
    if proposed_state.bounds != base_state.bounds:
        return _rollback_v21(
            base_state, "candidate cannot replace frozen control bounds"
        )
    if (
        proposed_state.frame.reencoding_policy_hash
        != base_state.frame.reencoding_policy_hash
    ):
        return _rollback_v21(
            base_state, "candidate cannot replace the reencoding policy"
        )
    if proposed_state.frame.generation < base_state.frame.generation:
        return _rollback_v21(base_state, "candidate frame generation moved backwards")
    if proposed_state.frame.high_water < base_state.frame.high_water:
        return _rollback_v21(base_state, "candidate frame high_water moved backwards")
    if target_high_water < proposed_state.frame.high_water:
        return _rollback_v21(
            base_state, "candidate target high_water is below its frame"
        )
    if any(
        record.as_of > proposed_state.frame.high_water
        for record in incoming_by_id.values()
    ):
        return _rollback_v21(
            base_state, "incoming record exceeds the proposed frame high_water"
        )
    if requested_budget > proposed_state.frame.accepted_budget:
        return _rollback_v21(base_state, "candidate requested budget exceeds its frame")
    if _resident_layout_unchecked_v21(proposed_state).resident_bytes > requested_budget:
        return _rollback_v21(
            base_state, "candidate resident bytes exceed requested budget"
        )
    monotonic_failure = _candidate_monotonic_failure_v21(base_state, proposed_state)
    if monotonic_failure is not None:
        return _rollback_v21(base_state, monotonic_failure)
    resident_record_failure = _candidate_resident_record_failure_v21(
        base_state, proposed_state, incoming_by_id
    )
    if resident_record_failure is not None:
        return _rollback_v21(base_state, resident_record_failure)
    lineage_failure = _candidate_lineage_failure_v21(base_state, proposed_state)
    if lineage_failure is not None:
        return _rollback_v21(base_state, lineage_failure)
    return ReencodingResultV21(
        state=proposed_state,
        status=ReencodingStatusV21.COMMITTED,
        consumed_delta=True,
        reason=None,
    )


def query_label_v21(
    state: CapsuleStateV21, required: tuple[QueryRequirementV21, ...]
) -> QueryLabelV21:
    """Return the conservative label for a nonempty required-set query."""
    validate_capsule_state_v21(state)
    if type(required) is not tuple or not required:
        raise ValueError("a nonempty required contribution set is required")
    exact_ids = {record.record_id for record in state.exact_kernel + state.hot_cache}
    approximate_feature_ids = {
        representative.feature_id
        for segment in state.capsule_segments
        for representative in segment.representatives
    }
    saw_approximation = False
    for requirement in required:
        if type(requirement) is not QueryRequirementV21:
            raise TypeError(
                "V21 query requirements require nominal QueryRequirementV21"
            )
        nominal = cast(QueryRequirementV21, requirement)
        validate_query_requirement_v21(nominal)
        if nominal.record_id is not None:
            if nominal.record_id in exact_ids:
                continue
            if nominal.record_id in approximate_feature_ids:
                saw_approximation = True
                continue
            return QueryLabelV21.RELEASED_MISS
        if nominal.coverage_index is None:
            raise ValueError("query requirement lacks a selector")
        if any(
            segment.coverage_vector[nominal.coverage_index] == 1
            for segment in state.capsule_segments
            if nominal.coverage_index < len(segment.coverage_vector)
        ):
            saw_approximation = True
            continue
        return QueryLabelV21.RELEASED_MISS
    if saw_approximation:
        return QueryLabelV21.CAPSULE_APPROX
    return QueryLabelV21.EXACT
