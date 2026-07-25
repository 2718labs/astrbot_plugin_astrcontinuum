from __future__ import annotations

import uuid
from typing import Sequence

from .models import Claim, ClaimStatus, ContextCapsule, ContextEvent, Snapshot


class ScaffoldCapsuleCompiler:
    """Wiring-only compiler. Replace with schema-constrained semantic compiler."""

    async def compile(self, session_id: str, base_snapshot: Snapshot | None, events: Sequence[ContextEvent]) -> tuple[ContextCapsule, ...]:
        del base_snapshot
        if not events:
            return ()
        claims = tuple(Claim(str(uuid.uuid4()), "fact", event.content, ClaimStatus.ACTIVE, 1.0, (event.event_id,)) for event in events)
        return (ContextCapsule(str(uuid.uuid4()), "micro", session_id, events[0].sequence, events[-1].sequence, claims=claims, narrative_summary="Scaffold capsule; replace with real compiler.", token_cost=sum(max(1, len(e.content)//4) for e in events), source_coverage=1.0),)
