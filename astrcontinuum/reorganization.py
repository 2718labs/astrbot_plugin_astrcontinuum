"""Deterministic capsule reorganization for the production domain.

The reorganization boundary is deliberately local and model-free.  It copies
structured domain items into one immutable capsule, truncates optional text
only when the requested budget requires it, and records every omitted item as
an explicit release.  Exact anchors and dependencies are core edges: they are
never silently dropped.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from math import ceil
from typing import Any, TypeVar

from .domain import (
    CapsuleAnchor,
    CapsuleClaim,
    CapsuleLevel,
    CapsuleQuality,
    ContextCapsuleEnvelope,
    Decision,
    Dependency,
    Entity,
)


class ReorganizationStatus(str, Enum):
    """Disposition of one source item in the new capsule."""

    RETAINED = "retained"
    APPROXIMATE = "approximate"
    RELEASED = "released"


@dataclass(frozen=True, slots=True)
class ReorganizationRecord:
    """Immutable audit row for one source item."""

    kind: str
    item_id: str
    source_capsule_id: str
    status: ReorganizationStatus
    before_tokens: int
    after_tokens: int
    required: bool


@dataclass(frozen=True, slots=True)
class ReorganizationResult:
    """New capsule plus deterministic loss accounting."""

    capsule: ContextCapsuleEnvelope
    records: tuple[ReorganizationRecord, ...]
    before_tokens: int
    after_tokens: int
    loss_count: int
    release_count: int

    @property
    def retained_count(self) -> int:
        return sum(record.status is ReorganizationStatus.RETAINED for record in self.records)

    @property
    def approximate_count(self) -> int:
        return sum(record.status is ReorganizationStatus.APPROXIMATE for record in self.records)

    @property
    def compression_ratio(self) -> float:
        """Return resident after/before tokens, with an empty-input identity."""

        if self.before_tokens == 0:
            return 1.0
        return self.after_tokens / self.before_tokens

    @property
    def reduction_ratio(self) -> float:
        """Return the fraction of source tokens released from the resident view."""

        return 1.0 - self.compression_ratio if self.before_tokens else 0.0


class ReorganizationBudgetError(ValueError):
    """Raised when mandatory exact edges cannot fit the requested budget."""

    def __init__(self, *, required_tokens: int, budget: int) -> None:
        self.required_tokens = required_tokens
        self.budget = budget
        super().__init__(
            "reorganization budget cannot retain exact anchors and dependencies "
            f"(required={required_tokens}, budget={budget})"
        )


class ReorganizationInvariantError(ValueError):
    """Raised when one logical item has conflicting immutable source values."""


_T = TypeVar("_T")
_BASE_OVERHEAD_TOKENS = 8
_MIN_APPROXIMATE_CHARS = 8
_LEVEL_ORDER = {
    CapsuleLevel.MICRO: 0,
    CapsuleLevel.EPISODE: 1,
    CapsuleLevel.TASK: 2,
    CapsuleLevel.GLOBAL: 3,
}


def reorganize_capsules(
    capsules: Sequence[ContextCapsuleEnvelope],
    *,
    token_budget: int,
) -> ReorganizationResult:
    """Deterministically fold ``capsules`` into one bounded capsule.

    No provider, model, or generated summary is consulted.  Optional
    structured values are retained in canonical order, shortened by a fixed
    character rule when necessary, or released with an audit row.  Core exact
    anchors and dependencies must fit in full or the operation fails closed.
    """

    source = _validate_inputs(capsules, token_budget)
    ordered = tuple(sorted(source, key=lambda item: item.capsule_id))
    session_key = ordered[0].session_key
    if any(item.session_key != session_key for item in ordered[1:]):
        raise ValueError("all capsules must use one session key")

    before_tokens = sum(item.token_cost for item in ordered)
    records: list[ReorganizationRecord] = []
    selected: dict[str, list[Any]] = {
        "goals": [],
        "constraints": [],
        "decisions": [],
        "progress": [],
        "open_loops": [],
        "preferences": [],
        "entities": [],
        "emotional_context": [],
        "exact_anchors": [],
        "dependencies": [],
    }
    seen: dict[tuple[str, str], object] = {}
    used_tokens = _BASE_OVERHEAD_TOKENS

    # Core edges are planned first and are always copied byte-for-byte.
    for capsule in ordered:
        for kind, field_name, items in (
            ("exact_anchor", "exact_anchors", capsule.exact_anchors),
            ("dependency", "dependencies", capsule.dependencies),
        ):
            for item in items:
                item_id = _item_id(item)
                identity = (kind, item_id)
                if identity in seen:
                    if seen[identity] != item:
                        raise ReorganizationInvariantError(
                            f"conflicting {kind} identity: {item_id}"
                        )
                    continue
                seen[identity] = item
                item_tokens = _item_tokens(kind, item)
                used_tokens += item_tokens
                selected[field_name].append(item)
                records.append(
                    ReorganizationRecord(
                        kind=kind,
                        item_id=item_id,
                        source_capsule_id=capsule.capsule_id,
                        status=ReorganizationStatus.RETAINED,
                        before_tokens=item_tokens,
                        after_tokens=item_tokens,
                        required=True,
                    )
                )

    if used_tokens > token_budget:
        raise ReorganizationBudgetError(
            required_tokens=used_tokens,
            budget=token_budget,
        )

    optional_fields = (
        ("goal", "goals"),
        ("constraint", "constraints"),
        ("decision", "decisions"),
        ("progress", "progress"),
        ("open_loop", "open_loops"),
        ("preference", "preferences"),
        ("entity", "entities"),
        ("emotional_context", "emotional_context"),
    )
    for capsule in ordered:
        for kind, field_name in optional_fields:
            for item in getattr(capsule, field_name):
                item_id = _item_id(item)
                identity = (kind, item_id)
                if identity in seen:
                    if seen[identity] != item:
                        raise ReorganizationInvariantError(
                            f"conflicting {kind} identity: {item_id}"
                        )
                    continue
                seen[identity] = item
                before_item_tokens = _item_tokens(kind, item)
                remaining = token_budget - used_tokens
                if before_item_tokens <= remaining:
                    selected[field_name].append(item)
                    used_tokens += before_item_tokens
                    records.append(
                        ReorganizationRecord(
                            kind=kind,
                            item_id=item_id,
                            source_capsule_id=capsule.capsule_id,
                            status=ReorganizationStatus.RETAINED,
                            before_tokens=before_item_tokens,
                            after_tokens=before_item_tokens,
                            required=False,
                        )
                    )
                    continue

                approximate = _approximate_item(item, kind, remaining)
                approximate_tokens = (
                    _item_tokens(kind, approximate) if approximate is not None else 0
                )
                if approximate is not None and approximate_tokens <= remaining:
                    selected[field_name].append(approximate)
                    used_tokens += approximate_tokens
                    records.append(
                        ReorganizationRecord(
                            kind=kind,
                            item_id=item_id,
                            source_capsule_id=capsule.capsule_id,
                            status=ReorganizationStatus.APPROXIMATE,
                            before_tokens=before_item_tokens,
                            after_tokens=approximate_tokens,
                            required=False,
                        )
                    )
                else:
                    records.append(
                        ReorganizationRecord(
                            kind=kind,
                            item_id=item_id,
                            source_capsule_id=capsule.capsule_id,
                            status=ReorganizationStatus.RELEASED,
                            before_tokens=before_item_tokens,
                            after_tokens=0,
                            required=False,
                        )
                    )

    # A narrative field is required by the domain envelope, but it is not a
    # summary channel.  Preserve only a fixed structural marker and account
    # for all source prose as released rather than inventing replacement prose.
    for capsule in ordered:
        summary_tokens = _text_tokens(capsule.narrative_summary)
        records.append(
            ReorganizationRecord(
                kind="narrative_summary",
                item_id=f"{capsule.capsule_id}:narrative_summary",
                source_capsule_id=capsule.capsule_id,
                status=ReorganizationStatus.RELEASED,
                before_tokens=summary_tokens,
                after_tokens=0,
                required=False,
            )
        )

    records_tuple = tuple(records)
    capsule_id = _new_capsule_id(ordered, token_budget, records_tuple)
    all_source_event_ids = tuple(
        sorted({event_id for capsule in ordered for event_id in capsule.source_event_ids})
    )
    represented_source_event_ids = tuple(
        sorted(
            {
                event_id
                for field_name, values in selected.items()
                for item in values
                for event_id in getattr(item, "source_event_ids", ())
            }
        )
    )
    source_coverage = (
        len(set(represented_source_event_ids)) / len(set(all_source_event_ids))
        if all_source_event_ids
        else 1.0
    )
    release_count = sum(record.status is ReorganizationStatus.RELEASED for record in records_tuple)
    loss_count = sum(record.status is not ReorganizationStatus.RETAINED for record in records_tuple)
    result_capsule = ContextCapsuleEnvelope(
        capsule_id=capsule_id,
        schema_version="2.1.0",
        level=max((item.level for item in ordered), key=_LEVEL_ORDER.__getitem__),
        session_key=session_key,
        covered_event_start=min(item.covered_event_start for item in ordered),
        covered_event_end=max(item.covered_event_end for item in ordered),
        source_event_ids=all_source_event_ids,
        goals=tuple(selected["goals"]),
        constraints=tuple(selected["constraints"]),
        decisions=tuple(selected["decisions"]),
        progress=tuple(selected["progress"]),
        open_loops=tuple(selected["open_loops"]),
        preferences=tuple(selected["preferences"]),
        entities=tuple(selected["entities"]),
        emotional_context=tuple(selected["emotional_context"]),
        exact_anchors=tuple(selected["exact_anchors"]),
        dependencies=tuple(selected["dependencies"]),
        narrative_summary="reorganized capsule",
        token_cost=used_tokens,
        quality=CapsuleQuality(
            mechanical_passed=True,
            source_coverage=source_coverage,
            anchor_recall=1.0,
            unsupported_critical_claims=0,
            coverage_gap=sum(
                record.status is ReorganizationStatus.RELEASED
                and record.kind != "narrative_summary"
                for record in records_tuple
            ),
        ),
        created_at=max(item.created_at for item in ordered),
    )
    return ReorganizationResult(
        capsule=result_capsule,
        records=records_tuple,
        before_tokens=before_tokens,
        after_tokens=used_tokens,
        loss_count=loss_count,
        release_count=release_count,
    )


def _validate_inputs(
    capsules: Sequence[ContextCapsuleEnvelope], token_budget: object
) -> tuple[ContextCapsuleEnvelope, ...]:
    if isinstance(token_budget, bool) or not isinstance(token_budget, int) or token_budget < 0:
        raise ValueError("token_budget must be a non-negative integer")
    if not isinstance(capsules, Sequence) or isinstance(capsules, (str, bytes)):
        raise TypeError("capsules must be a sequence of ContextCapsuleEnvelope")
    source = tuple(capsules)
    if not source:
        raise ValueError("capsules must not be empty")
    if any(type(item) is not ContextCapsuleEnvelope for item in source):
        raise TypeError("capsules must contain ContextCapsuleEnvelope values")
    return source


def _item_id(item: object) -> str:
    for field_name in ("anchor_id", "dependency_id", "claim_id", "decision_id", "entity_id"):
        value = getattr(item, field_name, None)
        if isinstance(value, str):
            return value
    raise TypeError(f"unsupported capsule item: {type(item).__name__}")


def _text_tokens(text: str) -> int:
    return max(1, ceil(len(text) / 4))


def _item_tokens(kind: str, item: object) -> int:
    if isinstance(item, CapsuleClaim):
        return _text_tokens(item.text)
    if isinstance(item, Decision):
        return _text_tokens(
            item.text
            + item.rationale
            + "".join(item.alternatives)
            + "".join(item.supersedes)
            + item.rejected_because
        )
    if isinstance(item, Entity):
        return _text_tokens(item.kind + item.canonical_name + "".join(item.aliases))
    if isinstance(item, CapsuleAnchor):
        return _text_tokens(item.exact_text)
    if isinstance(item, Dependency):
        return _text_tokens(item.kind + item.target_id)
    raise TypeError(f"unsupported {kind} item: {type(item).__name__}")


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return text[:1]
    return text[: max_chars - 1] + "…"


def _approximate_item(item: _T, kind: str, remaining_tokens: int) -> _T | None:
    if remaining_tokens < _text_tokens("x" * _MIN_APPROXIMATE_CHARS):
        return None
    max_chars = max(_MIN_APPROXIMATE_CHARS, remaining_tokens * 4)
    if isinstance(item, CapsuleClaim):
        return item.model_copy(update={"text": _truncate(item.text, max_chars)})  # type: ignore[return-value]
    if isinstance(item, Decision):
        return item.model_copy(  # type: ignore[return-value]
            update={
                "text": _truncate(item.text, max_chars),
                "rationale": _truncate(item.rationale, max_chars),
                "alternatives": tuple(_truncate(value, max_chars) for value in item.alternatives),
                "supersedes": tuple(_truncate(value, max_chars) for value in item.supersedes),
                "rejected_because": _truncate(item.rejected_because, max_chars),
            }
        )
    if isinstance(item, Entity):
        return item.model_copy(  # type: ignore[return-value]
            update={
                "kind": _truncate(item.kind, max_chars),
                "canonical_name": _truncate(item.canonical_name, max_chars),
                "aliases": tuple(_truncate(value, max_chars) for value in item.aliases),
            }
        )
    return None


def _new_capsule_id(
    capsules: tuple[ContextCapsuleEnvelope, ...],
    token_budget: int,
    records: tuple[ReorganizationRecord, ...],
) -> str:
    payload = "|".join(
        (
            str(token_budget),
            *(item.capsule_id for item in capsules),
            *(
                f"{record.kind}:{record.item_id}:{record.status.value}:{record.after_tokens}"
                for record in records
            ),
        )
    )
    return "reorg-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


__all__ = [
    "ReorganizationBudgetError",
    "ReorganizationInvariantError",
    "ReorganizationRecord",
    "ReorganizationResult",
    "ReorganizationStatus",
    "reorganize_capsules",
]
