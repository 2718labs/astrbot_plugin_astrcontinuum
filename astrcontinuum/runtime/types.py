from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ..domain import SessionKey
from ..storage import RequestView


class RequestViewErrorCode(str, Enum):
    """Stable, content-free request-view validation failures."""

    TYPE_INVALID = "REQUEST_VIEW_TYPE_INVALID"
    SESSION_MISMATCH = "REQUEST_VIEW_SESSION_MISMATCH"
    BOOTSTRAP_INVALID = "REQUEST_VIEW_BOOTSTRAP_INVALID"
    SNAPSHOT_INVALID = "REQUEST_VIEW_SNAPSHOT_INVALID"
    COVERAGE_INVALID = "REQUEST_VIEW_COVERAGE_INVALID"
    DELTA_GAP = "REQUEST_VIEW_DELTA_GAP"
    DELTA_IDENTITY_MISMATCH = "REQUEST_VIEW_DELTA_IDENTITY_MISMATCH"


class RequestViewSource(Protocol):
    """Narrow durable read port used by the request-side runtime."""

    def read_request_view(self, session_key: SessionKey) -> RequestView: ...


class RuntimeSlot(str, Enum):
    """Frozen provider-projection priority slots."""

    ACTIVE_GOAL = "active_goal"
    HARD_CONSTRAINT = "hard_constraint"
    RAW_DELTA = "raw_delta"
    EXACT_ANCHOR = "exact_anchor"
    RECENT_RAW = "recent_raw"
    ACTIVE_TASK = "active_task"
    RELEVANT_EVIDENCE = "relevant_evidence"
    EPISODE_BACKGROUND = "episode_background"
    GLOBAL_BACKGROUND = "global_background"


RUNTIME_SLOT_PRIORITY: tuple[RuntimeSlot, ...] = (
    RuntimeSlot.ACTIVE_GOAL,
    RuntimeSlot.HARD_CONSTRAINT,
    RuntimeSlot.RAW_DELTA,
    RuntimeSlot.EXACT_ANCHOR,
    RuntimeSlot.RECENT_RAW,
    RuntimeSlot.ACTIVE_TASK,
    RuntimeSlot.RELEVANT_EVIDENCE,
    RuntimeSlot.EPISODE_BACKGROUND,
    RuntimeSlot.GLOBAL_BACKGROUND,
)


