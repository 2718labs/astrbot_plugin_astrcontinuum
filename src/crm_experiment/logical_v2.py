"""Self-contained source resolution and winner-frontier updates for CRM v2."""

from __future__ import annotations

from collections import defaultdict

from crm_experiment.canonical import utf8_bytes
from crm_experiment.contracts import AtomStatus
from crm_experiment.contracts_v2 import (
    ActiveRecordV2,
    CapsuleStateV2,
    DeltaEnvelopeV2,
    KeyWinnerV2,
    LogicalResolutionV2,
    SourceReceiptV2,
)


def decode_logical_state_v2(state: CapsuleStateV2) -> tuple[ActiveRecordV2, ...]:
    """Decode direct-only resident payloads without changing source records."""
    records = state.kernel + tuple(block.record for block in state.body)
    return tuple(sorted(records, key=lambda record: record.atom.source_id))


def _rank(winner: KeyWinnerV2) -> tuple[int, str]:
    return winner.revision, winner.source_id


def resolve_latest_v2(
    base_state: CapsuleStateV2 | None,
    envelope: DeltaEnvelopeV2,
    *,
    key_registry_limit: int | None = None,
    max_semantic_key_bytes: int | None = None,
) -> LogicalResolutionV2:
    """Atomically resolve a complete delta interval against the frozen frontier."""
    if base_state is None:
        if envelope.base_high_water != 0:
            raise ValueError("initial delta base_high_water must equal zero")
        if key_registry_limit is None or max_semantic_key_bytes is None:
            raise ValueError("initial resolution requires explicit key registry bounds")
        if key_registry_limit <= 0 or max_semantic_key_bytes <= 0:
            raise ValueError("key registry bounds must be positive")
        base_records: tuple[ActiveRecordV2, ...] = ()
        frontier_by_key: dict[str, KeyWinnerV2] = {}
        receipt_by_source: dict[str, SourceReceiptV2] = {}
    else:
        if envelope.base_high_water != base_state.high_water:
            raise ValueError("delta base_high_water does not match frozen state")
        if key_registry_limit not in (None, base_state.key_registry_limit):
            raise ValueError("key_registry_limit cannot change during resolution")
        if max_semantic_key_bytes not in (None, base_state.max_semantic_key_bytes):
            raise ValueError("max_semantic_key_bytes cannot change during resolution")
        key_registry_limit = base_state.key_registry_limit
        max_semantic_key_bytes = base_state.max_semantic_key_bytes
        base_records = decode_logical_state_v2(base_state)
        frontier_by_key = {
            winner.semantic_key: winner for winner in base_state.frontier
        }
        receipt_by_source = {
            receipt.source_id: receipt for receipt in base_state.receipts
        }

    assert key_registry_limit is not None
    assert max_semantic_key_bytes is not None
    replay_only = envelope.target_high_water == envelope.base_high_water
    payload_by_source = {record.atom.source_id: record.atom for record in base_records}
    if not replay_only:
        for atom in envelope.records:
            payload_by_source[atom.source_id] = atom
            receipt_by_source[atom.source_id] = SourceReceiptV2.from_atom(atom)
            for semantic_key in atom.semantic_keys:
                if utf8_bytes(semantic_key) > max_semantic_key_bytes:
                    raise ValueError("semantic key exceeds max_semantic_key_bytes")
                proposed = KeyWinnerV2.from_atom(atom, semantic_key)
                previous = frontier_by_key.get(semantic_key)
                if previous is None or _rank(proposed) > _rank(previous):
                    frontier_by_key[semantic_key] = proposed

    frontier = tuple(
        sorted(frontier_by_key.values(), key=lambda winner: winner.semantic_key)
    )
    if len(frontier) > key_registry_limit:
        raise ValueError("frontier exceeds key_registry_limit")
    frontier_source_ids = {winner.source_id for winner in frontier}
    receipts = tuple(
        sorted(
            (receipt_by_source[source_id] for source_id in frontier_source_ids),
            key=lambda receipt: receipt.source_id,
        )
    )

    keys_by_source: dict[str, list[str]] = defaultdict(list)
    for winner in frontier:
        if winner.status is AtomStatus.ACTIVE:
            keys_by_source[winner.source_id].append(winner.semantic_key)

    active_records: list[ActiveRecordV2] = []
    for source_id in sorted(keys_by_source):
        atom = payload_by_source.get(source_id)
        if atom is None:
            continue
        active_records.append(
            ActiveRecordV2(atom, tuple(sorted(keys_by_source[source_id])))
        )

    active_source_ids = set(keys_by_source)
    receipt_by_source_id = {receipt.source_id: receipt for receipt in receipts}
    for source_id in active_source_ids:
        for dependency in receipt_by_source_id[source_id].depends_on:
            if dependency not in active_source_ids:
                raise ValueError(
                    f"active dependency missing: {source_id}->{dependency}"
                )

    return LogicalResolutionV2(
        active_records=tuple(active_records),
        frontier=frontier,
        receipts=receipts,
        high_water=envelope.target_high_water,
    )


def active_weight_v2(state: CapsuleStateV2) -> float:
    """Return active logical weight, including released non-core payloads."""
    return sum(item.weight for item in state.weight_policy.source_weights)
