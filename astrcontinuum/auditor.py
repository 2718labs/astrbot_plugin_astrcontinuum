from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from .models import AuditReport, ClaimStatus, ContextEvent, Snapshot


class DeterministicLossAuditor:
    """Mechanical checks must pass before optional semantic audit."""

    async def audit(
        self, previous: Snapshot | None, candidate: Snapshot, covered_events: Sequence[ContextEvent]
    ) -> AuditReport:
        del previous
        event_ids = {event.event_id for event in covered_events}
        anchors = [
            anchor
            for capsule in candidate.capsules
            for anchor in capsule.exact_anchors
            if anchor.status == ClaimStatus.ACTIVE
        ]
        claims = [
            claim
            for capsule in candidate.capsules
            for claim in (*capsule.claims, *capsule.decisions)
            if claim.status == ClaimStatus.ACTIVE
        ]
        missing_anchor_sources = [
            a.anchor_id for a in anchors if a.source_event_id not in event_ids
        ]
        unsupported = [
            c.claim_id
            for c in claims
            if not c.source_event_ids
            or any(source not in event_ids for source in c.source_event_ids)
        ]
        duplicates = [text for text, count in Counter(c.text for c in claims).items() if count > 1]
        issues: list[str] = []
        if missing_anchor_sources:
            issues.append(f"missing anchor sources: {missing_anchor_sources}")
        if unsupported:
            issues.append(f"unsupported claims: {unsupported}")
        if duplicates:
            issues.append(f"duplicate active claims: {duplicates}")
        source_coverage = (
            sum(bool(c.source_event_ids) for c in claims) / len(claims) if claims else 1.0
        )
        passed = not issues and source_coverage == 1.0
        return AuditReport(
            passed,
            1.0 if not missing_anchor_sources else 0.0,
            1.0,
            1.0,
            source_coverage,
            len(duplicates),
            len(unsupported),
            sum(max(1, len(e.content) // 4) for e in covered_events),
            sum(c.token_cost for c in candidate.capsules),
            tuple(issues),
        )
