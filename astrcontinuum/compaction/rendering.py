from __future__ import annotations

from ..domain import (
    AnchorStatus,
    ContextCapsuleEnvelope,
    SemanticStatus,
)


def render_capsule(capsule: ContextCapsuleEnvelope) -> str:
    lines = [
        (
            f"[CAPSULE {capsule.capsule_id} "
            f"EVENTS {capsule.covered_event_start}-{capsule.covered_event_end}]"
        )
    ]
    claim_groups = (
        ("GOAL", capsule.goals),
        ("CONSTRAINT", capsule.constraints),
        ("PROGRESS", capsule.progress),
        ("OPEN_LOOP", capsule.open_loops),
        ("PREFERENCE", capsule.preferences),
        ("EMOTIONAL_CONTEXT", capsule.emotional_context),
    )
    for label, group in claim_groups:
        lines.extend(
            f"{label}: {item.text} [source:{','.join(item.source_event_ids)}]"
            for item in group
            if item.status is SemanticStatus.ACTIVE
        )
    for item in capsule.decisions:
        if item.status is not SemanticStatus.ACTIVE:
            continue
        lines.append(f"DECISION: {item.text} [source:{','.join(item.source_event_ids)}]")
        if item.rationale:
            lines.append(f"RATIONALE: {item.rationale}")
        lines.extend(f"ALTERNATIVE: {value}" for value in item.alternatives)
        if item.rejected_because:
            lines.append(f"REJECTED_BECAUSE: {item.rejected_because}")
    lines.extend(
        (f"ENTITY: {item.canonical_name} ({item.kind}) [source:{','.join(item.source_event_ids)}]")
        for item in capsule.entities
    )
    lines.extend(
        (
            f"EXACT_{item.anchor_type.value.upper()}: {item.exact_text} "
            f"[source:{','.join(item.source_event_ids)}]"
        )
        for item in capsule.exact_anchors
        if item.status is AnchorStatus.ACTIVE
    )
    return "\n".join(lines)
