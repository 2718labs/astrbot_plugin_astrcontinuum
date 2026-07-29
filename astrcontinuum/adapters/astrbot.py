"""Isolated AstrBot host adaptation and verified internal capability probe."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import NoReturn, cast

from ..context_graph import (
    ContextEngineMode,
    LiveContextTrace,
    assemble_live_context,
)
from ..domain import (
    CompactionJobEnvelope,
    EventEnvelope,
    EventType,
    SessionKey,
    SourceHook,
)
from ..runtime import (
    AssemblyResult,
    BudgetConfig,
    CandidateBlock,
    RetrievalConfig,
    TokenCounter,
    ToolLoopAssessment,
    ToolLoopState,
    Utf8ByteTokenCounter,
    evaluate_tool_loop,
    read_request_view,
    select_candidates,
)
from ..storage import CanonicalMetricObservation, RequestView, SQLiteRepository
from ..tokenization import (
    BYTE_FALLBACK,
    CANONICAL_O200K,
    HostBudgetView,
    PreparedBudgetInput,
    RequestBudgetOutcome,
    RequestBudgetProfile,
    RequestBudgetWorkload,
    TokenizerError,
    TokenizerErrorCode,
    TokenizerProfile,
    TokenizerRegistry,
    run_request_budget,
)

_NO_RESULT = object()
_MISSING = object()
_MAX_VALUE_DEPTH = 4
_MAX_COLLECTION_ITEMS = 16
_MAX_STRING_CHARACTERS = 512
_MIN_METADATA_BYTES = 128
_MAX_CONTEXT_TRACE_SESSIONS = 256


class AdapterStage(str, Enum):
    """Stable, content-free AstrBot adapter stages."""

    IDENTITY = "IDENTITY"
    EVENT_IDENTITY = "EVENT_IDENTITY"
    TOOL_METADATA = "TOOL_METADATA"
    PROJECTION = "PROJECTION"
    OPAQUE_COST = "OPAQUE_COST"


class AdapterErrorCode(str, Enum):
    """Stable AstrBot compatibility and identity failures."""

    HOST_IDENTITY_MISSING = "HOST_IDENTITY_MISSING"
    HOST_TIMESTAMP_INVALID = "HOST_TIMESTAMP_INVALID"
    EVENT_IDENTITY_INVALID = "EVENT_IDENTITY_INVALID"
    TOOL_METADATA_INVALID = "TOOL_METADATA_INVALID"
    PROJECTION_IMPORT_UNAVAILABLE = "PROJECTION_IMPORT_UNAVAILABLE"
    PROJECTION_API_UNAVAILABLE = "PROJECTION_API_UNAVAILABLE"
    PROJECTION_BUILD_FAILED = "PROJECTION_BUILD_FAILED"
    OPAQUE_TOKEN_COUNT_INVALID = "OPAQUE_TOKEN_COUNT_INVALID"
    JOURNAL_CONTENT_INVALID = "JOURNAL_CONTENT_INVALID"
    COMPACTION_TARGET_INVALID = "COMPACTION_TARGET_INVALID"
    MESSAGE_BOUNDARY_INVALID = "MESSAGE_BOUNDARY_INVALID"


@dataclass(frozen=True, slots=True)
class AdapterFault:
    """One redacted fail-open observation."""

    code: str
    stage: str
    missing_field_count: int
    capability_available: bool


class AstrBotAdapterError(ValueError):
    """Raise one adapter failure without host or message content."""

    def __init__(
        self,
        code: AdapterErrorCode,
        stage: AdapterStage,
        *,
        missing_field_count: int = 0,
        capability_available: bool = False,
    ) -> None:
        self.code = code.value
        self.stage = stage.value
        self.missing_field_count = missing_field_count
        self.capability_available = capability_available
        super().__init__(self.code)

    @property
    def fault(self) -> AdapterFault:
        return AdapterFault(
            code=self.code,
            stage=self.stage,
            missing_field_count=self.missing_field_count,
            capability_available=self.capability_available,
        )


@dataclass(frozen=True, slots=True)
class HostTurnIdentity:
    """Stable host identity shared by every authoritative turn Hook."""

    session_key: SessionKey
    host_message_id: str
    created_at: datetime
    request_identity: int


@dataclass(frozen=True, slots=True)
class DeterministicEventIdentity:
    """Deterministic Journal identifiers for one authoritative callback."""

    event_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True, repr=False)
class ProjectionFactory:
    """Request-local constructors derived from the current Hook user message."""

    message_type: Callable[..., object] = field(repr=False)
    text_part_type: Callable[..., object] = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProjectionBuild:
    """Fail-open temporary projection construction result."""

    objects: tuple[object, ...] = field(repr=False)
    fault: AdapterFault | None


@dataclass(frozen=True, slots=True, repr=False)
class PreparedRequest:
    """Request-local durable view and AC-owned fallback candidates."""

    turn: HostTurnIdentity = field(repr=False)
    current_input: str = field(repr=False)
    user_event: EventEnvelope = field(repr=False)
    view: RequestView = field(repr=False)
    candidates: tuple[CandidateBlock, ...] = field(repr=False)
    trusted_token_usage: int | None
    budget_profile: RequestBudgetProfile


class _LazyTokenCounter:
    """Construct one request counter lazily inside the coarse worker call."""

    __slots__ = ("_counter", "_provider", "profile")

    def __init__(
        self,
        provider: Callable[[TokenizerProfile], TokenCounter],
        profile: TokenizerProfile,
    ) -> None:
        self._provider = provider
        self.profile = profile
        self._counter: TokenCounter | None = None

    def count_text(self, text: str) -> int:
        counter = self._counter
        if counter is None:
            try:
                candidate = self._provider(self.profile)
            except TokenizerError:
                raise
            except Exception:  # noqa: BLE001 - construction details stay private
                raise TokenizerError(TokenizerErrorCode.TOKENIZER_CONSTRUCTION_FAILED) from None
            try:
                candidate_profile = getattr(candidate, "profile", _MISSING)
            except Exception:  # noqa: BLE001 - provider counter details stay private
                raise TokenizerError(TokenizerErrorCode.TOKENIZER_CONSTRUCTION_FAILED) from None
            if (
                not isinstance(candidate_profile, TokenizerProfile)
                or candidate_profile != self.profile
            ):
                raise TokenizerError(TokenizerErrorCode.TOKENIZER_CONSTRUCTION_FAILED)
            if not callable(getattr(candidate, "count_text", None)):
                raise TokenizerError(TokenizerErrorCode.TOKENIZER_CONSTRUCTION_FAILED)
            counter = candidate
            self._counter = counter
        return counter.count_text(text)


class _ProfiledByteFallbackCounter:
    """Bind the compatibility counter to the explicit fallback profile."""

    __slots__ = ("_counter",)

    profile = BYTE_FALLBACK

    def __init__(self) -> None:
        self._counter = Utf8ByteTokenCounter()

    def count_text(self, text: str) -> int:
        return self._counter.count_text(text)


def _raise(
    code: AdapterErrorCode,
    stage: AdapterStage,
    *,
    missing_field_count: int = 0,
    capability_available: bool = False,
) -> NoReturn:
    raise AstrBotAdapterError(
        code,
        stage,
        missing_field_count=missing_field_count,
        capability_available=capability_available,
    )


def _fault(
    code: AdapterErrorCode,
    stage: AdapterStage,
    *,
    missing_field_count: int = 0,
    capability_available: bool = False,
) -> AdapterFault:
    return AdapterFault(
        code=code.value,
        stage=stage.value,
        missing_field_count=missing_field_count,
        capability_available=capability_available,
    )


def _safe_call(target: object, method_name: str) -> object:
    method = _safe_attribute(target, method_name)
    if not callable(method):
        return _MISSING
    try:
        return method()
    except Exception:  # noqa: BLE001 - host failures cross a redacted boundary
        return _MISSING


def _safe_attribute(target: object, name: str) -> object:
    try:
        return getattr(target, name)
    except Exception:  # noqa: BLE001 - host properties cross a redacted boundary
        return _MISSING


def _required_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _optional_text(value: object) -> str | None | object:
    if value is None or value == "":
        return None
    text = _required_text(value)
    return text if text is not None else _MISSING


def _message_type_text(value: object) -> str | None:
    candidate = getattr(value, "value", value)
    return _required_text(candidate)


def _host_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def extract_host_session_key(event: object, conversation: object) -> SessionKey:
    """Extract one exact seven-field SessionKey from documented AstrBot objects."""

    message_obj = getattr(event, "message_obj", _MISSING)
    raw_group_id = _safe_call(event, "get_group_id")
    values = {
        "platform_instance_id": _required_text(_safe_call(event, "get_platform_id")),
        "message_type": _message_type_text(getattr(message_obj, "type", _MISSING)),
        "session_id": _required_text(getattr(message_obj, "session_id", _MISSING)),
        "user_id": _required_text(_safe_call(event, "get_sender_id")),
        "conversation_id": _required_text(getattr(conversation, "cid", _MISSING)),
    }
    group_id = _optional_text(raw_group_id)
    if group_id is _MISSING:
        values["group_id"] = None
    persona_id = _optional_text(getattr(conversation, "persona_id", None))
    if persona_id is _MISSING:
        values["persona_id"] = None

    missing_count = sum(value is None for value in values.values())
    missing_count += int(raw_group_id is _MISSING or group_id is _MISSING)
    missing_count += int(persona_id is _MISSING)
    if missing_count:
        _raise(
            AdapterErrorCode.HOST_IDENTITY_MISSING,
            AdapterStage.IDENTITY,
            missing_field_count=missing_count,
        )

    platform_instance_id = values["platform_instance_id"]
    message_type = values["message_type"]
    session_id = values["session_id"]
    user_id = values["user_id"]
    conversation_id = values["conversation_id"]
    if (
        platform_instance_id is None
        or message_type is None
        or session_id is None
        or user_id is None
        or conversation_id is None
    ):
        _raise(
            AdapterErrorCode.HOST_IDENTITY_MISSING,
            AdapterStage.IDENTITY,
            missing_field_count=1,
        )
    return SessionKey(
        platform_instance_id=platform_instance_id,
        message_type=message_type,
        session_id=session_id,
        group_id=group_id if isinstance(group_id, str) else None,
        user_id=user_id,
        conversation_id=conversation_id,
        persona_id=persona_id if isinstance(persona_id, str) else None,
    )


def extract_host_turn_identity(event: object, request: object) -> HostTurnIdentity:
    """Extract the exact seven-field SessionKey and stable host delivery identity."""

    message_obj = getattr(event, "message_obj", _MISSING)
    conversation = getattr(request, "conversation", _MISSING)
    session_key = extract_host_session_key(event, conversation)
    host_message_id = _required_text(getattr(message_obj, "message_id", _MISSING))
    if host_message_id is None:
        _raise(
            AdapterErrorCode.HOST_IDENTITY_MISSING,
            AdapterStage.IDENTITY,
            missing_field_count=1,
        )
    created_at = _host_datetime(getattr(message_obj, "timestamp", _MISSING))
    if created_at is None:
        _raise(
            AdapterErrorCode.HOST_TIMESTAMP_INVALID,
            AdapterStage.IDENTITY,
            missing_field_count=1,
        )
    return HostTurnIdentity(
        session_key=session_key,
        host_message_id=host_message_id,
        created_at=created_at,
        request_identity=id(request),
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def deterministic_event_identity(
    turn: HostTurnIdentity,
    source_hook: SourceHook,
    *,
    ordinal: int = 0,
    canonical_metadata: str = "",
) -> DeterministicEventIdentity:
    """Derive stable callback identity without time, UUIDs, or process-local hash."""

    if (
        not isinstance(turn, HostTurnIdentity)
        or not isinstance(source_hook, SourceHook)
        or isinstance(ordinal, bool)
        or not isinstance(ordinal, int)
        or ordinal < 0
        or not isinstance(canonical_metadata, str)
    ):
        _raise(AdapterErrorCode.EVENT_IDENTITY_INVALID, AdapterStage.EVENT_IDENTITY)
    idempotency_payload = _canonical_json(
        {
            "canonical_metadata": canonical_metadata,
            "host_message_id": turn.host_message_id,
            "ordinal": ordinal,
            "source_hook": source_hook.value,
        }
    )
    idempotency_key = "ac1:" + hashlib.sha256(idempotency_payload.encode("utf-8")).hexdigest()
    event_payload = _canonical_json(
        {
            "idempotency_key": idempotency_key,
            "session_key_hash": turn.session_key.session_key_hash,
            "source_hook": source_hook.value,
        }
    )
    event_id = "evt:" + hashlib.sha256(event_payload.encode("utf-8")).hexdigest()
    return DeterministicEventIdentity(
        event_id=event_id,
        idempotency_key=idempotency_key,
    )


def _type_name(value: object) -> str:
    value_type = type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}"


def _bounded_text(value: str) -> str | dict[str, object]:
    if len(value) <= _MAX_STRING_CHARACTERS:
        return value
    return {
        "text": value[:_MAX_STRING_CHARACTERS],
        "truncated": True,
        "characters": len(value),
    }


def _mapping_key(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (bool, int)):
        return _canonical_json(value)
    if isinstance(value, float) and math.isfinite(value):
        return _canonical_json(value)
    return f"<{_type_name(value)}>"


def _canonical_value(value: object, *, depth: int = 0) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"type": "non_finite_float"}
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value)}
    if depth >= _MAX_VALUE_DEPTH:
        return {"type": _type_name(value), "truncated": True}
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda item: _mapping_key(item[0]))
        bounded = items[:_MAX_COLLECTION_ITEMS]
        result = {
            _mapping_key(key): _canonical_value(item, depth=depth + 1) for key, item in bounded
        }
        if len(items) > len(bounded):
            result["$truncated_items"] = len(items) - len(bounded)
        return result
    if isinstance(value, (list, tuple)):
        bounded_items = value[:_MAX_COLLECTION_ITEMS]
        result_items = [_canonical_value(item, depth=depth + 1) for item in bounded_items]
        if len(value) > len(bounded_items):
            result_items.append({"truncated_items": len(value) - len(bounded_items)})
        return result_items
    return {"type": _type_name(value)}


def _tool_name(tool: object) -> str:
    if isinstance(tool, str) and tool.strip():
        return tool
    for attribute in ("name", "tool_name", "__name__"):
        candidate = _required_text(getattr(tool, attribute, None))
        if candidate is not None:
            return candidate
    return _type_name(tool)


def canonical_tool_metadata(
    tool: object,
    tool_args: object,
    *,
    tool_result: object = _NO_RESULT,
    max_utf8_bytes: int = 4_096,
) -> str:
    """Encode bounded deterministic tool metadata without arbitrary object repr."""

    if (
        isinstance(max_utf8_bytes, bool)
        or not isinstance(max_utf8_bytes, int)
        or max_utf8_bytes < _MIN_METADATA_BYTES
    ):
        _raise(AdapterErrorCode.TOOL_METADATA_INVALID, AdapterStage.TOOL_METADATA)
    kind = "call" if tool_result is _NO_RESULT else "result"
    payload: dict[str, object] = {
        "arguments": _canonical_value(tool_args),
        "kind": kind,
        "tool": _bounded_text(_tool_name(tool)),
    }
    if tool_result is not _NO_RESULT:
        payload["result"] = _canonical_value(tool_result)
    encoded = _canonical_json(payload)
    encoded_size = len(encoded.encode("utf-8"))
    if encoded_size <= max_utf8_bytes:
        return encoded
    fallback = _canonical_json(
        {
            "kind": kind,
            "truncated": True,
            "utf8_bytes": encoded_size,
        }
    )
    if len(fallback.encode("utf-8")) > max_utf8_bytes:
        _raise(AdapterErrorCode.TOOL_METADATA_INVALID, AdapterStage.TOOL_METADATA)
    return fallback


def projection_factory_from_user_message(
    user_message: object,
) -> ProjectionFactory:
    """Derive fresh projection constructors from one current Hook object."""

    content = _safe_attribute(user_message, "content")
    if not isinstance(content, (list, tuple)):
        _raise(
            AdapterErrorCode.PROJECTION_API_UNAVAILABLE,
            AdapterStage.PROJECTION,
        )
    text_part = None
    for part in content:
        discriminator = _safe_attribute(part, "type")
        if type(discriminator) is not str or discriminator != "text":
            continue
        if isinstance(_safe_attribute(part, "text"), str):
            text_part = part
            break
    if text_part is None:
        _raise(
            AdapterErrorCode.PROJECTION_API_UNAVAILABLE,
            AdapterStage.PROJECTION,
        )
    return ProjectionFactory(
        message_type=type(user_message),
        text_part_type=type(text_part),
    )


def build_projection_objects(
    text: str,
    factory: ProjectionFactory,
) -> ProjectionBuild:
    """Build one provider-only user Message or return a redacted fail-open fault."""

    if not isinstance(factory, ProjectionFactory):
        return ProjectionBuild(
            objects=(),
            fault=_fault(
                AdapterErrorCode.PROJECTION_API_UNAVAILABLE,
                AdapterStage.PROJECTION,
            ),
        )
    if not isinstance(text, str):
        return ProjectionBuild(
            objects=(),
            fault=_fault(
                AdapterErrorCode.PROJECTION_BUILD_FAILED,
                AdapterStage.PROJECTION,
                capability_available=True,
            ),
        )
    if not text:
        return ProjectionBuild(objects=(), fault=None)
    try:
        part = factory.text_part_type(text=text)
        marker = getattr(part, "mark_as_temp", None)
        if not callable(marker):
            raise TypeError
        marked = marker()
        if marked is not None:
            part = marked
        part_type = _safe_attribute(part, "type")
        part_text = _safe_attribute(part, "text")
        if (
            type(part_type) is not str
            or part_type != "text"
            or type(part_text) is not str
            or part_text != text
            or _safe_attribute(part, "_no_save") is not True
        ):
            raise TypeError
        message = factory.message_type(role="user", content=[part])
        message.__setattr__("_no_save", True)
        message_role = _safe_attribute(message, "role")
        message_content = _safe_attribute(message, "content")
        final_part_type = _safe_attribute(part, "type")
        final_part_text = _safe_attribute(part, "text")
        if (
            type(message_role) is not str
            or message_role != "user"
            or not isinstance(message_content, (list, tuple))
            or len(message_content) != 1
            or message_content[0] is not part
            or _safe_attribute(message, "_no_save") is not True
            or type(final_part_type) is not str
            or final_part_type != "text"
            or type(final_part_text) is not str
            or final_part_text != text
            or _safe_attribute(part, "_no_save") is not True
        ):
            raise TypeError
    except Exception:  # noqa: BLE001 - construction details and content stay private
        return ProjectionBuild(
            objects=(),
            fault=_fault(
                AdapterErrorCode.PROJECTION_BUILD_FAILED,
                AdapterStage.PROJECTION,
                capability_available=True,
            ),
        )
    return ProjectionBuild(objects=(message,), fault=None)


def _content_texts(message: object) -> tuple[str, ...]:
    try:
        content = (
            message.get("content", None)
            if isinstance(message, Mapping)
            else getattr(message, "content", None)
        )
    except Exception:  # noqa: BLE001 - host property details stay private
        _raise(
            AdapterErrorCode.OPAQUE_TOKEN_COUNT_INVALID,
            AdapterStage.OPAQUE_COST,
        )
    if isinstance(content, str):
        return (content,)
    if not isinstance(content, (list, tuple)):
        return ()
    texts: list[str] = []
    for part in content:
        if isinstance(part, str):
            texts.append(part)
            continue
        for attribute in ("text", "image_url", "audio_url"):
            try:
                value = (
                    part.get(attribute, None)
                    if isinstance(part, Mapping)
                    else getattr(part, attribute, None)
                )
            except Exception:  # noqa: BLE001 - host property details stay private
                _raise(
                    AdapterErrorCode.OPAQUE_TOKEN_COUNT_INVALID,
                    AdapterStage.OPAQUE_COST,
                )
            if isinstance(value, str):
                texts.append(value)
    return tuple(texts)


def estimate_opaque_token_cost(
    objects: tuple[object, ...],
    counter: TokenCounter,
) -> int:
    """Count opaque host content ephemerally without retaining text or hashes."""

    total = 0
    for item in objects:
        for text in _content_texts(item):
            try:
                value = counter.count_text(text)
            except Exception:  # noqa: BLE001 - counter details stay private
                _raise(
                    AdapterErrorCode.OPAQUE_TOKEN_COUNT_INVALID,
                    AdapterStage.OPAQUE_COST,
                )
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                _raise(
                    AdapterErrorCode.OPAQUE_TOKEN_COUNT_INVALID,
                    AdapterStage.OPAQUE_COST,
                )
            total += value
    return total


def _message_role(message: object) -> str | None:
    value = (
        message.get("role", None)
        if isinstance(message, Mapping)
        else getattr(message, "role", None)
    )
    return value if isinstance(value, str) else None


def select_projection_boundaries(
    messages: list[object],
) -> tuple[tuple[object, ...], tuple[object, ...]]:
    """Select the leading system objects and exact current user object by identity."""

    if not isinstance(messages, list) or not messages or _message_role(messages[-1]) != "user":
        _raise(
            AdapterErrorCode.MESSAGE_BOUNDARY_INVALID,
            AdapterStage.PROJECTION,
        )
    system_end = 0
    while system_end < len(messages) and _message_role(messages[system_end]) == "system":
        system_end += 1
    return tuple(messages[:system_end]), (messages[-1],)


class AstrBotHookBridge:
    """Async Hook bridge over the synchronous canonical SQLite repository."""

    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        retrieval_config: RetrievalConfig | None = None,
        counter_provider: Callable[[TokenizerProfile], TokenCounter] | None = None,
        request_runner: Callable[..., RequestBudgetOutcome] | None = None,
        context_engine_mode: ContextEngineMode = ContextEngineMode.OFF,
    ) -> None:
        if not isinstance(context_engine_mode, ContextEngineMode):
            raise TypeError("context_engine_mode must be a ContextEngineMode")
        self._repository = repository
        self._retrieval_config = retrieval_config or RetrievalConfig()
        registry = TokenizerRegistry()
        self._counter_provider = counter_provider or registry.counter_for
        self._request_runner = request_runner or run_request_budget
        self._compatibility_counter = Utf8ByteTokenCounter()
        self._context_engine_mode = context_engine_mode
        self._last_context_trace: LiveContextTrace | None = None
        self._context_traces: OrderedDict[str, LiveContextTrace] = OrderedDict()

    @property
    def repository(self) -> SQLiteRepository:
        return self._repository

    @property
    def context_engine_mode(self) -> ContextEngineMode:
        return self._context_engine_mode

    @property
    def last_context_trace(self) -> LiveContextTrace | None:
        return self._last_context_trace

    def inspect_context_trace(self, session_key: SessionKey) -> LiveContextTrace | None:
        """Return bounded, content-free diagnostics without reading the Journal."""

        if not isinstance(session_key, SessionKey):
            raise TypeError("session_key must be a SessionKey")
        return self._context_traces.get(session_key.session_key_hash)

    def _remember_context_trace(
        self,
        session_key: SessionKey,
        trace: LiveContextTrace,
    ) -> None:
        session_hash = session_key.session_key_hash
        self._context_traces[session_hash] = trace
        self._context_traces.move_to_end(session_hash)
        while len(self._context_traces) > _MAX_CONTEXT_TRACE_SESSIONS:
            self._context_traces.popitem(last=False)
        self._last_context_trace = trace

    def _compatibility_cost(self, content: str) -> int:
        if not isinstance(content, str):
            _raise(
                AdapterErrorCode.JOURNAL_CONTENT_INVALID,
                AdapterStage.IDENTITY,
            )
        try:
            value = self._compatibility_counter.count_text(content)
        except Exception:  # noqa: BLE001 - counter details stay private
            _raise(
                AdapterErrorCode.OPAQUE_TOKEN_COUNT_INVALID,
                AdapterStage.OPAQUE_COST,
            )
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _raise(
                AdapterErrorCode.OPAQUE_TOKEN_COUNT_INVALID,
                AdapterStage.OPAQUE_COST,
            )
        return value

    async def read_tool_loop_state(self, session_key: SessionKey) -> ToolLoopAssessment:
        """Read the durable Journal-only tool protocol without host sidecars."""

        view = await asyncio.to_thread(read_request_view, self._repository, session_key)
        return evaluate_tool_loop(view.delta)

    def _canonical_observation(self, content: str) -> CanonicalMetricObservation:
        count: int | None = None
        try:
            counter = self._counter_provider(CANONICAL_O200K)
            counter_profile = getattr(counter, "profile", _MISSING)
            if (
                not isinstance(counter_profile, TokenizerProfile)
                or counter_profile != CANONICAL_O200K
            ):
                raise TokenizerError(TokenizerErrorCode.TOKENIZER_CONSTRUCTION_FAILED)
            value = counter.count_text(content)
            if not isinstance(value, bool) and isinstance(value, int) and value >= 0:
                count = value
        except Exception:  # noqa: BLE001 - canonical failures become one backfill intent
            count = None
        return CanonicalMetricObservation(CANONICAL_O200K.profile_id, count)

    def _capture_with_metrics(
        self,
        capture: Callable[..., EventEnvelope],
        *,
        content: str,
        **kwargs: object,
    ) -> EventEnvelope:
        return capture(
            content=content,
            token_count=self._compatibility_cost(content),
            canonical=self._canonical_observation(content),
            **kwargs,
        )

    async def prepare_request(
        self,
        event: object,
        request: object,
        *,
        budget_profile: RequestBudgetProfile,
    ) -> PreparedRequest | None:
        """Capture the sole user event, read one view, and prepare fallback candidates."""

        turn = extract_host_turn_identity(event, request)
        current_input = getattr(request, "prompt", None)
        if not isinstance(current_input, str):
            _raise(
                AdapterErrorCode.JOURNAL_CONTENT_INVALID,
                AdapterStage.IDENTITY,
            )
        # Authenticate the existing current-session view before the first live write.
        # This prevents already-corrupted durable content from being followed by a new
        # Journal event before the composition root can lock the storage boundary.
        initial_view = await asyncio.to_thread(
            read_request_view,
            self._repository,
            turn.session_key,
        )
        if evaluate_tool_loop(initial_view.delta).state is not ToolLoopState.STABLE:
            return None
        identity = deterministic_event_identity(turn, SourceHook.ON_LLM_REQUEST)
        user_event = await asyncio.to_thread(
            self._capture_with_metrics,
            self._repository.capture_user_event,
            event_id=identity.event_id,
            session_key=turn.session_key,
            content=current_input,
            idempotency_key=identity.idempotency_key,
            created_at=turn.created_at,
        )
        view = await asyncio.to_thread(
            read_request_view,
            self._repository,
            turn.session_key,
        )
        candidates = tuple(
            candidate
            for candidate in select_candidates(
                view,
                current_input,
                self._retrieval_config,
            )
            if user_event.event_id not in candidate.source_event_ids
        )
        conversation = getattr(request, "conversation", None)
        raw_usage = getattr(conversation, "token_usage", None)
        trusted_token_usage = (
            raw_usage
            if isinstance(raw_usage, int) and not isinstance(raw_usage, bool) and raw_usage > 0
            else None
        )
        return PreparedRequest(
            turn=turn,
            current_input=current_input,
            user_event=user_event,
            view=view,
            candidates=candidates,
            trusted_token_usage=trusted_token_usage,
            budget_profile=budget_profile,
        )

    async def evaluate_prepared(
        self,
        prepared: PreparedRequest,
        *,
        host_budget_view: HostBudgetView,
    ) -> RequestBudgetOutcome:
        """Run pressure, counting, graph selection, and verification in one thread."""

        workload = RequestBudgetWorkload(
            prepared=PreparedBudgetInput(
                current_input=prepared.current_input,
                view=prepared.view,
                candidates=prepared.candidates,
                trusted_token_usage=prepared.trusted_token_usage,
            ),
            host=host_budget_view,
            profile=prepared.budget_profile,
        )
        trace_slot: list[LiveContextTrace] = []

        def assembly_runner(
            view: RequestView,
            candidates: tuple[CandidateBlock, ...],
            *,
            current_input: str,
            opaque_token_cost: int,
            fixed_required_cost: int,
            counter: TokenCounter,
            config: BudgetConfig,
            block_token_counts: Mapping[str, int],
        ) -> AssemblyResult:
            assembly, trace = assemble_live_context(
                view,
                candidates,
                current_input=current_input,
                opaque_token_cost=opaque_token_cost,
                fixed_required_cost=fixed_required_cost,
                counter=counter,
                budget_config=config,
                block_token_counts=block_token_counts,
                mode=self._context_engine_mode,
            )
            trace_slot[:] = [trace]
            return assembly

        outcome = await asyncio.to_thread(
            self._request_runner,
            workload,
            primary=_LazyTokenCounter(
                self._counter_provider,
                prepared.budget_profile.tokenizer_profile,
            ),
            fallback=_LazyTokenCounter(
                lambda _profile: _ProfiledByteFallbackCounter(),
                BYTE_FALLBACK,
            ),
            assembly_runner=assembly_runner,
        )
        if outcome.mutation_allowed and trace_slot:
            self._remember_context_trace(
                prepared.turn.session_key,
                trace_slot[-1],
            )
        return cast(RequestBudgetOutcome, outcome)

    async def capture_assistant(
        self,
        prepared: PreparedRequest,
        content: str,
    ) -> EventEnvelope:
        """Capture the sole authoritative assistant completion."""

        identity = deterministic_event_identity(
            prepared.turn,
            SourceHook.ON_AGENT_DONE,
        )
        return await asyncio.to_thread(
            self._capture_with_metrics,
            self._repository.capture_assistant_event,
            event_id=identity.event_id,
            session_key=prepared.turn.session_key,
            content=content,
            idempotency_key=identity.idempotency_key,
            created_at=prepared.turn.created_at,
        )

    async def capture_tool_call(
        self,
        prepared: PreparedRequest,
        tool: object,
        tool_args: object,
        *,
        ordinal: int,
    ) -> EventEnvelope:
        """Capture the sole authoritative tool-call fact."""

        metadata = canonical_tool_metadata(tool, tool_args)
        return await self._capture_tool(
            prepared,
            metadata,
            event_type=EventType.TOOL_CALL,
            source_hook=SourceHook.ON_USING_LLM_TOOL,
            ordinal=ordinal,
        )

    async def capture_tool_result(
        self,
        prepared: PreparedRequest,
        tool: object,
        tool_args: object,
        tool_result: object,
        *,
        ordinal: int,
    ) -> EventEnvelope:
        """Capture the sole authoritative tool-result fact."""

        metadata = canonical_tool_metadata(
            tool,
            tool_args,
            tool_result=tool_result,
        )
        return await self._capture_tool(
            prepared,
            metadata,
            event_type=EventType.TOOL_RESULT,
            source_hook=SourceHook.ON_LLM_TOOL_RESPOND,
            ordinal=ordinal,
        )

    async def _capture_tool(
        self,
        prepared: PreparedRequest,
        metadata: str,
        *,
        event_type: EventType,
        source_hook: SourceHook,
        ordinal: int,
    ) -> EventEnvelope:
        identity = deterministic_event_identity(
            prepared.turn,
            source_hook,
            ordinal=ordinal,
            canonical_metadata=metadata,
        )
        return await asyncio.to_thread(
            self._capture_with_metrics,
            self._repository.capture_tool_event,
            event_id=identity.event_id,
            session_key=prepared.turn.session_key,
            event_type=event_type,
            content=metadata,
            idempotency_key=identity.idempotency_key,
            created_at=prepared.turn.created_at,
        )

    async def raise_compaction_intent(
        self,
        prepared: PreparedRequest,
        *,
        target_high_water_mark: int,
    ) -> CompactionJobEnvelope | None:
        """Raise one deterministic, bounded durable intent without waiting for work."""

        if (
            isinstance(target_high_water_mark, bool)
            or not isinstance(target_high_water_mark, int)
            or target_high_water_mark < 1
        ):
            _raise(
                AdapterErrorCode.COMPACTION_TARGET_INVALID,
                AdapterStage.EVENT_IDENTITY,
            )
        payload = _canonical_json(
            {
                "host_message_id": prepared.turn.host_message_id,
                "session_key_hash": prepared.turn.session_key.session_key_hash,
                "target_high_water_mark": target_high_water_mark,
            }
        )
        job_id = "job:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return await asyncio.to_thread(
            self._repository.raise_compaction_intent,
            job_id=job_id,
            session_key=prepared.turn.session_key,
            target_high_water_mark=target_high_water_mark,
            now=prepared.turn.created_at,
        )
