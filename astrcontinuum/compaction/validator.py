from __future__ import annotations

from collections.abc import Sequence

from .. import domain as _domain
from ..domain.capsules import ContextCapsuleEnvelope
from ..domain.events import EventEnvelope
from ..domain.snapshots import SnapshotEnvelope
from ..domain.validation import PermanentValidationReport


def validate_candidate(
    *,
    base_snapshot: SnapshotEnvelope | None,
    base_capsules: Sequence[ContextCapsuleEnvelope],
    candidate_snapshot: SnapshotEnvelope,
    candidate_capsules: Sequence[ContextCapsuleEnvelope],
    source_events: Sequence[EventEnvelope],
    target_high_water_mark: int,
    token_ceiling: int,
) -> PermanentValidationReport:
    return _domain.validate_permanent(
        previous_snapshot=base_snapshot,
        previous_capsules=base_capsules,
        candidate_snapshot=candidate_snapshot,
        candidate_capsules=candidate_capsules,
        source_events=source_events,
        target_high_water_mark=target_high_water_mark,
        token_ceiling=token_ceiling,
    )
