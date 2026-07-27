from __future__ import annotations

from astrcontinuum.runtime.types import CandidateBlock, CandidateKind, RuntimeSlot


def candidate(
    block_id: str,
    *,
    required: bool = False,
    source_event_ids: tuple[str, ...] | None = None,
    text: str | None = None,
) -> CandidateBlock:
    return CandidateBlock(
        block_id=block_id,
        slot=RuntimeSlot.RELEVANT_EVIDENCE,
        kind=CandidateKind.DECISION,
        text=text or f"private-content-{block_id}",
        source_event_ids=source_event_ids or (f"event-{block_id}",),
        score=1.0,
        reason="query",
        required=required,
        capsule_id=None,
        event_sequence=None,
    )
