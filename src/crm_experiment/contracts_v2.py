"""Self-contained logical and resident contracts for CRM state version 2."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Self

from crm_experiment.canonical import canonical_json, sha256_text, utf8_bytes
from crm_experiment.contracts import AtomRole, AtomStatus

_SOURCE_PAYLOAD_DOMAIN = "crm-v2-source-payload/v1"
_SOURCE_RECORD_DOMAIN = "crm-v2-source-record/v2"
_WEIGHT_POLICY_DOMAIN = "crm-v2-weight-policy/v1"
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
