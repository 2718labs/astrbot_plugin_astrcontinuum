from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Protocol

from ..domain.capsules import ContextCapsuleEnvelope
from ..domain.events import EventEnvelope
from ..domain.snapshots import SnapshotEnvelope
from ..domain.validation import PermanentValidationReport
from ..storage.repository import SnapshotCapsuleMembership
from ..tokenization import ContextLimitSource, TokenizerMode


class SegmentBoundaryReason(str, Enum):
    PREFERRED = "PREFERRED"
    MAX_EVENTS = "MAX_EVENTS"
    MAX_TOKENS = "MAX_TOKENS"
    OVERSIZED_EVENT = "OVERSIZED_EVENT"
    END_OF_INPUT = "END_OF_INPUT"


@dataclass(frozen=True, slots=True)
class SegmenterConfig:
    max_events_per_segment: int = 16
    max_tokens_per_segment: int = 4096
    max_preferred_boundaries: int = 128

    def __post_init__(self) -> None:
        if not _is_non_bool_int(self.max_events_per_segment) or self.max_events_per_segment <= 0:
            raise ValueError("SEGMENTER_INVALID_MAX_EVENTS")
        if not _is_non_bool_int(self.max_tokens_per_segment) or self.max_tokens_per_segment <= 0:
            raise ValueError("SEGMENTER_INVALID_MAX_TOKENS")
        if not _is_non_bool_int(self.max_preferred_boundaries) or self.max_preferred_boundaries < 0:
            raise ValueError("SEGMENTER_INVALID_MAX_PREFERRED_BOUNDARIES")


@dataclass(frozen=True, slots=True)
class EventSegment:
    start_sequence: int
    end_sequence: int
    events: tuple[EventEnvelope, ...]
    token_cost: int
    boundary_reason: SegmentBoundaryReason


class CompilerErrorCode(str, Enum):
    BASE_INVALID = "COMPILER_BASE_INVALID"
    BASE_CAPSULE_MISMATCH = "COMPILER_BASE_CAPSULE_MISMATCH"
    SOURCE_EVENTS_EMPTY = "COMPILER_SOURCE_EVENTS_EMPTY"
    SOURCE_EVENTS_INVALID = "COMPILER_SOURCE_EVENTS_INVALID"
    SOURCE_SESSION_MISMATCH = "COMPILER_SOURCE_SESSION_MISMATCH"
    SOURCE_COVERAGE_MISMATCH = "COMPILER_SOURCE_COVERAGE_MISMATCH"
    TARGET_INVALID = "COMPILER_TARGET_INVALID"
    TOKEN_CEILING_INVALID = "COMPILER_TOKEN_CEILING_INVALID"
    NOW_INVALID = "COMPILER_NOW_INVALID"
    SEGMENTER_CONFIG_INVALID = "COMPILER_SEGMENTER_CONFIG_INVALID"
    BACKEND_FAILURE = "COMPILER_BACKEND_FAILURE"
    BACKEND_OUTPUT_INVALID = "COMPILER_BACKEND_OUTPUT_INVALID"
    BACKEND_OUTPUT_EMPTY = "COMPILER_BACKEND_OUTPUT_EMPTY"
    TOKEN_COUNTER_FAILURE = "COMPILER_TOKEN_COUNTER_FAILURE"
    TOKEN_COUNTER_INVALID = "COMPILER_TOKEN_COUNTER_INVALID"
    PERMANENT_VALIDATOR_FAILURE = "COMPILER_PERMANENT_VALIDATOR_FAILURE"
    PERMANENT_VALIDATION_FAILED = "COMPILER_PERMANENT_VALIDATION_FAILED"
    REORGANIZATION_FAILED = "COMPILER_REORGANIZATION_FAILED"


class CompilerInvariantError(ValueError):
    def __init__(
        self,
        code: CompilerErrorCode,
        *,
        report: PermanentValidationReport | None = None,
    ) -> None:
        self.code = code
        self.report = report
        message_codes = [code.value]
        if report is not None:
            message_codes.extend(item.value for item in report.failure_codes)
        super().__init__("|".join(message_codes))


class CompilerBackendDeferred(RuntimeError):
    """A safe backend deferral that must not consume the retry budget."""

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("compiler deferral code must be non-empty")
        self.code = code.strip()
        super().__init__(self.code)


@dataclass(frozen=True, slots=True, repr=False)
class CompactionProviderBinding:
    """One content-hidden Provider snapshot used by a single compaction run."""

    provider_id: str = field(repr=False)
    model_identity: str | None = field(repr=False)
    context_limit: int
    context_limit_source: ContextLimitSource

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ValueError("provider_id must be non-empty")
        if self.model_identity is not None and not isinstance(self.model_identity, str):
            raise TypeError("model_identity must be a string or None")
        if (
            isinstance(self.context_limit, bool)
            or not isinstance(self.context_limit, int)
            or self.context_limit < 1
        ):
            raise ValueError("context_limit must be a positive integer")
        if not isinstance(self.context_limit_source, ContextLimitSource):
            raise TypeError("context_limit_source must be a ContextLimitSource")


