from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class ClaimStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class ContextEvent:
    event_id: str
    session_id: str
    sequence: int
    event_type: str
    role: str
    content: str
    created_at: float
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    kind: str
    text: str
    status: ClaimStatus
    confidence: float
    source_event_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExactAnchor:
    anchor_id: str
    anchor_type: str
    exact_text: str
    source_event_id: str
    status: ClaimStatus = ClaimStatus.ACTIVE
    importance: float = 1.0


@dataclass(frozen=True, slots=True)
class ContextCapsule:
    capsule_id: str
    level: Literal["micro", "episode", "task", "global"]
    session_id: str
    covered_event_start: int
    covered_event_end: int
    claims: tuple[Claim, ...] = ()
    decisions: tuple[Claim, ...] = ()
    exact_anchors: tuple[ExactAnchor, ...] = ()
    dependencies: tuple[str, ...] = ()
    narrative_summary: str = ""
    token_cost: int = 0
    source_coverage: float = 0.0


@dataclass(frozen=True, slots=True)
class Snapshot:
    snapshot_id: str
    session_id: str
    version: int
    covered_event_seq: int
    capsules: tuple[ContextCapsule, ...]
    committed: bool
    created_at: float
    base_version: int | None = None


@dataclass(frozen=True, slots=True)
class AuditReport:
    passed: bool
    critical_anchor_recall: float
    constraint_recall: float
    open_loop_recall: float
    source_coverage: float
    contradiction_count: int
    unsupported_claim_count: int
    token_before: int
    token_after: int
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AssemblyTrace:
    session_id: str
    snapshot_version: int | None
    covered_event_seq: int
    latest_event_seq: int
    total_tokens: int
    slot_tokens: dict[str, int]
    degraded_mode: str | None = None
