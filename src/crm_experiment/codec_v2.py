"""Deterministic depth-one physical packing for frozen CRM v2 states."""

from __future__ import annotations

from dataclasses import dataclass, replace

from crm_experiment.canonical import utf8_bytes
from crm_experiment.contracts import AtomRole, AtomStatus
from crm_experiment.contracts_v2 import (
    ActiveRecordV2,
    BodyBlockV2,
    CapsuleStateV2,
    DirectBlockV2,
    PackedContextBlockV2,
    PackedEntryV2,
    canonical_affixes_v2,
)
from crm_experiment.resident_v2 import (
    logical_semantic_hash_v2,
    resident_bytes_v2,
)


class CodecDecodeErrorV2(ValueError):
    """A frozen physical state could not reproduce its logical source records."""


@dataclass(frozen=True, slots=True)
class PackingAttemptV2:
    state: CapsuleStateV2
    emitted: bool
    reason: str
    direct_bytes: int
    packed_bytes: int
    savings_bytes: int
    risk: float = 0.0

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("packing reason must not be empty")
        if self.direct_bytes <= 0 or self.packed_bytes <= 0:
            raise ValueError("packing byte counts must be positive")
        if self.risk != 0.0:
            raise ValueError("lossless packing risk must equal zero")
        if self.emitted:
            if self.reason != "packed":
                raise ValueError("emitted packing must use the packed reason")
            if self.savings_bytes != self.direct_bytes - self.packed_bytes:
                raise ValueError("packing savings must match exact state bytes")
            if self.savings_bytes <= 0:
                raise ValueError("emitted packing must have strict positive savings")
        elif self.savings_bytes != 0:
            raise ValueError("direct fallback must report zero packing savings")


def _decode_error(error: Exception) -> CodecDecodeErrorV2:
    message = str(error)
    if "active_keys" in message:
        return CodecDecodeErrorV2(f"packed active_keys invalid: {message}")
    return CodecDecodeErrorV2(f"packed payload invalid: {message}")


def decode_frozen_state_v2(state: CapsuleStateV2) -> tuple[ActiveRecordV2, ...]:
    """Flatten one self-contained frozen state without consulting prior state."""
    try:
        state.__post_init__()
        receipt_by_source = {receipt.source_id: receipt for receipt in state.receipts}
        records = list(state.kernel)
        for block in state.body:
            if isinstance(block, DirectBlockV2):
                records.append(block.record)
                continue
            if not isinstance(block, PackedContextBlockV2):
                raise ValueError("body contains an unsupported block type")
            for entry in block.entries:
                receipt = receipt_by_source.get(entry.source_id)
                if receipt is None:
                    raise ValueError("packed entry is missing its source receipt")
                records.append(
                    entry.decode(
                        receipt,
                        block.common_prefix,
                        block.common_suffix,
                    )
                )
    except (AttributeError, TypeError, ValueError) as error:
        raise _decode_error(error) from error
    return tuple(sorted(records, key=lambda record: record.atom.source_id))


def direct_view_v2(state: CapsuleStateV2) -> CapsuleStateV2:
    """Return a canonical working view with every body source unpacked."""
    records = decode_frozen_state_v2(state)
    kernel = tuple(
        sorted(
            (record for record in records if record.atom.core_required),
            key=lambda record: (record.atom.role.value, record.atom.source_id),
        )
    )
    body = tuple(
        DirectBlockV2(record) for record in records if not record.atom.core_required
    )
    return replace(state, kernel=kernel, body=body)


def _fallback(
    state: CapsuleStateV2,
    reason: str,
    direct_bytes: int,
    *,
    candidate_bytes: int | None = None,
) -> PackingAttemptV2:
    return PackingAttemptV2(
        state=state,
        emitted=False,
        reason=reason,
        direct_bytes=direct_bytes,
        packed_bytes=candidate_bytes or direct_bytes,
        savings_bytes=0,
    )


def _body_sort_key(block: BodyBlockV2) -> tuple[str, ...]:
    if isinstance(block, DirectBlockV2):
        return (block.record.atom.source_id,)
    return block.source_ids


