from __future__ import annotations

from enum import Enum

from pydantic import AwareDatetime, Field, field_validator, model_validator

from ._base import (
    FrozenEnvelope,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UnitFloat,
    ensure_unique,
)
from .identity import SessionKey


class CapsuleLevel(str, Enum):
    MICRO = "micro"
    EPISODE = "episode"
    TASK = "task"
    GLOBAL = "global"


class SemanticStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"
    UNCERTAIN = "uncertain"


class AnchorStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class AnchorType(str, Enum):
    NAME = "name"
    NUMBER = "number"
    DATE = "date"
    PATH = "path"
    URL = "url"
    CODE = "code"
    FORMULA = "formula"
    PROMPT = "prompt"
    CONSTRAINT = "constraint"
    COMMITMENT = "commitment"
    MANUAL = "manual"
    OTHER = "other"


class CapsuleClaim(FrozenEnvelope):
    claim_id: NonEmptyStr
    text: NonEmptyStr
    status: SemanticStatus
    confidence: UnitFloat
    source_event_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @field_validator("source_event_ids")
    @classmethod
    def validate_unique_sources(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return ensure_unique(values, "source_event_ids")


class Decision(FrozenEnvelope):
    decision_id: NonEmptyStr
    text: NonEmptyStr
    status: SemanticStatus
    confidence: UnitFloat
    source_event_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    rationale: str
    alternatives: tuple[str, ...]
    supersedes: tuple[str, ...]
    rejected_because: str

    @field_validator("source_event_ids", "alternatives", "supersedes")
    @classmethod
    def validate_unique_items(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return ensure_unique(values, "decision list")


class Entity(FrozenEnvelope):
    entity_id: NonEmptyStr
    kind: NonEmptyStr
    canonical_name: NonEmptyStr
    aliases: tuple[str, ...]
    source_event_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @field_validator("aliases", "source_event_ids")
    @classmethod
    def validate_unique_items(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return ensure_unique(values, "entity list")


class CapsuleAnchor(FrozenEnvelope):
    anchor_id: NonEmptyStr
    anchor_type: AnchorType
    exact_text: NonEmptyStr
    source_event_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    status: AnchorStatus
    importance: UnitFloat

    @field_validator("source_event_ids")
    @classmethod
    def validate_unique_sources(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return ensure_unique(values, "source_event_ids")


class Dependency(FrozenEnvelope):
    dependency_id: NonEmptyStr
    kind: NonEmptyStr
    target_id: NonEmptyStr
    source_event_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @field_validator("source_event_ids")
    @classmethod
    def validate_unique_sources(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return ensure_unique(values, "source_event_ids")


class CapsuleQuality(FrozenEnvelope):
    mechanical_passed: bool
    source_coverage: UnitFloat
    anchor_recall: UnitFloat
    unsupported_critical_claims: NonNegativeInt
    coverage_gap: NonNegativeInt


class ContextCapsuleEnvelope(FrozenEnvelope):
    capsule_id: NonEmptyStr
    schema_version: NonEmptyStr
    level: CapsuleLevel
    session_key: SessionKey
    covered_event_start: PositiveInt
    covered_event_end: PositiveInt
    source_event_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    goals: tuple[CapsuleClaim, ...]
    constraints: tuple[CapsuleClaim, ...]
    decisions: tuple[Decision, ...]
    progress: tuple[CapsuleClaim, ...]
    open_loops: tuple[CapsuleClaim, ...]
    preferences: tuple[CapsuleClaim, ...]
    entities: tuple[Entity, ...]
    emotional_context: tuple[CapsuleClaim, ...]
    exact_anchors: tuple[CapsuleAnchor, ...]
    dependencies: tuple[Dependency, ...]
    narrative_summary: NonEmptyStr
    token_cost: NonNegativeInt
    quality: CapsuleQuality
    created_at: AwareDatetime

    @field_validator(
        "source_event_ids",
        "goals",
        "constraints",
        "decisions",
        "progress",
        "open_loops",
        "preferences",
        "entities",
        "emotional_context",
        "exact_anchors",
        "dependencies",
    )
    @classmethod
    def validate_unique_items(cls, values: tuple[object, ...]) -> tuple[object, ...]:
        return ensure_unique(values, "capsule list")

    @model_validator(mode="after")
    def validate_coverage_order(self) -> ContextCapsuleEnvelope:
        if self.covered_event_start > self.covered_event_end:
            raise ValueError("covered_event_start must not exceed covered_event_end")
        return self