class CandidateKind(str, Enum):
    """AC-owned structured source represented by one candidate block."""

    GOAL = "goal"
    CONSTRAINT = "constraint"
    RAW_EVENT = "raw_event"
    EXACT_ANCHOR = "exact_anchor"
    TASK_CONTEXT = "task_context"
    DECISION = "decision"
    ENTITY = "entity"
    DEPENDENCY = "dependency"
    EPISODE_BACKGROUND = "episode_background"
    GLOBAL_BACKGROUND = "global_background"


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    """Explicit bounds and deterministic fallback-scoring weights."""

    max_query_characters: int = 2_048
    max_capsules: int = 64
    max_delta_events: int = 128
    max_candidates: int = 256
    max_items_per_field: int = 64
    max_lexical_characters_per_candidate: int = 4_096
    goal_weight: float = 100.0
    constraint_weight: float = 100.0
    raw_delta_weight: float = 90.0
    exact_anchor_weight: float = 80.0
    task_weight: float = 50.0
    decision_weight: float = 40.0
    entity_weight: float = 30.0
    dependency_weight: float = 30.0
    background_weight: float = 10.0
    lexical_weight: float = 10.0

    def __post_init__(self) -> None:
        non_negative_limits = (
            "max_query_characters",
            "max_capsules",
            "max_delta_events",
        )
        positive_limits = (
            "max_candidates",
            "max_items_per_field",
            "max_lexical_characters_per_candidate",
        )
        for field_name in non_negative_limits:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        for field_name in positive_limits:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        weights = (
            self.goal_weight,
            self.constraint_weight,
            self.raw_delta_weight,
            self.exact_anchor_weight,
            self.task_weight,
            self.decision_weight,
            self.entity_weight,
            self.dependency_weight,
            self.background_weight,
            self.lexical_weight,
        )
        if any(not math.isfinite(value) or value < 0 for value in weights):
            raise ValueError("retrieval weights must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class CandidateBlock:
    """One immutable AC-owned projection candidate."""

    block_id: str
    slot: RuntimeSlot
    kind: CandidateKind
    text: str
    source_event_ids: tuple[str, ...]
    score: float
    reason: str
    required: bool
    capsule_id: str | None
    event_sequence: int | None

    def __post_init__(self) -> None:
        if not self.block_id:
            raise ValueError("block_id must be non-empty")
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if not self.source_event_ids:
            raise ValueError("source_event_ids must be non-empty")
        if len(set(self.source_event_ids)) != len(self.source_event_ids):
            raise ValueError("source_event_ids must be unique")
        if not math.isfinite(self.score) or self.score < 0:
            raise ValueError("score must be finite and non-negative")
        if not self.reason:
            raise ValueError("reason must be non-empty")
        if self.event_sequence is not None and self.event_sequence < 1:
            raise ValueError("event_sequence must be positive")


class TokenCounter(Protocol):
    """Canonical replaceable tokenizer boundary."""

    def count_text(self, text: str) -> int: ...


class AssemblyMode(str, Enum):
    """Projection-only budget mode."""

    NORMAL = "NORMAL"
    EMERGENCY_ASSEMBLY = "EMERGENCY_ASSEMBLY"


class BudgetErrorCode(str, Enum):
    """Stable, content-free budget failures."""

    CURRENT_INPUT_INVALID = "CURRENT_INPUT_INVALID"
    OPAQUE_TOKEN_COST_INVALID = "OPAQUE_TOKEN_COST_INVALID"
    FIXED_REQUIRED_COST_INVALID = "FIXED_REQUIRED_COST_INVALID"
    TOKEN_COUNTER_INVALID = "TOKEN_COUNTER_INVALID"
    TOKEN_COUNTER_FAILURE = "TOKEN_COUNTER_FAILURE"
    REQUIRED_INPUT_EXCEEDS_BUDGET = "REQUIRED_INPUT_EXCEEDS_BUDGET"
    INTERNAL_BUDGET_INVARIANT = "INTERNAL_BUDGET_INVARIANT"


@dataclass(frozen=True, slots=True)
class BudgetConfig:
    """Canonical request-input budget configuration."""

    target_input_budget: int = 130_000
    hard_input_ceiling: int = 150_000
    model_context_limit: int = 200_000
    reserved_output_and_tools: int = 32_000
    safety_margin: int = 2_000

    def __post_init__(self) -> None:
        positive_fields = (
            "target_input_budget",
            "hard_input_ceiling",
            "model_context_limit",
        )
        non_negative_fields = (
            "reserved_output_and_tools",
            "safety_margin",
        )
        for field_name in positive_fields:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        for field_name in non_negative_fields:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.reserved_output_and_tools > self.model_context_limit:
            raise ValueError("reserved_output_and_tools must not exceed model_context_limit")


@dataclass(frozen=True, slots=True)
class BlockSelection:
    """Content-free trace record for one selected block."""

    block_id: str
    slot: RuntimeSlot
    source_event_ids: tuple[str, ...]
    token_cost: int
    score: float
    reason: str


@dataclass(frozen=True, slots=True)
class BlockRejection:
    """Content-free trace record for one omitted block."""

    block_id: str
    slot: RuntimeSlot
    source_event_ids: tuple[str, ...]
    token_cost: int
    score: float
    reason: str


@dataclass(frozen=True, slots=True)
class AssemblyTrace:
    """Content-free budget arithmetic and selection trace."""

    mode: AssemblyMode
    snapshot_id: str | None
    pointer_version: int
    covered_event_end: int
    high_water_mark: int
    b_input: int
    current_input_cost: int
    fixed_required_cost: int
    safety_margin: int
    b_required: int
    opaque_token_cost: int
    b_ac: int
    ac_selected_cost: int
    projection_overhead_cost: int
    total_input_cost: int
    slot_token_costs: tuple[tuple[RuntimeSlot, int], ...]
    selections: tuple[BlockSelection, ...]
    rejections: tuple[BlockRejection, ...]


@dataclass(frozen=True, slots=True)
class AssemblyResult:
    """Selected AC-owned content plus its content-free trace."""

    projected_text: str
    selected_blocks: tuple[CandidateBlock, ...]
    trace: AssemblyTrace