def _eligible(record: ActiveRecordV2, immutable_ids: set[str]) -> bool:
    atom = record.atom
    return (
        atom.source_id in immutable_ids
        and atom.status is AtomStatus.ACTIVE
        and atom.role is AtomRole.CONTEXT
        and not atom.exact
        and not atom.core_required
        and not atom.depends_on
    )


def attempt_pack_group_v2(
    state: CapsuleStateV2,
    source_ids: tuple[str, ...],
) -> PackingAttemptV2:
    """Try one bounded source group using complete canonical state bytes."""
    direct = direct_view_v2(state)
    direct_bytes = resident_bytes_v2(direct)
    canonical_ids = tuple(sorted(source_ids))
    if len(canonical_ids) != len(set(canonical_ids)):
        return _fallback(direct, "duplicate_source", direct_bytes)
    if len(canonical_ids) < 2:
        return _fallback(direct, "record_limit", direct_bytes)

    policy = direct.packing_policy
    if len(canonical_ids) > policy.max_records_per_block:
        return _fallback(direct, "record_limit", direct_bytes)
    body_records = tuple(block.record for block in direct.body)
    record_by_source = {record.atom.source_id: record for record in body_records}
    selected = tuple(
        record_by_source[source_id]
        for source_id in canonical_ids
        if source_id in record_by_source
    )
    if len(selected) != len(canonical_ids):
        return _fallback(direct, "ineligible_source", direct_bytes)
    body_ids = tuple(record.atom.source_id for record in body_records)
    indexes = tuple(body_ids.index(source_id) for source_id in canonical_ids)
    if indexes != tuple(range(indexes[0], indexes[0] + len(indexes))):
        return _fallback(direct, "noncontiguous_group", direct_bytes)

    immutable_ids = set(policy.immutable_source_ids)
    if any(not _eligible(record, immutable_ids) for record in selected):
        return _fallback(direct, "ineligible_source", direct_bytes)
    if (
        sum(utf8_bytes(record.atom.text) for record in selected)
        > policy.max_decoded_block_bytes
    ):
        return _fallback(direct, "decoded_byte_limit", direct_bytes)

    texts = tuple(record.atom.text for record in selected)
    common_prefix, common_suffix = canonical_affixes_v2(texts)
    if not common_prefix and not common_suffix:
        return _fallback(direct, "no_shared_affix", direct_bytes)
    suffix_length = len(common_suffix)
    entries = tuple(
        PackedEntryV2(
            source_id=record.atom.source_id,
            active_keys=record.active_keys,
            text_middle=record.atom.text[
                len(common_prefix) : (
                    len(record.atom.text) - suffix_length if suffix_length else None
                )
            ],
            provenance=record.atom.provenance,
        )
        for record in selected
    )
    packed = PackedContextBlockV2(
        common_prefix=common_prefix,
        common_suffix=common_suffix,
        entries=entries,
    )
    selected_ids = set(canonical_ids)
    candidate_body = tuple(
        sorted(
            (
                *(
                    block
                    for block in direct.body
                    if block.record.atom.source_id not in selected_ids
                ),
                packed,
            ),
            key=_body_sort_key,
        )
    )
    candidate = replace(direct, body=candidate_body)
    try:
        if decode_frozen_state_v2(candidate) != decode_frozen_state_v2(direct):
            return _fallback(direct, "roundtrip_mismatch", direct_bytes)
    except CodecDecodeErrorV2:
        return _fallback(direct, "roundtrip_mismatch", direct_bytes)
    if logical_semantic_hash_v2(candidate) != logical_semantic_hash_v2(direct):
        return _fallback(direct, "logical_hash_mismatch", direct_bytes)

    candidate_bytes = resident_bytes_v2(candidate)
    if candidate_bytes >= direct_bytes:
        return _fallback(
            direct,
            "no_strict_savings",
            direct_bytes,
            candidate_bytes=candidate_bytes,
        )
    if candidate_bytes > candidate.accepted_budget:
        return _fallback(
            direct,
            "budget_exceeded",
            direct_bytes,
            candidate_bytes=candidate_bytes,
        )
    return PackingAttemptV2(
        state=candidate,
        emitted=True,
        reason="packed",
        direct_bytes=direct_bytes,
        packed_bytes=candidate_bytes,
        savings_bytes=direct_bytes - candidate_bytes,
    )
