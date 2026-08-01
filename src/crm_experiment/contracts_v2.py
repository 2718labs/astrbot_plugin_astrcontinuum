"""Self-contained logical and resident contracts for CRM state version 2."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Self

from crm_experiment.canonical import canonical_json, sha256_text, utf8_bytes
from crm_experiment.contracts import AtomRole, AtomStatus

_SOURCE_PAYLOAD_DOMAIN = "crm-v2-source-payload/v1"
_SOURCE_RECORD_DOMAIN = "crm-v2-source-record/v2"
_WEIGHT_POLICY_DOMAIN = "crm-v2-weight-policy/v1"
_SOURCE_ID_PREFIX = "sha256:"


def _valid_sha256(value: str) -> bool:
    return (
        len(value) == 64
        and value.lower() == value
        and all(character in "0123456789abcdef" for character in value)
    )


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
    body: tuple[DirectBlockV2, ...]

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

        if self.kernel != tuple(
            sorted(
                self.kernel,
                key=lambda record: (record.atom.role.value, record.atom.source_id),
            )
        ):
            raise ValueError("kernel sources must be canonically sorted")
        if self.body != tuple(
            sorted(self.body, key=lambda block: block.record.atom.source_id)
        ):
            raise ValueError("body sources must be canonically sorted")
        records = self.kernel + tuple(block.record for block in self.body)
        source_ids = tuple(record.atom.source_id for record in records)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("resident source IDs must be unique")
        if any(not record.atom.core_required for record in self.kernel):
            raise ValueError("resident kernel atoms must be core_required")
        if any(block.record.atom.core_required for block in self.body):
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
        roles = tuple(slot.role for slot in self.slots)
        if len(roles) != len(set(roles)):
            raise ValueError("kernel schema roles must be unique")
        if self.continuity_floor_bytes <= 0:
            raise ValueError("continuity_floor_bytes must be positive")


@dataclass(frozen=True, slots=True)
class KernelSelectionV2:
    records: tuple[ActiveRecordV2, ...]
    resident_bytes: int | None
    valid: bool
    reasons: tuple[str, ...]
