"""Self-contained logical and resident contracts for CRM state version 2."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Self

from crm_experiment.canonical import canonical_json, sha256_text, utf8_bytes
from crm_experiment.contracts import AtomRole, AtomStatus

_SOURCE_PAYLOAD_DOMAIN = "crm-v2-source-payload/v1"
_SOURCE_RECORD_DOMAIN = "crm-v2-source-record/v2"
_WEIGHT_POLICY_DOMAIN = "crm-v2-weight-policy/v1"
_RECOMPOSITION_POLICY_DOMAIN = "crm-v2-recomposition-policy/v1"
_SOURCE_ID_PREFIX = "sha256:"
_PACKING_CODEC_V2 = "dmc1-lcp-lcs-v1"
_PACKED_CONTEXT_KIND_V2 = "packed-context-dmc1-v1"


def _valid_sha256(value: str) -> bool:
    return (
        len(value) == 64
        and value.lower() == value
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_source_id_v2(value: str) -> bool:
    return value.startswith(_SOURCE_ID_PREFIX) and _valid_sha256(
        value.removeprefix(_SOURCE_ID_PREFIX)
    )


def canonical_affixes_v2(texts: tuple[str, ...]) -> tuple[str, str]:
    """Return the unique LCP then non-overlapping LCS factorization."""
    if len(texts) < 2:
        raise ValueError("canonical factoring requires at least two texts")
    if any(not isinstance(text, str) for text in texts):
        raise ValueError("packed texts must be strings")

    first = texts[0]
    prefix_length = min(len(text) for text in texts)
    for index in range(prefix_length):
        character = first[index]
        if any(text[index] != character for text in texts[1:]):
            prefix_length = index
            break
    common_prefix = first[:prefix_length]
    remainders = tuple(text[prefix_length:] for text in texts)

    first_remainder = remainders[0]
    suffix_length = min(len(remainder) for remainder in remainders)
    for offset in range(1, suffix_length + 1):
        character = first_remainder[-offset]
        if any(remainder[-offset] != character for remainder in remainders[1:]):
            suffix_length = offset - 1
            break
    common_suffix = first_remainder[-suffix_length:] if suffix_length else ""
    return common_prefix, common_suffix


def _source_payload_hash_fields_v2(
    *,
    text: str,
    provenance: tuple[str, ...],
) -> str:
    return sha256_text(
        canonical_json(
            {
                "domain": _SOURCE_PAYLOAD_DOMAIN,
                "provenance": provenance,
                "text": text,
            }
        )
    )


def _source_record_hash_fields_v2(
    *,
    semantic_keys: tuple[str, ...],
    role: AtomRole,
    status: AtomStatus,
    revision: int,
    as_of: int,
    exact: bool,
    depends_on: tuple[str, ...],
    core_required: bool,
    payload_hash: str,
) -> str:
    return sha256_text(
        canonical_json(
            {
                "as_of": as_of,
                "core_required": core_required,
                "depends_on": depends_on,
                "domain": _SOURCE_RECORD_DOMAIN,
                "exact": exact,
                "payload_hash": payload_hash,
                "revision": revision,
                "role": role,
                "semantic_keys": semantic_keys,
                "status": status,
            }
        )
    )


def _validate_source_metadata(
    *,
    semantic_keys: tuple[str, ...],
    revision: int,
    as_of: int,
    provenance: tuple[str, ...],
    depends_on: tuple[str, ...],
) -> None:
    if not semantic_keys:
        raise ValueError("semantic_keys must not be empty")
    if any(not key for key in semantic_keys):
        raise ValueError("semantic_keys must not contain empty values")
    if semantic_keys != tuple(sorted(set(semantic_keys))):
        raise ValueError("semantic_keys must be unique and sorted")
    if revision <= 0:
        raise ValueError("revision must be positive")
    if as_of < 0:
        raise ValueError("as_of must be nonnegative")
    if not provenance:
        raise ValueError("provenance must not be empty")
    if any(not item for item in provenance):
        raise ValueError("provenance must not contain empty values")
    if provenance != tuple(sorted(set(provenance))):
        raise ValueError("provenance must be unique and sorted")
    if depends_on != tuple(sorted(set(depends_on))):
        raise ValueError("depends_on must be unique and sorted")
    if any(not item for item in depends_on):
        raise ValueError("depends_on must not contain empty values")


@dataclass(frozen=True, slots=True)
class LogicalAtomV2:
    """One immutable source payload plus its content-bound control header."""

    source_id: str
    semantic_keys: tuple[str, ...]
    role: AtomRole
    text: str
    status: AtomStatus
    revision: int
    as_of: int
    provenance: tuple[str, ...]
    exact: bool
    depends_on: tuple[str, ...]
    core_required: bool

    @classmethod
    def create(
        cls,
        *,
        source_label: str,
        semantic_keys: tuple[str, ...],
        role: AtomRole,
        text: str,
        status: AtomStatus,
        revision: int,
        as_of: int,
        provenance: tuple[str, ...],
        exact: bool,
        depends_on: tuple[str, ...],
        core_required: bool,
    ) -> Self:
        """Create a fixed-length source identity from canonical record content."""
        if not source_label:
            raise ValueError("source_label must not be empty")
        payload_hash = _source_payload_hash_fields_v2(
            text=text,
            provenance=provenance,
        )
        digest = _source_record_hash_fields_v2(
            semantic_keys=semantic_keys,
            role=role,
            status=status,
            revision=revision,
            as_of=as_of,
            exact=exact,
            depends_on=depends_on,
            core_required=core_required,
            payload_hash=payload_hash,
        )
        return cls(
            source_id=f"{_SOURCE_ID_PREFIX}{digest}",
            semantic_keys=semantic_keys,
            role=role,
            text=text,
            status=status,
            revision=revision,
            as_of=as_of,
            provenance=provenance,
            exact=exact,
            depends_on=depends_on,
            core_required=core_required,
        )

    def __post_init__(self) -> None:
        _validate_source_metadata(
            semantic_keys=self.semantic_keys,
            revision=self.revision,
            as_of=self.as_of,
            provenance=self.provenance,
            depends_on=self.depends_on,
        )
        expected = f"{_SOURCE_ID_PREFIX}{source_record_hash_v2(self)}"
        if self.source_id != expected:
            raise ValueError("source_id must be bound to the canonical source record")
        if self.source_id in self.depends_on:
            raise ValueError("self-dependency is not allowed")


def source_payload_hash_v2(atom: LogicalAtomV2) -> str:
    """Hash releasable source payload without duplicating it in control state."""
    return _source_payload_hash_fields_v2(
        text=atom.text,
        provenance=atom.provenance,
    )


def source_record_hash_v2(atom: LogicalAtomV2) -> str:
    """Return the canonical digest committed by a v2 source identity."""
    return _source_record_hash_fields_v2(
        semantic_keys=atom.semantic_keys,
        role=atom.role,
        status=atom.status,
        revision=atom.revision,
        as_of=atom.as_of,
        exact=atom.exact,
        depends_on=atom.depends_on,
        core_required=atom.core_required,
        payload_hash=source_payload_hash_v2(atom),
    )


@dataclass(frozen=True, slots=True)
class SourceReceiptV2:
    """Mandatory compact header that validates a released source payload."""

    source_id: str
    semantic_keys: tuple[str, ...]
    role: AtomRole
    status: AtomStatus
    revision: int
    as_of: int
    exact: bool
    depends_on: tuple[str, ...]
    core_required: bool
    payload_hash: str

    @classmethod
    def from_atom(cls, atom: LogicalAtomV2) -> Self:
        return cls(
            source_id=atom.source_id,
            semantic_keys=atom.semantic_keys,
            role=atom.role,
            status=atom.status,
            revision=atom.revision,
            as_of=atom.as_of,
            exact=atom.exact,
            depends_on=atom.depends_on,
            core_required=atom.core_required,
            payload_hash=source_payload_hash_v2(atom),
        )

    def __post_init__(self) -> None:
        _validate_source_metadata(
            semantic_keys=self.semantic_keys,
            revision=self.revision,
            as_of=self.as_of,
            provenance=("receipt",),
            depends_on=self.depends_on,
        )
        if not _valid_sha256(self.payload_hash):
            raise ValueError("payload_hash must be lowercase SHA-256")
        digest = _source_record_hash_fields_v2(
            semantic_keys=self.semantic_keys,
            role=self.role,
            status=self.status,
            revision=self.revision,
            as_of=self.as_of,
            exact=self.exact,
            depends_on=self.depends_on,
            core_required=self.core_required,
            payload_hash=self.payload_hash,
        )
        if self.source_id != f"{_SOURCE_ID_PREFIX}{digest}":
            raise ValueError("source_id must bind the source receipt")


@dataclass(frozen=True, slots=True)
class ActiveRecordV2:
    """Resident payload plus the subset of keys it currently owns."""

    atom: LogicalAtomV2
    active_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.atom.status is not AtomStatus.ACTIVE:
            raise ValueError("active record atom must be active")
        if not self.active_keys:
            raise ValueError("active_keys must not be empty")
        if self.active_keys != tuple(sorted(set(self.active_keys))):
            raise ValueError("active_keys must be unique and sorted")
        if not set(self.active_keys).issubset(self.atom.semantic_keys):
            raise ValueError("active_keys must be a subset of semantic_keys")


@dataclass(frozen=True, slots=True)
class KeyWinnerV2:
    """Mandatory per-key dominance mark, including non-active tombstones."""

    semantic_key: str
    revision: int
    source_id: str
    status: AtomStatus
    source_record_hash: str
    core_required: bool

    @classmethod
    def from_atom(cls, atom: LogicalAtomV2, semantic_key: str) -> Self:
        if semantic_key not in atom.semantic_keys:
            raise ValueError("frontier key must belong to the source record")
        return cls(
            semantic_key=semantic_key,
            revision=atom.revision,
            source_id=atom.source_id,
            status=atom.status,
            source_record_hash=source_record_hash_v2(atom),
            core_required=atom.core_required,
        )

    def __post_init__(self) -> None:
        if not self.semantic_key:
            raise ValueError("frontier semantic_key must not be empty")
        if self.revision <= 0:
            raise ValueError("frontier revision must be positive")
        if not _valid_sha256(self.source_record_hash):
            raise ValueError("source_record_hash must be lowercase SHA-256")
        if self.source_id != f"{_SOURCE_ID_PREFIX}{self.source_record_hash}":
            raise ValueError("frontier source_id must bind source_record_hash")


@dataclass(frozen=True, slots=True)
class SourceWeightV2:
    source_id: str
    weight: float

    def __post_init__(self) -> None:
        if not self.source_id.startswith(_SOURCE_ID_PREFIX) or not _valid_sha256(
            self.source_id.removeprefix(_SOURCE_ID_PREFIX)
        ):
            raise ValueError("weight source_id must be content-addressed")
        if not math.isfinite(self.weight) or self.weight < 0:
            raise ValueError("weight must be finite and nonnegative")


def _weight_policy_hash_v2(
    version: str,
    source_weights: tuple[SourceWeightV2, ...],
) -> str:
    return sha256_text(
        canonical_json(
            {
                "domain": _WEIGHT_POLICY_DOMAIN,
                "source_weights": source_weights,
                "version": version,
            }
        )
    )


@dataclass(frozen=True, slots=True)
class WeightPolicyV2:
    version: str
    source_weights: tuple[SourceWeightV2, ...]
    policy_hash: str

    @classmethod
    def create(
        cls,
        *,
        version: str,
        source_weights: tuple[SourceWeightV2, ...],
    ) -> Self:
        return cls(
            version=version,
            source_weights=source_weights,
            policy_hash=_weight_policy_hash_v2(version, source_weights),
        )

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("weight policy version must not be empty")
        if any(
            not isinstance(source_weight, SourceWeightV2)
            for source_weight in self.source_weights
        ):
            raise ValueError("source_weights must contain SourceWeightV2 values")
        for source_weight in self.source_weights:
            source_weight.__post_init__()
        if self.source_weights != tuple(
            sorted(self.source_weights, key=lambda item: item.source_id)
        ):
            raise ValueError("source_weights must be canonically sorted")
        source_ids = tuple(item.source_id for item in self.source_weights)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_weights must contain unique source IDs")
        if self.policy_hash != _weight_policy_hash_v2(
            self.version, self.source_weights
        ):
            raise ValueError("policy_hash must bind version and source weights")


@dataclass(frozen=True, slots=True)
class DeltaEnvelopeV2:
    """One complete atomic high-water interval or a replay-only no-op."""

    base_high_water: int
    target_high_water: int
    records: tuple[LogicalAtomV2, ...]

    def __post_init__(self) -> None:
        if self.base_high_water < 0 or self.target_high_water < self.base_high_water:
            raise ValueError("target_high_water must not precede base_high_water")
        if self.target_high_water - self.base_high_water > 1:
            raise ValueError("delta high-water interval must be continuous")
        if any(not isinstance(atom, LogicalAtomV2) for atom in self.records):
            raise ValueError("delta records must contain LogicalAtomV2 values")
        for atom in self.records:
            atom.__post_init__()
        if self.records != tuple(
            sorted(self.records, key=lambda atom: (atom.as_of, atom.source_id))
        ):
            raise ValueError("delta records must be canonically sorted")
        source_ids = tuple(atom.source_id for atom in self.records)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("delta source IDs must be unique")
        if self.target_high_water == self.base_high_water:
            if any(atom.as_of > self.base_high_water for atom in self.records):
                raise ValueError("replay-only delta exceeds base high-water")
        elif any(atom.as_of != self.target_high_water for atom in self.records):
            raise ValueError("delta must not mix stale/new high-water records")


@dataclass(frozen=True, slots=True)
class DirectBlockV2:
    """Physical one-record body block used before optional packing."""

    record: ActiveRecordV2
    kind: str = "direct-v2"

    def __post_init__(self) -> None:
        if self.kind != "direct-v2":
            raise ValueError("direct block kind must be direct-v2")


@dataclass(frozen=True, slots=True)
class PackingPolicyV2:
    """Resident-only policy that authorizes bounded physical packing."""

    codec: str
    immutable_source_ids: tuple[str, ...]
    max_records_per_block: int
    max_decoded_block_bytes: int

    @classmethod
    def disabled(cls) -> Self:
        return cls(
            codec=_PACKING_CODEC_V2,
            immutable_source_ids=(),
            max_records_per_block=8,
            max_decoded_block_bytes=65_536,
        )

    def __post_init__(self) -> None:
        if self.codec != _PACKING_CODEC_V2:
            raise ValueError(f"packing codec must be {_PACKING_CODEC_V2}")
        if self.immutable_source_ids != tuple(sorted(self.immutable_source_ids)):
            raise ValueError("immutable_source_ids must be canonically sorted")
        if len(self.immutable_source_ids) != len(set(self.immutable_source_ids)):
            raise ValueError("immutable_source_ids must be unique")
        if any(
            not _valid_source_id_v2(source_id)
            for source_id in self.immutable_source_ids
        ):
            raise ValueError("immutable_source_ids must be content-addressed")
        if self.max_records_per_block < 2:
            raise ValueError("max_records_per_block must be at least two")
        if self.max_decoded_block_bytes <= 0:
            raise ValueError("max_decoded_block_bytes must be positive")


class SolverModeV2(StrEnum):
    AUTO = "auto"
    EXACT_SMALL = "exact_small"
    REFERENCE = "reference"
    GREEDY = "greedy"


class EncodingModeV2(StrEnum):
    DMC1 = "dmc1"
    DIRECT_ONLY = "direct_only"


class SourcePlacementV2(StrEnum):
    KERNEL = "kernel"
    BODY = "body"
    RELEASED_CONTROL = "released_control"


class RecompositionOutcomeV2(StrEnum):
    NORMAL = "normal"
    KERNEL_ONLY = "kernel_only"
    NOOP = "noop"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True, slots=True)
class CandidatePolicyV2:
    """Frozen work bounds for logical selection and physical pack proposals."""

    version: str = "crm-candidate-v1"
    solver_mode: SolverModeV2 = SolverModeV2.AUTO
    encoding_mode: EncodingModeV2 = EncodingModeV2.DMC1
    exact_small_limit: int = 12
    reference_row_limit: int = 16
    max_pack_neighbors: int = 2
    max_pack_proposals: int = 1024
    max_plan_evaluations: int = 4096
    max_greedy_steps: int = 256
    max_refinement_evaluations: int = 128
    near_tie_epsilon: float = 1e-12

    @property
    def enable_packing(self) -> bool:
        return self.encoding_mode is EncodingModeV2.DMC1

    @property
    def max_oracle_evaluations(self) -> int:
        return self.max_plan_evaluations

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("candidate policy version must not be empty")
        if not isinstance(self.solver_mode, SolverModeV2):
            raise ValueError("solver_mode must be SolverModeV2")
        if not isinstance(self.encoding_mode, EncodingModeV2):
            raise ValueError("encoding_mode must be EncodingModeV2")
        if not 0 <= self.exact_small_limit <= 20:
            raise ValueError("exact_small_limit must be between zero and twenty")
        if not self.exact_small_limit <= self.reference_row_limit <= 20:
            raise ValueError(
                "reference_row_limit must be between exact_small_limit and twenty"
            )
        if self.max_pack_neighbors <= 0:
            raise ValueError("max_pack_neighbors must be positive")
        if self.max_pack_proposals <= 0:
            raise ValueError("max_pack_proposals must be positive")
        if self.max_plan_evaluations <= 0:
            raise ValueError("max_plan_evaluations must be positive")
        if self.max_greedy_steps <= 0:
            raise ValueError("max_greedy_steps must be positive")
        if not 0 <= self.max_refinement_evaluations <= self.max_plan_evaluations:
            raise ValueError(
                "max_refinement_evaluations must fit the oracle work budget"
            )
        if not math.isfinite(self.near_tie_epsilon) or self.near_tie_epsilon < 0:
            raise ValueError("near_tie_epsilon must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class SourceRowV2:
    """One latest-active logical source in the sparse optimizer universe."""

    source_id: str
    receipt: SourceReceiptV2
    active_keys: tuple[str, ...]
    record: ActiveRecordV2 | None
    weight: float
    placement: SourcePlacementV2
    pack_eligible: bool

    @property
    def available(self) -> bool:
        return self.record is not None

    @property
    def mandatory(self) -> bool:
        return self.placement is SourcePlacementV2.KERNEL

    @property
    def depends_on(self) -> tuple[str, ...]:
        return self.receipt.depends_on

    def __post_init__(self) -> None:
        if not _valid_source_id_v2(self.source_id):
            raise ValueError("source row ID must be content-addressed")
        self.receipt.__post_init__()
        if self.source_id != self.receipt.source_id:
            raise ValueError("source row ID must match its receipt")
        if not self.active_keys or self.active_keys != tuple(
            sorted(set(self.active_keys))
        ):
            raise ValueError("source row active_keys must be canonical")
        if not math.isfinite(self.weight) or self.weight < 0:
            raise ValueError("source row weight must be finite and nonnegative")
        if not isinstance(self.placement, SourcePlacementV2):
            raise ValueError("source row placement must be SourcePlacementV2")
        if not isinstance(self.pack_eligible, bool):
            raise ValueError("source row pack_eligible must be boolean")
        if self.mandatory and not self.available:
            raise ValueError("mandatory source payload must be available")
        if self.pack_eligible and (not self.available or self.mandatory):
            raise ValueError("pack-eligible source must be optional and available")
        if self.record is not None:
            self.record.__post_init__()
            if self.record.atom.source_id != self.source_id:
                raise ValueError("source row payload must match source ID")
            if SourceReceiptV2.from_atom(self.record.atom) != self.receipt:
                raise ValueError("source row payload must match its receipt")
            if self.record.active_keys != self.active_keys:
                raise ValueError("source row payload active_keys mismatch")
        if self.placement is SourcePlacementV2.RELEASED_CONTROL and self.available:
            raise ValueError("released-control row cannot retain a payload")
        if self.placement is SourcePlacementV2.BODY and not self.available:
            raise ValueError("body row must retain a payload")
        if self.mandatory != self.receipt.core_required:
            raise ValueError("source row placement must match core_required")


@dataclass(frozen=True, slots=True)
class SparseEdgeV2:
    """One directed source-level dependency or canonical conflict edge."""

    source_id: str
    target_id: str

    def __post_init__(self) -> None:
        if not _valid_source_id_v2(self.source_id) or not _valid_source_id_v2(
            self.target_id
        ):
            raise ValueError("sparse edge endpoints must be content-addressed")
        if self.source_id == self.target_id:
            raise ValueError("sparse edge cannot be a self-edge")


@dataclass(frozen=True, slots=True)
class PackProposalV2:
    """One bounded source-ID adjacency proposal, never a semantic candidate."""

    left_source_id: str
    right_source_id: str
    neighbor_distance: int

    def __post_init__(self) -> None:
        if not _valid_source_id_v2(self.left_source_id) or not _valid_source_id_v2(
            self.right_source_id
        ):
            raise ValueError("pack proposal endpoints must be content-addressed")
        if self.left_source_id >= self.right_source_id:
            raise ValueError("pack proposal endpoints must be canonical")
        if self.neighbor_distance <= 0:
            raise ValueError("pack proposal neighbor_distance must be positive")


@dataclass(frozen=True, slots=True)
class SourceMatrixV2:
    """Sparse logical rows plus bounded physical-layout suggestions."""

    rows: tuple[SourceRowV2, ...]
    dependencies: tuple[SparseEdgeV2, ...]
    conflicts: tuple[SparseEdgeV2, ...]
    pack_proposals: tuple[PackProposalV2, ...]

    @property
    def active_source_ids(self) -> tuple[str, ...]:
        return tuple(row.source_id for row in self.rows)

    @property
    def selectable_source_ids(self) -> tuple[str, ...]:
        return tuple(row.source_id for row in self.rows if row.available)

    @property
    def unavailable_source_ids(self) -> tuple[str, ...]:
        return tuple(row.source_id for row in self.rows if not row.available)

    @property
    def mandatory_source_ids(self) -> tuple[str, ...]:
        return tuple(row.source_id for row in self.rows if row.mandatory)

    @property
    def pack_proposal_count(self) -> int:
        return len(self.pack_proposals)

    @property
    def total_candidate_count(self) -> int:
        return len(self.selectable_source_ids) + self.pack_proposal_count

    @property
    def proposal_count(self) -> int:
        """Compatibility alias for the explicitly named physical proposal count."""
        return self.pack_proposal_count

    def __post_init__(self) -> None:
        if self.rows != tuple(sorted(self.rows, key=lambda row: row.source_id)):
            raise ValueError("source matrix rows must be canonically sorted")
        source_ids = self.active_source_ids
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source matrix rows must have unique source IDs")
        source_id_set = set(source_ids)
        for row in self.rows:
            row.__post_init__()
            if not set(row.depends_on).issubset(source_id_set):
                raise ValueError("source row dependency is outside active universe")
        for name, edges in (
            ("dependencies", self.dependencies),
            ("conflicts", self.conflicts),
        ):
            if edges != tuple(
                sorted(edges, key=lambda edge: (edge.source_id, edge.target_id))
            ):
                raise ValueError(f"source matrix {name} must be canonically sorted")
            if len(edges) != len(set(edges)):
                raise ValueError(f"source matrix {name} must be unique")
            for edge in edges:
                edge.__post_init__()
                if (
                    edge.source_id not in source_id_set
                    or edge.target_id not in source_id_set
                ):
                    raise ValueError(f"source matrix {name} endpoint is unknown")
        if self.pack_proposals != tuple(
            sorted(
                self.pack_proposals,
                key=lambda item: (
                    item.left_source_id,
                    item.right_source_id,
                    item.neighbor_distance,
                ),
            )
        ):
            raise ValueError("pack proposals must be canonically sorted")
        if len(self.pack_proposals) != len(set(self.pack_proposals)):
            raise ValueError("pack proposals must be unique")
        eligible = {row.source_id for row in self.rows if row.pack_eligible}
        for proposal in self.pack_proposals:
            proposal.__post_init__()
            if not {
                proposal.left_source_id,
                proposal.right_source_id,
            }.issubset(eligible):
                raise ValueError("pack proposal references an ineligible source")


@dataclass(frozen=True, slots=True)
class CandidatePlanV2:
    """Logical retention plan; physical direct/packed layout is derived later."""

    retained_source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.retained_source_ids != tuple(sorted(set(self.retained_source_ids))):
            raise ValueError("retained source IDs must be unique and sorted")
        if any(
            not _valid_source_id_v2(source_id) for source_id in self.retained_source_ids
        ):
            raise ValueError("retained source ID must be content-addressed")


@dataclass(frozen=True, slots=True)
class PlanEvaluationV2:
    """Exact complete-state cost returned by the shared canonical encoder."""

    plan: CandidatePlanV2
    resident_bytes: int
    direct_equivalent_bytes: int
    packing_savings_bytes: int
    emitted_pack_count: int
    valid: bool
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        self.plan.__post_init__()
        if self.resident_bytes <= 0 or self.direct_equivalent_bytes <= 0:
            raise ValueError("plan byte counts must be positive")
        if self.direct_equivalent_bytes < self.resident_bytes:
            raise ValueError("direct equivalent cannot be smaller than encoded plan")
        if self.packing_savings_bytes != (
            self.direct_equivalent_bytes - self.resident_bytes
        ):
            raise ValueError("plan packing savings must match exact bytes")
        if self.emitted_pack_count < 0:
            raise ValueError("emitted_pack_count must be nonnegative")
        if self.emitted_pack_count == 0 and self.packing_savings_bytes != 0:
            raise ValueError("unpacked plan cannot report packing savings")
        if self.emitted_pack_count > 0 and self.packing_savings_bytes <= 0:
            raise ValueError("emitted packs require strict aggregate savings")
        if self.reasons != tuple(sorted(set(self.reasons))):
            raise ValueError("plan evaluation reasons must be canonical")
        if self.valid and self.reasons:
            raise ValueError("valid plan evaluation cannot contain reasons")
        if not self.valid and not self.reasons:
            raise ValueError("invalid plan evaluation requires reasons")


@dataclass(frozen=True, slots=True)
class OptimizerWorkV2:
    """Deterministic correctness counters; wall-clock is deliberately absent."""

    plans_evaluated: int
    oracle_evaluations: int
    greedy_steps: int
    refinement_evaluations: int
    work_limit_hit: bool
    reference_tie_breaks: int = 0

    def __post_init__(self) -> None:
        if any(
            value < 0
            for value in (
                self.plans_evaluated,
                self.oracle_evaluations,
                self.greedy_steps,
                self.refinement_evaluations,
                self.reference_tie_breaks,
            )
        ):
            raise ValueError("optimizer work counters must be nonnegative")
        if self.plans_evaluated != self.oracle_evaluations:
            raise ValueError("each unique plan must have one oracle evaluation")
        if self.reference_tie_breaks > self.oracle_evaluations:
            raise ValueError("reference tie-breaks require evaluated oracle plans")
        if not isinstance(self.work_limit_hit, bool):
            raise ValueError("work_limit_hit must be boolean")


@dataclass(frozen=True, slots=True)
class OptimizerSelectionV2:
    """One feasible logical selection and its exact encoded-state cost."""

    plan: CandidatePlanV2
    released_source_ids: tuple[str, ...]
    retained_weight: float
    omitted_weight: float
    evaluation: PlanEvaluationV2
    solver_mode: SolverModeV2
    work: OptimizerWorkV2

    def __post_init__(self) -> None:
        self.plan.__post_init__()
        self.evaluation.__post_init__()
        self.work.__post_init__()
        if self.evaluation.plan != self.plan:
            raise ValueError("optimizer evaluation must match selected plan")
        if self.released_source_ids != tuple(sorted(set(self.released_source_ids))):
            raise ValueError("released source IDs must be unique and sorted")
        if set(self.released_source_ids) & set(self.plan.retained_source_ids):
            raise ValueError("retained and released source IDs must be disjoint")
        if not math.isfinite(self.retained_weight) or self.retained_weight < 0:
            raise ValueError("retained_weight must be finite and nonnegative")
        if not math.isfinite(self.omitted_weight) or self.omitted_weight < 0:
            raise ValueError("omitted_weight must be finite and nonnegative")
        if not isinstance(self.solver_mode, SolverModeV2):
            raise ValueError("optimizer solver_mode must be SolverModeV2")


@dataclass(frozen=True, slots=True)
class PackedEntryV2:
    """One leaf payload residual bound to a mandatory source receipt."""

    source_id: str
    active_keys: tuple[str, ...]
    text_middle: str
    provenance: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _valid_source_id_v2(self.source_id):
            raise ValueError("packed entry source_id must be content-addressed")
        if not self.active_keys:
            raise ValueError("packed active_keys must not be empty")
        if self.active_keys != tuple(sorted(set(self.active_keys))):
            raise ValueError("packed active_keys must be unique and sorted")
        if not isinstance(self.text_middle, str):
            raise ValueError("packed text_middle must be a string")
        if not self.provenance:
            raise ValueError("packed provenance must not be empty")
        if any(not item for item in self.provenance):
            raise ValueError("packed provenance must not contain empty values")
        if self.provenance != tuple(sorted(set(self.provenance))):
            raise ValueError("packed provenance must be unique and sorted")

    def decode(
        self,
        receipt: SourceReceiptV2,
        common_prefix: str,
        common_suffix: str,
    ) -> ActiveRecordV2:
        if receipt.source_id != self.source_id:
            raise ValueError("packed entry is missing its source receipt")
        try:
            atom = LogicalAtomV2(
                source_id=self.source_id,
                semantic_keys=receipt.semantic_keys,
                role=receipt.role,
                text=f"{common_prefix}{self.text_middle}{common_suffix}",
                status=receipt.status,
                revision=receipt.revision,
                as_of=receipt.as_of,
                provenance=self.provenance,
                exact=receipt.exact,
                depends_on=receipt.depends_on,
                core_required=receipt.core_required,
            )
        except ValueError as error:
            raise ValueError(
                "packed payload does not match its source receipt"
            ) from error
        if SourceReceiptV2.from_atom(atom) != receipt:
            raise ValueError("packed payload does not reproduce its source receipt")
        return ActiveRecordV2(atom, self.active_keys)


@dataclass(frozen=True, slots=True)
class PackedContextBlockV2:
    """Depth-one DMC1 block decoded with receipts from the same frozen state."""

    common_prefix: str
    common_suffix: str
    entries: tuple[PackedEntryV2, ...]
    kind: str = _PACKED_CONTEXT_KIND_V2

    def __post_init__(self) -> None:
        if self.kind != _PACKED_CONTEXT_KIND_V2:
            raise ValueError(f"packed block kind must be {_PACKED_CONTEXT_KIND_V2}")
        if not isinstance(self.common_prefix, str) or not isinstance(
            self.common_suffix, str
        ):
            raise ValueError("packed common affixes must be strings")
        if len(self.entries) < 2:
            raise ValueError("packed block must contain at least two entries")
        if any(not isinstance(entry, PackedEntryV2) for entry in self.entries):
            raise ValueError("packed entries must be leaf PackedEntryV2 records")
        if self.entries != tuple(
            sorted(self.entries, key=lambda entry: entry.source_id)
        ):
            raise ValueError("packed entries must be canonically sorted")
        source_ids = tuple(entry.source_id for entry in self.entries)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("packed entries must contain unique sources")
        texts = tuple(
            f"{self.common_prefix}{entry.text_middle}{self.common_suffix}"
            for entry in self.entries
        )
        if canonical_affixes_v2(texts) != (
            self.common_prefix,
            self.common_suffix,
        ):
            raise ValueError("packed affixes must use canonical LCP/LCS factoring")

    @property
    def record(self) -> ActiveRecordV2:
        """Fail closed if a direct-only caller tries to flatten this block."""
        raise ValueError("packed block requires the frozen-state codec")

    @property
    def source_ids(self) -> tuple[str, ...]:
        if any(not isinstance(entry, PackedEntryV2) for entry in self.entries):
            raise ValueError("packed entries must be leaf PackedEntryV2 records")
        return tuple(entry.source_id for entry in self.entries)


BodyBlockV2 = DirectBlockV2 | PackedContextBlockV2


def _body_source_ids_v2(block: BodyBlockV2) -> tuple[str, ...]:
    if isinstance(block, DirectBlockV2):
        return (block.record.atom.source_id,)
    return block.source_ids


@dataclass(frozen=True, slots=True)
class CapsuleStateV2:
    """Frozen v2 state with mandatory control state and optional payloads."""

    schema_version: int
    generation: int
    high_water: int
    accepted_budget: int
    key_registry_limit: int
    max_semantic_key_bytes: int
    frontier: tuple[KeyWinnerV2, ...]
    receipts: tuple[SourceReceiptV2, ...]
    weight_policy: WeightPolicyV2
    kernel: tuple[ActiveRecordV2, ...]
    body: tuple[BodyBlockV2, ...]
    packing_policy: PackingPolicyV2 = field(default_factory=PackingPolicyV2.disabled)
    recomposition_policy_hash: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise ValueError("schema_version must equal 2")
        if self.generation <= 0:
            raise ValueError("generation must be positive")
        if self.high_water < 0:
            raise ValueError("high_water must be nonnegative")
        if self.accepted_budget <= 0:
            raise ValueError("accepted_budget must be positive")
        if self.key_registry_limit <= 0:
            raise ValueError("key_registry_limit must be positive")
        if self.max_semantic_key_bytes <= 0:
            raise ValueError("max_semantic_key_bytes must be positive")
        if any(not isinstance(winner, KeyWinnerV2) for winner in self.frontier):
            raise ValueError("frontier must contain KeyWinnerV2 records")
        for winner in self.frontier:
            winner.__post_init__()
        if self.frontier != tuple(
            sorted(self.frontier, key=lambda winner: winner.semantic_key)
        ):
            raise ValueError("frontier must be canonically sorted")
        frontier_keys = tuple(winner.semantic_key for winner in self.frontier)
        if len(frontier_keys) != len(set(frontier_keys)):
            raise ValueError("frontier semantic keys must be unique")
        if len(self.frontier) > self.key_registry_limit:
            raise ValueError("frontier exceeds key_registry_limit")
        if any(utf8_bytes(key) > self.max_semantic_key_bytes for key in frontier_keys):
            raise ValueError("frontier semantic key exceeds byte limit")
        if any(not isinstance(receipt, SourceReceiptV2) for receipt in self.receipts):
            raise ValueError("receipts must contain SourceReceiptV2 records")
        for receipt in self.receipts:
            receipt.__post_init__()
        if self.receipts != tuple(
            sorted(self.receipts, key=lambda receipt: receipt.source_id)
        ):
            raise ValueError("source receipts must be canonically sorted")
        receipt_ids = tuple(receipt.source_id for receipt in self.receipts)
        if len(receipt_ids) != len(set(receipt_ids)):
            raise ValueError("source receipts must have unique source IDs")
        frontier_source_ids = {winner.source_id for winner in self.frontier}
        if set(receipt_ids) != frontier_source_ids:
            raise ValueError("source receipts must exactly cover frontier sources")
        if any(receipt.as_of > self.high_water for receipt in self.receipts):
            raise ValueError("high_water must cover every source receipt")
        if any(
            utf8_bytes(key) > self.max_semantic_key_bytes
            for receipt in self.receipts
            for key in receipt.semantic_keys
        ):
            raise ValueError("receipt semantic key exceeds byte limit")
        frontier_key_set = set(frontier_keys)
        if any(
            not set(receipt.semantic_keys).issubset(frontier_key_set)
            for receipt in self.receipts
        ):
            raise ValueError("frontier must cover all source receipt keys")

        receipt_by_source = {receipt.source_id: receipt for receipt in self.receipts}
        for winner in self.frontier:
            receipt = receipt_by_source[winner.source_id]
            if winner.semantic_key not in receipt.semantic_keys:
                raise ValueError("frontier key is absent from source receipt")
            if (
                winner.revision != receipt.revision
                or winner.status is not receipt.status
                or winner.source_record_hash
                != receipt.source_id.removeprefix(_SOURCE_ID_PREFIX)
                or winner.core_required != receipt.core_required
            ):
                raise ValueError("frontier winner does not match source receipt")

        active_frontier_ids = {
            winner.source_id
            for winner in self.frontier
            if winner.status is AtomStatus.ACTIVE
        }
        if not isinstance(self.weight_policy, WeightPolicyV2):
            raise ValueError("weight_policy must be WeightPolicyV2")
        if any(
            not isinstance(source_weight, SourceWeightV2)
            for source_weight in self.weight_policy.source_weights
        ):
            raise ValueError("weight policy must contain SourceWeightV2 records")
        for source_weight in self.weight_policy.source_weights:
            source_weight.__post_init__()
        self.weight_policy.__post_init__()
        weight_ids = {
            source_weight.source_id
            for source_weight in self.weight_policy.source_weights
        }
        if weight_ids != active_frontier_ids:
            raise ValueError("weight policy must exactly cover active frontier sources")
        for source_id in active_frontier_ids:
            receipt = receipt_by_source[source_id]
            for dependency in receipt.depends_on:
                if dependency not in active_frontier_ids:
                    raise ValueError(
                        f"active dependency missing: {source_id}->{dependency}"
                    )

        if not isinstance(self.packing_policy, PackingPolicyV2):
            raise ValueError("packing_policy must be PackingPolicyV2")
        self.packing_policy.__post_init__()
        if not set(self.packing_policy.immutable_source_ids).issubset(
            active_frontier_ids
        ):
            raise ValueError("packing policy sources must be active frontier sources")
        if self.recomposition_policy_hash and not _valid_sha256(
            self.recomposition_policy_hash
        ):
            raise ValueError("recomposition policy hash must be lowercase SHA-256")

        if any(not isinstance(record, ActiveRecordV2) for record in self.kernel):
            raise ValueError("kernel must contain ActiveRecordV2 records")
        if self.kernel != tuple(
            sorted(
                self.kernel,
                key=lambda record: (record.atom.role.value, record.atom.source_id),
            )
        ):
            raise ValueError("kernel sources must be canonically sorted")
        if any(
            not isinstance(block, (DirectBlockV2, PackedContextBlockV2))
            for block in self.body
        ):
            raise ValueError("body contains an unsupported block type")
        if self.body != tuple(sorted(self.body, key=_body_source_ids_v2)):
            raise ValueError("body sources must be canonically sorted")
        body_source_ids = tuple(
            source_id for block in self.body for source_id in _body_source_ids_v2(block)
        )
        if body_source_ids != tuple(sorted(body_source_ids)):
            raise ValueError(
                "body blocks must cover contiguous canonical source-ID slices"
            )

        body_records: list[ActiveRecordV2] = []
        immutable_ids = set(self.packing_policy.immutable_source_ids)
        for block in self.body:
            block.__post_init__()
            if isinstance(block, DirectBlockV2):
                body_records.append(block.record)
                continue
            if len(block.entries) > self.packing_policy.max_records_per_block:
                raise ValueError("packed block exceeds max_records_per_block")
            if not set(block.source_ids).issubset(immutable_ids):
                raise ValueError("packed source is not immutable in packing policy")
            decoded: list[ActiveRecordV2] = []
            for entry in block.entries:
                entry.__post_init__()
                receipt = receipt_by_source.get(entry.source_id)
                if receipt is None:
                    raise ValueError("packed entry is missing its source receipt")
                decoded.append(
                    entry.decode(
                        receipt,
                        block.common_prefix,
                        block.common_suffix,
                    )
                )
            if (
                sum(utf8_bytes(record.atom.text) for record in decoded)
                > self.packing_policy.max_decoded_block_bytes
            ):
                raise ValueError("packed block exceeds max_decoded_block_bytes")
            if any(
                record.atom.role is not AtomRole.CONTEXT
                or record.atom.exact
                or record.atom.core_required
                or record.atom.depends_on
                for record in decoded
            ):
                raise ValueError("packed block contains an ineligible source")
            body_records.extend(decoded)

        records = self.kernel + tuple(body_records)
        for record in records:
            record.atom.__post_init__()
            record.__post_init__()
        source_ids = tuple(record.atom.source_id for record in records)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("resident source IDs must be unique")
        if any(not record.atom.core_required for record in self.kernel):
            raise ValueError("resident kernel atoms must be core_required")
        if any(record.atom.core_required for record in body_records):
            raise ValueError("core_required atoms must not reside in the body")
        if any(record.atom.as_of > self.high_water for record in records):
            raise ValueError("high_water must cover every resident atom")

        active_keys_by_source: dict[str, set[str]] = {}
        for winner in self.frontier:
            if winner.status is AtomStatus.ACTIVE:
                active_keys_by_source.setdefault(winner.source_id, set()).add(
                    winner.semantic_key
                )
        record_by_source = {record.atom.source_id: record for record in records}
        for record in records:
            atom = record.atom
            receipt = receipt_by_source.get(atom.source_id)
            if receipt is None or receipt != SourceReceiptV2.from_atom(atom):
                raise ValueError("resident source does not match its receipt")
            expected_active = tuple(
                sorted(active_keys_by_source.get(atom.source_id, set()))
            )
            if record.active_keys != expected_active:
                raise ValueError("active_keys must match the active frontier")
        for winner in self.frontier:
            if (
                winner.status is AtomStatus.ACTIVE
                and winner.core_required
                and winner.source_id not in record_by_source
            ):
                raise ValueError(
                    "active core frontier winner is missing kernel payload"
                )


@dataclass(frozen=True, slots=True)
class LogicalResolutionV2:
    """Canonical latest-state result before physical body encoding."""

    active_records: tuple[ActiveRecordV2, ...]
    frontier: tuple[KeyWinnerV2, ...]
    receipts: tuple[SourceReceiptV2, ...]
    high_water: int


@dataclass(frozen=True, slots=True)
class FrozenStateViewV2:
    """The only state capability accepted by source projection."""

    state: CapsuleStateV2


@dataclass(frozen=True, slots=True)
class ProjectedSourceV2:
    """One decoded source-bound record emitted to a query projection."""

    source_id: str
    semantic_keys: tuple[str, ...]
    active_keys: tuple[str, ...]
    role: AtomRole
    text: str
    revision: int

    @classmethod
    def from_record(cls, record: ActiveRecordV2) -> Self:
        return cls(
            source_id=record.atom.source_id,
            semantic_keys=record.atom.semantic_keys,
            active_keys=record.active_keys,
            role=record.atom.role,
            text=record.atom.text,
            revision=record.atom.revision,
        )

    def __post_init__(self) -> None:
        if not _valid_source_id_v2(self.source_id):
            raise ValueError("projected source_id must be content-addressed")
        if not self.semantic_keys or self.semantic_keys != tuple(
            sorted(set(self.semantic_keys))
        ):
            raise ValueError("projected semantic_keys must be canonical")
        if not self.active_keys or self.active_keys != tuple(
            sorted(set(self.active_keys))
        ):
            raise ValueError("projected active_keys must be canonical")
        if not set(self.active_keys).issubset(self.semantic_keys):
            raise ValueError("projected active_keys must belong to semantic_keys")
        if self.revision <= 0:
            raise ValueError("projected revision must be positive")


@dataclass(frozen=True, slots=True)
class ProjectionResultV2:
    """Projection whose selected coverage derives only from emitted records."""

    query_id: str
    records: tuple[ProjectedSourceV2, ...]
    text: str
    byte_cost: int
    supported: bool
    reason: str

    @property
    def selected_source_ids(self) -> tuple[str, ...]:
        return tuple(record.source_id for record in self.records)

    def __post_init__(self) -> None:
        if not self.query_id:
            raise ValueError("projection query_id must not be empty")
        if not self.reason:
            raise ValueError("projection reason must not be empty")
        if self.byte_cost != utf8_bytes(self.text):
            raise ValueError("projection byte_cost must match emitted UTF-8 text")
        source_ids = self.selected_source_ids
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("projection records must contain unique sources")
        if self.supported:
            if not self.records or self.reason != "supported":
                raise ValueError("supported projection requires emitted records")
            if self.text != "\n".join(record.text for record in self.records):
                raise ValueError("projection text must derive from emitted records")
        elif self.records:
            raise ValueError("unsupported projection must not emit source records")


@dataclass(frozen=True, slots=True)
class ResidentBreakdownV2:
    """Exact additive decomposition of canonical resident bytes."""

    fixed_bytes: int
    body_item_bytes: tuple[int, ...]
    body_separator_bytes: int
    persistent_bytes: int


@dataclass(frozen=True, slots=True)
class KernelSlotV2:
    role: AtomRole
    max_items: int
    max_text_bytes: int

    def __post_init__(self) -> None:
        if self.max_items <= 0 or self.max_text_bytes <= 0:
            raise ValueError("kernel slot bounds must be positive")


@dataclass(frozen=True, slots=True)
class KernelSchemaV2:
    slots: tuple[KernelSlotV2, ...]
    continuity_floor_bytes: int

    def __post_init__(self) -> None:
        if not self.slots:
            raise ValueError("kernel schema slots must not be empty")
        if any(not isinstance(slot, KernelSlotV2) for slot in self.slots):
            raise ValueError("kernel schema slots must contain KernelSlotV2 values")
        for slot in self.slots:
            slot.__post_init__()
        roles = tuple(slot.role for slot in self.slots)
        if len(roles) != len(set(roles)):
            raise ValueError("kernel schema roles must be unique")
        if self.continuity_floor_bytes <= 0:
            raise ValueError("continuity_floor_bytes must be positive")


def recomposition_policy_hash_v2(
    kernel_schema: KernelSchemaV2,
    candidate_policy: CandidatePolicyV2,
) -> str:
    """Bind every no-op-result-relevant recomposition policy input."""
    kernel_schema.__post_init__()
    candidate_policy.__post_init__()
    return sha256_text(
        canonical_json(
            {
                "candidate_policy": candidate_policy,
                "domain": _RECOMPOSITION_POLICY_DOMAIN,
                "kernel_schema": kernel_schema,
            }
        )
    )


@dataclass(frozen=True, slots=True)
class KernelSelectionV2:
    records: tuple[ActiveRecordV2, ...]
    resident_bytes: int | None
    valid: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EncodedPlanV2:
    """One canonical physical realization of a logical retention plan."""

    state: CapsuleStateV2
    direct_state: CapsuleStateV2
    evaluation: PlanEvaluationV2
    codec_attempts: int
    direct_fallback_count: int
    full_state_evaluations: int

    def __post_init__(self) -> None:
        self.evaluation.__post_init__()
        if (
            self.codec_attempts < 0
            or self.direct_fallback_count < 0
            or self.full_state_evaluations <= 0
        ):
            raise ValueError("codec counters must be nonnegative")
        if self.evaluation.emitted_pack_count > self.codec_attempts:
            raise ValueError("emitted packs cannot exceed codec attempts")
        if self.direct_fallback_count != (
            self.codec_attempts - self.evaluation.emitted_pack_count
        ):
            raise ValueError("codec attempts must partition emit/fallback outcomes")
        expected_full_evaluations = 2 if self.evaluation.emitted_pack_count else 1
        if self.full_state_evaluations != expected_full_evaluations:
            raise ValueError(
                "encoder must materialize only the direct and optional final state"
            )


@dataclass(frozen=True, slots=True)
class GateReportV2:
    """Public, deterministic acceptance evidence for one candidate state."""

    valid: bool
    reasons: tuple[str, ...]
    candidate_resident_bytes: int | None
    direct_equivalent_bytes: int | None
    accepted_budget: int
    budget_margin: int | None
    expected_logical_hash: str
    actual_logical_hash: str | None

    def __post_init__(self) -> None:
        if self.reasons != tuple(sorted(set(self.reasons))):
            raise ValueError("gate reasons must be canonical")
        if self.accepted_budget <= 0:
            raise ValueError("gate accepted_budget must be positive")
        if not self.expected_logical_hash:
            raise ValueError("gate expected logical hash must not be empty")
        if (
            self.candidate_resident_bytes is not None
            and self.candidate_resident_bytes <= 0
        ):
            raise ValueError("gate candidate bytes must be positive")
        if (
            self.direct_equivalent_bytes is not None
            and self.direct_equivalent_bytes <= 0
        ):
            raise ValueError("gate direct bytes must be positive")
        if self.candidate_resident_bytes is None:
            if self.budget_margin is not None:
                raise ValueError(
                    "unknown candidate bytes require unknown budget margin"
                )
        elif self.budget_margin != self.accepted_budget - self.candidate_resident_bytes:
            raise ValueError("gate budget margin must match exact bytes")
        if self.valid:
            if self.reasons:
                raise ValueError("valid gate cannot contain reasons")
            if (
                self.candidate_resident_bytes is None
                or self.actual_logical_hash is None
            ):
                raise ValueError("valid gate requires complete evidence")
        elif not self.reasons:
            raise ValueError("invalid gate requires reasons")


@dataclass(frozen=True, slots=True)
class RecompositionRequestV2:
    """All outcome-independent inputs required for one atomic generation."""

    envelope: DeltaEnvelopeV2
    requested_budget: int
    kernel_schema: KernelSchemaV2
    next_weight_policy: WeightPolicyV2
    next_packing_policy: PackingPolicyV2
    candidate_policy: CandidatePolicyV2
    key_registry_limit: int
    max_semantic_key_bytes: int

    def __post_init__(self) -> None:
        self.envelope.__post_init__()
        self.kernel_schema.__post_init__()
        self.next_weight_policy.__post_init__()
        self.next_packing_policy.__post_init__()
        self.candidate_policy.__post_init__()
        if self.requested_budget <= 0:
            raise ValueError("requested_budget must be positive")
        if self.key_registry_limit <= 0 or self.max_semantic_key_bytes <= 0:
            raise ValueError("recomposition registry bounds must be positive")


@dataclass(frozen=True, slots=True)
class RecompositionMetricsV2:
    """Packing and source release remain separate, fixed-denominator accounts."""

    persistent_bytes: int
    direct_equivalent_bytes: int
    packing_savings_bytes: int
    packing_savings_rate: float
    active_source_count: int
    available_source_count: int
    retained_source_count: int
    released_source_count: int
    released_control_source_count: int
    known_released_payload_bytes: int
    active_weight: float
    retained_weight: float
    omitted_weight: float
    weighted_omission_rate: float
    pack_proposal_count: int
    codec_attempts: int
    emitted_pack_count: int
    direct_fallback_count: int
    encoder_full_state_evaluations: int
    optimizer_work: OptimizerWorkV2

    def __post_init__(self) -> None:
        if self.persistent_bytes <= 0 or self.direct_equivalent_bytes <= 0:
            raise ValueError("recomposition byte counts must be positive")
        if self.packing_savings_bytes != (
            self.direct_equivalent_bytes - self.persistent_bytes
        ):
            raise ValueError("packing savings must match direct equivalent bytes")
        expected_packing_rate = (
            self.packing_savings_bytes / self.direct_equivalent_bytes
        )
        if not math.isclose(
            self.packing_savings_rate,
            expected_packing_rate,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise ValueError(
                "packing_savings_rate must use direct-equivalent denominator"
            )
        counts = (
            self.active_source_count,
            self.available_source_count,
            self.retained_source_count,
            self.released_source_count,
            self.released_control_source_count,
            self.known_released_payload_bytes,
            self.pack_proposal_count,
            self.codec_attempts,
            self.emitted_pack_count,
            self.direct_fallback_count,
            self.encoder_full_state_evaluations,
        )
        if any(value < 0 for value in counts):
            raise ValueError("recomposition counters must be nonnegative")
        if (
            self.retained_source_count + self.released_source_count
            != self.active_source_count
        ):
            raise ValueError("retained/released counts must partition active sources")
        if self.released_control_source_count > self.released_source_count:
            raise ValueError("released-control sources must be released")
        if self.released_control_source_count != (
            self.active_source_count - self.available_source_count
        ):
            raise ValueError(
                "released-control sources must equal the unavailable active sources"
            )
        if self.emitted_pack_count + self.direct_fallback_count != self.codec_attempts:
            raise ValueError("codec outcomes must partition attempts")
        if any(
            not math.isfinite(value) or value < 0
            for value in (self.active_weight, self.retained_weight, self.omitted_weight)
        ):
            raise ValueError("recomposition weights must be finite and nonnegative")
        if not math.isclose(
            self.retained_weight + self.omitted_weight,
            self.active_weight,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("retained/omitted weights must partition active weight")
        expected_omission_rate = (
            self.omitted_weight / self.active_weight if self.active_weight else 0.0
        )
        if not math.isclose(
            self.weighted_omission_rate,
            expected_omission_rate,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise ValueError("weighted omission must use active-weight denominator")
        self.optimizer_work.__post_init__()


@dataclass(frozen=True, slots=True)
class RecompositionResultV2:
    """Atomic transition result; invalid candidates never escape on rollback."""

    state: CapsuleStateV2 | None
    outcome: RecompositionOutcomeV2
    committed: bool
    consumed_delta: bool
    gate: GateReportV2
    selection: OptimizerSelectionV2 | None
    metrics: RecompositionMetricsV2 | None
    encoded_plan: EncodedPlanV2 | None

    def __post_init__(self) -> None:
        self.gate.__post_init__()
        if not isinstance(self.outcome, RecompositionOutcomeV2):
            raise ValueError("outcome must be RecompositionOutcomeV2")
        if self.committed:
            if self.state is None or not self.consumed_delta or not self.gate.valid:
                raise ValueError(
                    "committed result requires state, delta, and valid gate"
                )
        else:
            if self.consumed_delta or self.gate.valid:
                raise ValueError("rollback cannot consume delta or pass the gate")
            if self.encoded_plan is not None:
                raise ValueError("invalid encoded candidate must not escape rollback")
        if self.outcome is RecompositionOutcomeV2.ROLLED_BACK and self.committed:
            raise ValueError("rolled-back result cannot be committed")
        if self.outcome is RecompositionOutcomeV2.NOOP:
            if (
                not self.committed
                or self.selection is not None
                or self.encoded_plan is not None
            ):
                raise ValueError(
                    "noop must commit the original state without solver output"
                )
        if self.selection is not None:
            self.selection.__post_init__()
        if self.metrics is not None:
            self.metrics.__post_init__()
