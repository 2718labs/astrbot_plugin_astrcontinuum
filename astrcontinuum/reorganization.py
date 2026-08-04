"""Deterministic capsule reorganization for the production domain.

The reorganization boundary is deliberately local and model-free.  It copies
structured domain items into one immutable capsule, truncates optional text
only when the requested budget requires it, and records every omitted item as
an explicit release.  Exact anchors and dependencies are core edges: they are
never silently dropped.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
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
from .runtime.budget import Utf8ByteTokenCounter
from .runtime.types import TokenCounter


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
    """Worker-local lossy artifact plus deterministic loss accounting.

    This result is not a publishable snapshot.  Callers must route its capsule
    and records through the existing permanent validator and publication gate;
    this module intentionally does not relax either safety floor.
    """

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


def canonicalize_reorganization_records(
    records: Sequence[ReorganizationRecord],
) -> tuple[ReorganizationRecord, ...]:
    """Validate closed audit records while preserving caller order and values."""

    canonical_records: list[ReorganizationRecord] = []
    source_item_keys: set[tuple[str, str, str]] = set()
    for record in records:
        if type(record) is not ReorganizationRecord:
            raise ReorganizationInvariantError("reorganization record must be exact")
        _require_nonempty_record_identity(record.kind, "kind")
        _require_nonempty_record_identity(record.item_id, "item_id")
        _require_nonempty_record_identity(record.source_capsule_id, "source_capsule_id")
        if type(record.status) is not ReorganizationStatus:
            raise ReorganizationInvariantError("reorganization record status must be exact")
        _require_nonnegative_record_tokens(record.before_tokens, "before_tokens")
        _require_nonnegative_record_tokens(record.after_tokens, "after_tokens")
        if type(record.required) is not bool:
            raise ReorganizationInvariantError("reorganization record required flag must be exact")
        if record.required and record.status is not ReorganizationStatus.RETAINED:
            raise ReorganizationInvariantError("required reorganization record must be retained")

        source_item_key = (record.source_capsule_id, record.kind, record.item_id)
        if source_item_key in source_item_keys:
            raise ReorganizationInvariantError("duplicate reorganization record source item")
        source_item_keys.add(source_item_key)
        canonical_records.append(record)
    return tuple(canonical_records)


def _require_nonempty_record_identity(value: object, field_name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ReorganizationInvariantError(
            f"reorganization record {field_name} must be a non-empty string"
        )


def _require_nonnegative_record_tokens(value: object, field_name: str) -> None:
    if type(value) is not int or value < 0:
        raise ReorganizationInvariantError(
            f"reorganization record {field_name} must be a non-negative integer"
        )


def reorganize_capsules(
    capsules: Sequence[ContextCapsuleEnvelope],
    *,
    token_budget: int,
    counter: TokenCounter | None = None,
) -> ReorganizationResult:
    """Deterministically fold ``capsules`` into one bounded capsule.

    No provider, model, or generated summary is consulted.  Optional
    structured values are retained in canonical order, shortened by a fixed
    character rule when necessary, or released with an audit row.  Core exact
    anchors and dependencies must fit in full or the operation fails closed.
    The returned value is a worker-local artifact and must not bypass the
    permanent validator/publication gate.
    """

    source = _validate_inputs(capsules, token_budget)
    token_counter = counter if counter is not None else Utf8ByteTokenCounter()
    _validate_counter(token_counter)
    ordered = tuple(sorted(source, key=lambda item: item.capsule_id))
    session_key = ordered[0].session_key
    if any(item.session_key != session_key for item in ordered[1:]):
        raise ValueError("all capsules must use one session key")

    # ``token_cost`` is not part of the counted surface: including it would
    # make the final value recursive.  Every other canonical field, including
    # metadata and narrative_summary, is counted as UTF-8 through one counter.
    before_tokens = sum(_count_text(token_counter, _capsule_surface(item)) for item in ordered)
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
                item_tokens = _item_tokens(token_counter, kind, item)
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

    # Summary rows are intentionally released: this worker-local artifact has
    # no summary-generation fallback.  They still carry canonical accounting.
    for capsule in ordered:
        records.append(
            ReorganizationRecord(
                kind="narrative_summary",
                item_id=f"{capsule.capsule_id}:narrative_summary",
                source_capsule_id=capsule.capsule_id,
                status=ReorganizationStatus.RELEASED,
                before_tokens=_count_text(
                    token_counter,
                    _canonical_json({"narrative_summary": capsule.narrative_summary}),
                ),
                after_tokens=0,
                required=False,
            )
        )

    mandatory_tokens = _provisional_after_tokens(
        ordered,
        selected,
        records,
        token_budget=token_budget,
        token_counter=token_counter,
    )
    if mandatory_tokens > token_budget:
        raise ReorganizationBudgetError(
            required_tokens=mandatory_tokens,
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
                before_item_tokens = _item_tokens(token_counter, kind, item)
                retained_record = ReorganizationRecord(
                    kind=kind,
                    item_id=item_id,
                    source_capsule_id=capsule.capsule_id,
                    status=ReorganizationStatus.RETAINED,
                    before_tokens=before_item_tokens,
                    after_tokens=before_item_tokens,
                    required=False,
                )
                selected[field_name].append(item)
                candidate_tokens = _provisional_after_tokens(
                    ordered,
                    selected,
                    records + [retained_record],
                    token_budget=token_budget,
                    token_counter=token_counter,
                )
                if candidate_tokens <= token_budget:
                    records.append(retained_record)
                    continue
                selected[field_name].pop()

                approximate, approximate_tokens = _fit_approximate_item(
                    item,
                    kind,
                    field_name,
                    selected,
                    records,
                    ordered,
                    token_budget=token_budget,
                    token_counter=token_counter,
                )
                if approximate is not None:
                    selected[field_name].append(approximate)
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
                    continue
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

    records_tuple = tuple(records)
    result_capsule = _build_capsule(
        ordered,
        selected,
        records_tuple,
        token_budget=token_budget,
        token_counter=token_counter,
    )
    used_tokens = _count_text(token_counter, _capsule_surface(result_capsule))
    if used_tokens > token_budget:
        raise ReorganizationBudgetError(
            required_tokens=used_tokens,
            budget=token_budget,
        )
    result_capsule = result_capsule.model_copy(update={"token_cost": used_tokens})
    release_count = sum(record.status is ReorganizationStatus.RELEASED for record in records_tuple)
    loss_count = sum(record.status is not ReorganizationStatus.RETAINED for record in records_tuple)
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
    item_fields = (
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
    for capsule in source:
        valid_event_ids = set(capsule.source_event_ids)
        for field_name in item_fields:
            for item in getattr(capsule, field_name):
                item_event_ids = getattr(item, "source_event_ids", ())
                if not set(item_event_ids).issubset(valid_event_ids):
                    raise ValueError(
                        f"{field_name} item source_event_ids must be contained in "
                        "capsule source_event_ids"
                    )
    return source


def _item_id(item: object) -> str:
    for field_name in ("anchor_id", "dependency_id", "claim_id", "decision_id", "entity_id"):
        value = getattr(item, field_name, None)
        if isinstance(value, str):
            return value
    raise TypeError(f"unsupported capsule item: {type(item).__name__}")


def _validate_counter(counter: object) -> None:
    if not callable(getattr(counter, "count_text", None)):
        raise TypeError("counter must expose callable count_text(text)")


def _count_text(counter: TokenCounter, text: str) -> int:
    try:
        value = counter.count_text(text)
    except Exception as error:
        raise ValueError("token counter failed") from error
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("token counter must return a non-negative integer")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _capsule_surface(capsule: ContextCapsuleEnvelope) -> str:
    """Canonical counted surface excluding recursive ``token_cost``."""

    return _canonical_json(capsule.model_dump(mode="json", exclude={"token_cost"}))


def _item_surface(item: object) -> str:
    model_dump = getattr(item, "model_dump", None)
    if not callable(model_dump):
        raise TypeError(f"unsupported capsule item: {type(item).__name__}")
    return _canonical_json(model_dump(mode="json"))


def _item_tokens(counter: TokenCounter, kind: str, item: object) -> int:
    if not isinstance(item, (CapsuleClaim, Decision, Entity, CapsuleAnchor, Dependency)):
        raise TypeError(f"unsupported {kind} item: {type(item).__name__}")
    return _count_text(counter, _item_surface(item))


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return text[:1]
    return text[: max_chars - 1] + "…"


def _approximate_item(item: _T, kind: str, max_chars: int) -> _T | None:
    if max_chars < _MIN_APPROXIMATE_CHARS:
        return None
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


def _fit_approximate_item(
    item: _T,
    kind: str,
    field_name: str,
    selected: dict[str, list[Any]],
    records: list[ReorganizationRecord],
    capsules: tuple[ContextCapsuleEnvelope, ...],
    *,
    token_budget: int,
    token_counter: TokenCounter,
) -> tuple[_T | None, int]:
    """Find the longest deterministic approximation that fits exactly."""

    max_chars = max(
        (
            len(getattr(item, "text", "")),
            len(getattr(item, "rationale", "")),
            len(getattr(item, "canonical_name", "")),
        ),
        default=0,
    )
    low = _MIN_APPROXIMATE_CHARS
    high = max_chars - 1
    best: tuple[_T, int] | None = None
    item_id = _item_id(item)
    source_capsule_id = next(
        capsule.capsule_id
        for capsule in capsules
        if any(item_id == _item_id(candidate) for candidate in getattr(capsule, field_name))
    )
    while low <= high:
        candidate_chars = (low + high) // 2
        candidate = _approximate_item(item, kind, candidate_chars)
        if candidate is None or candidate == item:
            high = candidate_chars - 1
            continue
        after_tokens = _item_tokens(token_counter, kind, candidate)
        candidate_selected = {name: list(values) for name, values in selected.items()}
        candidate_selected[field_name].append(candidate)
        candidate_record = ReorganizationRecord(
            kind=kind,
            item_id=item_id,
            source_capsule_id=source_capsule_id,
            status=ReorganizationStatus.APPROXIMATE,
            before_tokens=_item_tokens(token_counter, kind, item),
            after_tokens=after_tokens,
            required=False,
        )
        candidate_tokens = _provisional_after_tokens(
            capsules,
            candidate_selected,
            records + [candidate_record],
            token_budget=token_budget,
            token_counter=token_counter,
        )
        if candidate_tokens <= token_budget:
            best = (candidate, after_tokens)
            low = candidate_chars + 1
        else:
            high = candidate_chars - 1
    return best if best is not None else (None, 0)


def _provisional_after_tokens(
    capsules: tuple[ContextCapsuleEnvelope, ...],
    selected: dict[str, list[Any]],
    records: tuple[ReorganizationRecord, ...] | list[ReorganizationRecord],
    *,
    token_budget: int,
    token_counter: TokenCounter,
) -> int:
    provisional = _build_capsule(
        capsules,
        selected,
        tuple(records),
        token_budget=token_budget,
        token_counter=token_counter,
    )
    return _count_text(token_counter, _capsule_surface(provisional))


def _build_capsule(
    capsules: tuple[ContextCapsuleEnvelope, ...],
    selected: dict[str, list[Any]],
    records: tuple[ReorganizationRecord, ...],
    *,
    token_budget: int,
    token_counter: TokenCounter,
) -> ContextCapsuleEnvelope:
    del token_counter  # The counted surface is applied by the caller.
    session_key = capsules[0].session_key
    all_source_event_ids = tuple(
        sorted({event_id for capsule in capsules for event_id in capsule.source_event_ids})
    )
    represented_source_event_ids = {
        event_id
        for values in selected.values()
        for item in values
        for event_id in getattr(item, "source_event_ids", ())
    }
    source_coverage = (
        min(1.0, len(represented_source_event_ids) / len(set(all_source_event_ids)))
        if all_source_event_ids
        else 1.0
    )
    release_count = sum(
        record.status is ReorganizationStatus.RELEASED and record.kind != "narrative_summary"
        for record in records
    )
    capsule_id = _new_capsule_id(capsules, token_budget, records)
    return ContextCapsuleEnvelope(
        capsule_id=capsule_id,
        schema_version="2.1.0",
        level=max((item.level for item in capsules), key=_LEVEL_ORDER.__getitem__),
        session_key=session_key,
        covered_event_start=min(item.covered_event_start for item in capsules),
        covered_event_end=max(item.covered_event_end for item in capsules),
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
        token_cost=0,
        quality=CapsuleQuality(
            mechanical_passed=True,
            source_coverage=source_coverage,
            anchor_recall=1.0,
            unsupported_critical_claims=0,
            coverage_gap=release_count,
        ),
        created_at=max(item.created_at for item in capsules),
    )


def _new_capsule_id(
    capsules: tuple[ContextCapsuleEnvelope, ...],
    token_budget: int,
    records: tuple[ReorganizationRecord, ...],
) -> str:
    payload = "|".join(
        (
            str(token_budget),
            _canonical_json([item.model_dump(mode="json") for item in capsules]),
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
    "canonicalize_reorganization_records",
    "reorganize_capsules",
]