@dataclass(frozen=True, slots=True)
class CompactionFitProvenance:
    """Content-free provenance for the all-segment preflight decision."""

    tokenizer_profile_id: str
    tokenizer_mode: str
    fallback_code: str
    primary_result_discarded: bool

    def __post_init__(self) -> None:
        for name in ("tokenizer_profile_id", "tokenizer_mode", "fallback_code"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be non-empty")
        try:
            TokenizerMode(self.tokenizer_mode)
        except ValueError:
            raise ValueError("tokenizer_mode must be a stable TokenizerMode value") from None
        if type(self.primary_result_discarded) is not bool:
            raise TypeError("primary_result_discarded must be a boolean")


@dataclass(frozen=True, slots=True)
class CompilationRequest:
    base_snapshot: SnapshotEnvelope | None
    base_capsules: tuple[ContextCapsuleEnvelope, ...]
    source_events: tuple[EventEnvelope, ...]
    segments: tuple[EventSegment, ...]
    target_high_water_mark: int
    token_ceiling: int
    canonical_event_token_counts: Mapping[str, int] = field(
        default_factory=dict,
        repr=False,
    )
    provider_binding: CompactionProviderBinding | None = field(
        default=None,
        repr=False,
    )
    provider_binding_captured: bool = False

    def __post_init__(self) -> None:
        counts = dict(self.canonical_event_token_counts)
        if any(
            not isinstance(event_id, str)
            or not event_id
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            for event_id, count in counts.items()
        ):
            raise ValueError("canonical_event_token_counts must contain non-negative integers")
        object.__setattr__(
            self,
            "canonical_event_token_counts",
            MappingProxyType(counts),
        )
        if self.provider_binding is not None and not isinstance(
            self.provider_binding,
            CompactionProviderBinding,
        ):
            raise TypeError("provider_binding must be a CompactionProviderBinding or None")
        if type(self.provider_binding_captured) is not bool:
            raise TypeError("provider_binding_captured must be a boolean")


@dataclass(frozen=True, slots=True)
class CompilerOutput:
    capsules: tuple[ContextCapsuleEnvelope, ...]
    rendered_context: str
    fitted_segments: tuple[EventSegment, ...] | None = None
    fit_provenance: CompactionFitProvenance | None = None


class CompilerBackend(Protocol):
    async def compile(self, request: CompilationRequest) -> CompilerOutput: ...


@dataclass(frozen=True, slots=True)
class CompilationCandidate:
    snapshot: SnapshotEnvelope
    memberships: tuple[SnapshotCapsuleMembership, ...]
    segments: tuple[EventSegment, ...]
    permanent_report: PermanentValidationReport
    fit_provenance: CompactionFitProvenance | None = None


@dataclass(frozen=True, slots=True)
class SemanticAuditRequest:
    snapshot: SnapshotEnvelope
    capsules: tuple[ContextCapsuleEnvelope, ...]
    segments: tuple[EventSegment, ...]
    permanent_report: PermanentValidationReport


@dataclass(frozen=True, slots=True)
class SemanticAuditReport:
    passed: bool
    failure_codes: tuple[str, ...]


class SemanticAuditBackend(Protocol):
    async def audit(self, request: SemanticAuditRequest) -> SemanticAuditReport: ...


@dataclass(frozen=True, slots=True)
class AuditedCandidate:
    snapshot: SnapshotEnvelope
    memberships: tuple[SnapshotCapsuleMembership, ...]
    segments: tuple[EventSegment, ...]
    permanent_report: PermanentValidationReport
    semantic_report: SemanticAuditReport | None


class SemanticAuditErrorCode(str, Enum):
    MECHANICAL_INVALID = "SEMANTIC_AUDIT_MECHANICAL_INVALID"
    STRICT_AUDIT_INVALID = "SEMANTIC_AUDIT_STRICT_AUDIT_INVALID"
    BACKEND_REQUIRED = "SEMANTIC_AUDIT_BACKEND_REQUIRED"
    BACKEND_FAILURE = "SEMANTIC_AUDIT_BACKEND_FAILURE"
    BACKEND_OUTPUT_INVALID = "SEMANTIC_AUDIT_BACKEND_OUTPUT_INVALID"
    SEMANTIC_REJECTED = "SEMANTIC_AUDIT_SEMANTIC_REJECTED"


class SemanticAuditInvariantError(ValueError):
    __slots__ = ("_code", "_failed_candidate")

    def __init__(
        self,
        code: SemanticAuditErrorCode,
        *,
        failed_candidate: AuditedCandidate | None = None,
    ) -> None:
        self._code = code
        self._failed_candidate = failed_candidate
        super().__init__(code.value)

    @property
    def code(self) -> SemanticAuditErrorCode:
        return self._code

    @property
    def failed_candidate(self) -> AuditedCandidate | None:
        return self._failed_candidate


def _is_non_bool_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
