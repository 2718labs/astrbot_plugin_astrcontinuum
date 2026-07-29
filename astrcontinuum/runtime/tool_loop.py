"""Pure durable tool-loop validation for Journal event deltas.

This module deliberately consumes only immutable :class:`EventEnvelope` values and
the canonical JSON metadata persisted in tool events.  It never inspects host
messages, provider thinking sidecars, or process-local pairing state.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum

from ..domain import EventEnvelope, EventType, SessionKey


class ToolLoopState(str, Enum):
    """The only durable states accepted by the request and finalizer gates."""

    STABLE = "stable"
    AWAITING_RESULTS = "awaiting_results"
    AWAITING_ASSISTANT = "awaiting_assistant"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True, repr=False)
class ToolFingerprint:
    """Canonical durable identity of one tool call/result pairing."""

    tool: str
    arguments: str


@dataclass(frozen=True, slots=True)
class PendingTool:
    """One multiset entry, kept content-redacted in diagnostics."""

    fingerprint: ToolFingerprint = field(repr=False)
    count: int


@dataclass(frozen=True, slots=True)
class ToolLoopAssessment:
    """Pure FSM output; invalid assessments never carry pending facts forward."""

    state: ToolLoopState
    pending: tuple[PendingTool, ...] = field(default=(), repr=False)

    @property
    def is_stable(self) -> bool:
        return self.state is ToolLoopState.STABLE


@dataclass(frozen=True, slots=True)
class _ParsedToolLoop:
    """Single-pass FSM result shared by state checks and durable grouping."""

    assessment: ToolLoopAssessment
    units: tuple[tuple[EventEnvelope, ...], ...] | None


def _invalid() -> ToolLoopAssessment:
    return ToolLoopAssessment(state=ToolLoopState.INVALID)


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-finite JSON constant")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _canonical_json(value: object) -> str | None:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        return None


def _contains_lossy_marker(value: object) -> bool:
    """Reject adapter bounds that could make distinct tool arguments collide."""

    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                return True
            normalized = key.casefold().lstrip("$").replace("-", "_")
            if normalized in {"truncated", "truncated_items"}:
                return True
            if _contains_lossy_marker(nested):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_lossy_marker(item) for item in value)
    return False


def _tool_fingerprint(event: EventEnvelope) -> ToolFingerprint | None:
    expected_kind = "call" if event.event_type is EventType.TOOL_CALL else "result"
    if not isinstance(event.content, str):
        return None
    try:
        metadata = json.loads(
            event.content,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(metadata, dict)
        or metadata.get("kind") != expected_kind
        or _contains_lossy_marker(metadata)
    ):
        return None
    if "tool" not in metadata or "arguments" not in metadata:
        # The bounded fallback format has only kind/truncation metadata. It cannot
        # establish a durable pairing, so the caller must fail closed.
        return None
    tool = metadata["tool"]
    if not isinstance(tool, str) or not tool.strip():
        return None
    canonical_arguments = _canonical_json(metadata["arguments"])
    if canonical_arguments is None:
        return None
    return ToolFingerprint(tool=tool, arguments=canonical_arguments)


def _pending_entries(pending: Counter[ToolFingerprint]) -> tuple[PendingTool, ...]:
    return tuple(
        PendingTool(fingerprint=fingerprint, count=count)
        for fingerprint, count in sorted(
            pending.items(),
            key=lambda item: (item[0].tool, item[0].arguments),
        )
        if count > 0
    )


def _materialize_events(events: Iterable[EventEnvelope]) -> tuple[EventEnvelope, ...] | None:
    try:
        iterator = iter(events)
    except Exception:  # noqa: BLE001 - a broken durable cursor is unsafe
        return None
    materialized: list[EventEnvelope] = []
    while True:
        try:
            materialized.append(next(iterator))
        except StopIteration:
            return tuple(materialized)
        except Exception:  # noqa: BLE001 - a broken durable cursor is unsafe
            return None


def _parse_events(events: tuple[EventEnvelope, ...]) -> _ParsedToolLoop:
    """Run the one durable FSM and emit complete continuation units on closure."""

    state = ToolLoopState.STABLE
    pending: Counter[ToolFingerprint] = Counter()
    previous_sequence: int | None = None
    session_key: SessionKey | None = None
    units: list[tuple[EventEnvelope, ...]] = []
    start_index: int | None = None

    for index, event in enumerate(events):
        if not isinstance(event, EventEnvelope):
            return _ParsedToolLoop(assessment=_invalid(), units=None)
        if previous_sequence is not None and event.sequence != previous_sequence + 1:
            return _ParsedToolLoop(assessment=_invalid(), units=None)
        previous_sequence = event.sequence
        if session_key is None:
            session_key = event.session_key
        elif event.session_key != session_key:
            return _ParsedToolLoop(assessment=_invalid(), units=None)

        event_type = event.event_type
        if event_type not in {
            EventType.USER_MESSAGE,
            EventType.ASSISTANT_MESSAGE,
            EventType.TOOL_CALL,
            EventType.TOOL_RESULT,
        }:
            return _ParsedToolLoop(assessment=_invalid(), units=None)

        fingerprint = (
            _tool_fingerprint(event)
            if event_type in {EventType.TOOL_CALL, EventType.TOOL_RESULT}
            else None
        )
        if event_type in {EventType.TOOL_CALL, EventType.TOOL_RESULT} and fingerprint is None:
            return _ParsedToolLoop(assessment=_invalid(), units=None)

        if state is ToolLoopState.STABLE:
            if event_type is EventType.TOOL_CALL:
                if fingerprint is None:
                    return _ParsedToolLoop(assessment=_invalid(), units=None)
                start_index = index
                pending[fingerprint] += 1
                state = ToolLoopState.AWAITING_RESULTS
            elif event_type is EventType.TOOL_RESULT:
                return _ParsedToolLoop(assessment=_invalid(), units=None)
            else:
                units.append((event,))
            continue

        if state is ToolLoopState.AWAITING_RESULTS:
            if event_type is EventType.TOOL_CALL:
                if fingerprint is None:
                    return _ParsedToolLoop(assessment=_invalid(), units=None)
                pending[fingerprint] += 1
                continue
            if event_type is EventType.TOOL_RESULT:
                if fingerprint is None or pending[fingerprint] < 1:
                    return _ParsedToolLoop(assessment=_invalid(), units=None)
                pending[fingerprint] -= 1
                if pending[fingerprint] == 0:
                    del pending[fingerprint]
                if not pending:
                    state = ToolLoopState.AWAITING_ASSISTANT
                continue
            return _ParsedToolLoop(assessment=_invalid(), units=None)

        if state is ToolLoopState.AWAITING_ASSISTANT:
            if event_type is EventType.ASSISTANT_MESSAGE:
                if start_index is None:
                    return _ParsedToolLoop(assessment=_invalid(), units=None)
                units.append(events[start_index : index + 1])
                start_index = None
                state = ToolLoopState.STABLE
                continue
            if event_type is EventType.TOOL_CALL:
                if fingerprint is None:
                    return _ParsedToolLoop(assessment=_invalid(), units=None)
                pending[fingerprint] += 1
                state = ToolLoopState.AWAITING_RESULTS
                continue
            return _ParsedToolLoop(assessment=_invalid(), units=None)

        return _ParsedToolLoop(assessment=_invalid(), units=None)

    assessment = ToolLoopAssessment(state=state, pending=_pending_entries(pending))
    return _ParsedToolLoop(
        assessment=assessment,
        units=tuple(units) if state is ToolLoopState.STABLE else None,
    )


def evaluate_tool_loop(events: Iterable[EventEnvelope]) -> ToolLoopAssessment:
    """Evaluate the durable tool protocol as a fail-closed finite-state machine.

    Matching is a multiset of canonical ``(tool, arguments)`` values, not callback
    order.  Any malformed metadata, unexpected event kind, or impossible transition
    produces the absorbing ``invalid`` state.
    """

    materialized = _materialize_events(events)
    if materialized is None:
        return _invalid()
    return _parse_events(materialized).assessment


def durable_event_units(
    events: Iterable[EventEnvelope],
) -> tuple[tuple[EventEnvelope, ...], ...] | None:
    """Partition a stable Journal history into indivisible durable units.

    Ordinary events are singleton units. A tool unit spans every call/result in
    one completed continuation and its closing assistant event. Malformed,
    truncated, mismatched, or open input returns ``None``: callers must not
    manufacture a partial tool unit from it.
    """

    materialized = _materialize_events(events)
    if materialized is None:
        return None
    parsed = _parse_events(materialized)
    if parsed.assessment.state is not ToolLoopState.STABLE:
        return None
    return parsed.units
