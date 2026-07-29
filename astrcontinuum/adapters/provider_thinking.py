"""Reversible, version-pinned provider egress compatibility for thinking replay.

The shim deliberately talks only to public provider instances obtained from
``Context.get_all_providers()``.  It makes request-local copy-on-write context
projections for known OpenAI-compatible Claude requests and never mutates
AstrBot history, models, or provider classes.
"""

from __future__ import annotations

import inspect
import re
import threading
import weakref
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import wraps

__all__ = ["ProviderThinkingCompatibility", "ThinkingCompatibilityBlockedError"]


_MISSING = object()
_CLAUDE_MODEL_PATTERN = re.compile(
    r"(?<![a-z0-9])claude(?:[0-9]+)?(?![a-z0-9])",
    re.IGNORECASE,
)
_THINKING_PART_TYPES = frozenset({"think", "thinking", "redacted_thinking"})
_REASONING_TEXT_PREFIX = "[assistant reasoning]\n"
_REDACTED_REASONING_TEXT = "[assistant reasoning redacted]"
_OPENAI_COMPAT_PROVIDER_TYPES = frozenset(
    {
        "openai_chat_completion",
        "groq_chat_completion",
        "longcat_chat_completion",
        "aihubmix_chat_completion",
        "openrouter_chat_completion",
        "xai_chat_completion",
        "xiaomi_chat_completion",
        "zhipu_chat_completion",
    }
)
_NATIVE_THINKING_PROVIDER_TYPES = frozenset(
    {
        "anthropic_chat_completion",
        "googlegenai_chat_completion",
        "kimi_code_chat_completion",
        "minimax_token_plan",
        "xiaomi_token_plan",
    }
)
_SAFE_ASSISTANT_FIELDS = frozenset({"role", "content", "tool_calls", "tool_call_id"})
_NATIVE_ASSISTANT_METADATA_FIELDS = frozenset(
    {
        "reasoning_content",
        "reasoning",
        "reasoning_signature",
        "signature",
        "encrypted",
    }
)
_NATIVE_PART_METADATA_FIELDS = frozenset({"signature", "encrypted"})


class ThinkingCompatibilityBlockedError(RuntimeError):
    """Block an unsafe target replay without exposing message content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"AstrContinuum blocked unsafe thinking replay: {code}")


@dataclass(slots=True, repr=False)
class _ProviderThinkingBinding:
    provider: object
    original_text_chat: Callable[..., Awaitable[object]]
    original_text_chat_stream: Callable[..., object]
    text_chat_watcher: object
    text_chat_stream_watcher: object
    text_chat_was_instance_attribute: bool
    text_chat_stream_was_instance_attribute: bool
    active: bool = True


def _instance_has_attribute(target: object, name: str) -> bool:
    try:
        values = vars(target)
    except TypeError:
        return False
    return name in values


def _provider_descriptor(provider: object) -> tuple[str, str] | None:
    meta = getattr(provider, "meta", None)
    if not callable(meta):
        return None
    try:
        value = meta()
    except Exception:  # noqa: BLE001 - host provider metadata is optional
        return None
    provider_type = getattr(value, "type", None)
    provider_id = getattr(value, "id", None)
    if not isinstance(provider_type, str) or not isinstance(provider_id, str):
        return None
    return provider_type, provider_id


def _effective_model_identity(provider: object, explicit_model: object) -> str | None:
    if isinstance(explicit_model, str) and explicit_model.strip():
        return explicit_model
    get_model = getattr(provider, "get_model", None)
    if not callable(get_model):
        return None
    try:
        model = get_model()
    except Exception:  # noqa: BLE001 - host provider metadata is optional
        return None
    return model if isinstance(model, str) and model.strip() else None


def _plain_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _part_value(part: object, key: str) -> object:
    if isinstance(part, Mapping):
        return part.get(key)
    try:
        return getattr(part, key)
    except Exception:  # noqa: BLE001 - opaque host object is intentionally ignored
        return _MISSING


def _safe_media_value(value: object) -> object:
    if isinstance(value, str):
        return value
    if not isinstance(value, Mapping):
        raise ThinkingCompatibilityBlockedError("MEDIA_PART_INVALID")
    if any(key not in {"url", "id", *_NATIVE_PART_METADATA_FIELDS} for key in value):
        raise ThinkingCompatibilityBlockedError("MEDIA_PART_UNSAFE")
    url = value.get("url")
    if not isinstance(url, str):
        raise ThinkingCompatibilityBlockedError("MEDIA_PART_INVALID")
    safe: dict[str, object] = {"url": url}
    media_id = value.get("id")
    if isinstance(media_id, str):
        safe["id"] = media_id
    return safe


def _reasoning_text(value: str) -> str:
    return f"{_REASONING_TEXT_PREFIX}{value}"


def _safe_content_part(part: object) -> tuple[dict[str, object] | None, str | None]:
    """Return one protocol-neutral part or a redacted-thinking marker.

    No opaque field is coerced to text. The only static marker represents an
    explicit ``redacted_thinking`` part; all other unknown data blocks replay.
    """

    part_type = _part_value(part, "type")
    if not isinstance(part_type, str):
        raise ThinkingCompatibilityBlockedError("CONTENT_PART_TYPE_INVALID")

    raw_part = part if isinstance(part, Mapping) else None

    def require_allowed(*allowed: str) -> None:
        if raw_part is None:
            return
        permitted = {*allowed, *_NATIVE_PART_METADATA_FIELDS}
        if any(key not in permitted for key in raw_part):
            raise ThinkingCompatibilityBlockedError("CONTENT_PART_UNSAFE")

    if part_type == "text":
        require_allowed("type", "text")
        text = _plain_text(_part_value(part, "text"))
        if text is None:
            raise ThinkingCompatibilityBlockedError("TEXT_PART_INVALID")
        return {"type": "text", "text": text}, None

    if part_type == "think":
        require_allowed("type", "think")
        think = _plain_text(_part_value(part, "think"))
        if think is None:
            raise ThinkingCompatibilityBlockedError("THINKING_TEXT_UNAVAILABLE")
        return None, _reasoning_text(think)

    if part_type == "thinking":
        require_allowed("type", "thinking", "text")
        thinking = _plain_text(_part_value(part, "thinking"))
        if thinking is None:
            thinking = _plain_text(_part_value(part, "text"))
        if thinking is None:
            raise ThinkingCompatibilityBlockedError("THINKING_TEXT_UNAVAILABLE")
        return None, _reasoning_text(thinking)

    if part_type == "redacted_thinking":
        require_allowed("type", "data")
        return None, _REDACTED_REASONING_TEXT

    if part_type in {"image_url", "audio_url"}:
        field = "image_url" if part_type == "image_url" else "audio_url"
        require_allowed("type", field)
        value = _safe_media_value(_part_value(part, field))
        return {"type": part_type, field: value}, None

    raise ThinkingCompatibilityBlockedError("CONTENT_PART_UNSUPPORTED")


def _assistant_message_mapping(message: object) -> dict[str, object]:
    if isinstance(message, Mapping):
        payload = dict(message)
    else:
        dumper = getattr(message, "model_dump", None)
        if not callable(dumper):
            raise ThinkingCompatibilityBlockedError("ASSISTANT_SERIALIZATION_UNAVAILABLE")
        try:
            dumped = dumper()
        except Exception:  # noqa: BLE001 - details may contain message content
            raise ThinkingCompatibilityBlockedError("ASSISTANT_SERIALIZATION_FAILED") from None
        if not isinstance(dumped, Mapping):
            raise ThinkingCompatibilityBlockedError("ASSISTANT_SERIALIZATION_INVALID")
        payload = dict(dumped)
    if payload.get("role") != "assistant":
        raise ThinkingCompatibilityBlockedError("ASSISTANT_ROLE_INVALID")
    return payload


def _assistant_needs_normalization(payload: Mapping[str, object]) -> bool:
    permitted = _SAFE_ASSISTANT_FIELDS | _NATIVE_ASSISTANT_METADATA_FIELDS
    if any(key not in permitted for key in payload):
        raise ThinkingCompatibilityBlockedError("ASSISTANT_FIELDS_UNSAFE")
    if any(key in payload for key in _NATIVE_ASSISTANT_METADATA_FIELDS):
        return True
    if "tool_calls" in payload and _tool_calls_need_projection(payload["tool_calls"]):
        return True
    content = payload.get("content")
    if content is None or isinstance(content, str):
        return False
    if not isinstance(content, list):
        raise ThinkingCompatibilityBlockedError("ASSISTANT_CONTENT_INVALID")
    for part in content:
        part_type = _part_value(part, "type")
        if part_type in _THINKING_PART_TYPES:
            return True
        if part_type not in {"text", "image_url", "audio_url"}:
            raise ThinkingCompatibilityBlockedError("CONTENT_PART_UNSUPPORTED")
        if isinstance(part, Mapping):
            allowed = {
                "text": {"type", "text", *_NATIVE_PART_METADATA_FIELDS},
                "image_url": {"type", "image_url", *_NATIVE_PART_METADATA_FIELDS},
                "audio_url": {"type", "audio_url", *_NATIVE_PART_METADATA_FIELDS},
            }[part_type]
            if any(key not in allowed for key in part):
                raise ThinkingCompatibilityBlockedError("CONTENT_PART_UNSAFE")
            if any(key in part for key in _NATIVE_PART_METADATA_FIELDS):
                return True
        if part_type in {"image_url", "audio_url"}:
            field = "image_url" if part_type == "image_url" else "audio_url"
            media_value = _part_value(part, field)
            _safe_media_value(media_value)
            if isinstance(media_value, Mapping) and any(
                key in media_value for key in _NATIVE_PART_METADATA_FIELDS
            ):
                return True
    return False


def _projected_content_and_reasoning_notes(
    payload: Mapping[str, object],
    content: object,
) -> tuple[list[dict[str, object]], list[str]]:
    projected_parts: list[dict[str, object]] = []
    content_note_texts: set[str] = set()
    last_content_note_boundary: int | None = None

    if isinstance(content, list):
        for part in content:
            visible, note = _safe_content_part(part)
            if note is not None:
                projected_parts.append({"type": "text", "text": note})
                content_note_texts.add(note)
                last_content_note_boundary = len(projected_parts)
            elif visible is not None:
                projected_parts.append(visible)

    note_texts: list[str] = []
    seen_mirrors = set(content_note_texts)
    for field in ("reasoning_content", "reasoning"):
        plaintext = _plain_text(payload.get(field))
        if plaintext is not None:
            note = _reasoning_text(plaintext)
            if note not in seen_mirrors:
                seen_mirrors.add(note)
                note_texts.append(note)

    if isinstance(content, list) and note_texts:
        insertion = last_content_note_boundary or 0
        projected_parts[insertion:insertion] = [
            {"type": "text", "text": note} for note in note_texts
        ]
        note_texts = []
    return projected_parts, note_texts


def _tool_call_mapping(tool_call: object) -> Mapping[str, object]:
    if isinstance(tool_call, Mapping):
        return tool_call
    dumper = getattr(tool_call, "model_dump", None)
    if not callable(dumper):
        raise ThinkingCompatibilityBlockedError("TOOL_CALL_SERIALIZATION_UNAVAILABLE")
    try:
        dumped = dumper()
    except Exception:  # noqa: BLE001 - host serialization can fail arbitrarily
        raise ThinkingCompatibilityBlockedError("TOOL_CALL_SERIALIZATION_FAILED") from None
    if not isinstance(dumped, Mapping):
        raise ThinkingCompatibilityBlockedError("TOOL_CALL_SERIALIZATION_INVALID")
    return dumped


def _tool_calls_need_projection(value: object) -> bool:
    """Return whether non-empty assistant calls must use the safe COW shape.

    Every non-empty tool-call list is projected: that fixed shape drops opaque
    provider sidecars even when a host model object changes its dump details.
    The explicit scan makes a sidecar observable to this gate and ensures
    unserializable calls block before any request leaves the plugin boundary.
    """

    if not isinstance(value, list):
        raise ThinkingCompatibilityBlockedError("TOOL_CALLS_INVALID")
    for tool_call in value:
        raw = _tool_call_mapping(tool_call)
        if any(key not in {"id", "type", "function"} for key in raw):
            return True
        function = raw.get("function")
        if not isinstance(function, Mapping):
            return True
        if any(key not in {"name", "arguments"} for key in function):
            return True
    return bool(value)


def _safe_tool_calls(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ThinkingCompatibilityBlockedError("TOOL_CALLS_INVALID")
    safe_calls: list[dict[str, object]] = []
    for tool_call in value:
        raw = _tool_call_mapping(tool_call)
        tool_id = raw.get("id")
        tool_type = raw.get("type")
        function = raw.get("function")
        if not isinstance(tool_id, str) or tool_type != "function":
            raise ThinkingCompatibilityBlockedError("TOOL_CALL_INVALID")
        if not isinstance(function, Mapping):
            dumper = getattr(function, "model_dump", None)
            if not callable(dumper):
                raise ThinkingCompatibilityBlockedError("TOOL_FUNCTION_INVALID")
            try:
                function = dumper()
            except Exception:  # noqa: BLE001 - host serialization can fail arbitrarily
                raise ThinkingCompatibilityBlockedError("TOOL_FUNCTION_INVALID") from None
        if not isinstance(function, Mapping):
            raise ThinkingCompatibilityBlockedError("TOOL_FUNCTION_INVALID")
        name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(name, str) or not (arguments is None or isinstance(arguments, str)):
            raise ThinkingCompatibilityBlockedError("TOOL_FUNCTION_INVALID")
        safe_calls.append(
            {
                "id": tool_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
    return safe_calls


def _normalized_assistant_message(message: object) -> object:
    payload = _assistant_message_mapping(message)
    if not _assistant_needs_normalization(payload):
        return message

    content = payload.get("content")
    projected_parts, note_texts = _projected_content_and_reasoning_notes(
        payload,
        content,
    )
    result: dict[str, object] = {"role": "assistant"}
    if "tool_calls" in payload:
        result["tool_calls"] = _safe_tool_calls(payload["tool_calls"])
    if "tool_call_id" in payload:
        tool_call_id = payload["tool_call_id"]
        if not isinstance(tool_call_id, str):
            raise ThinkingCompatibilityBlockedError("TOOL_CALL_ID_INVALID")
        result["tool_call_id"] = tool_call_id

    if isinstance(content, str):
        if note_texts:
            result["content"] = "\n\n".join((*note_texts, content))
        else:
            result["content"] = content
        return result

    if isinstance(content, list):
        rebuilt_content = projected_parts
        if not rebuilt_content and "tool_calls" in result:
            result["content"] = None
        elif not rebuilt_content:
            raise ThinkingCompatibilityBlockedError("ASSISTANT_CONTENT_EMPTY")
        else:
            result["content"] = rebuilt_content
        return result

    if content is None:
        if note_texts:
            result["content"] = [{"type": "text", "text": note} for note in note_texts]
        elif "tool_calls" in result:
            result["content"] = None
        else:
            raise ThinkingCompatibilityBlockedError("ASSISTANT_CONTENT_UNAVAILABLE")
        return result

    raise ThinkingCompatibilityBlockedError("ASSISTANT_CONTENT_INVALID")


def _normalize_openai_claude_contexts(contexts: list[object]) -> list[object]:
    normalized: list[object] = []
    changed = False
    for message in contexts:
        try:
            role = (
                message.get("role") if isinstance(message, Mapping) else getattr(message, "role")  # noqa: B009 - host object is dynamically typed
            )
        except Exception:  # noqa: BLE001 - host object details are private
            raise ThinkingCompatibilityBlockedError("MESSAGE_ROLE_UNAVAILABLE") from None
        if role != "assistant":
            normalized.append(message)
            continue
        replacement = _normalized_assistant_message(message)
        changed = changed or replacement is not message
        normalized.append(replacement)
    return normalized if changed else contexts


def _tool_calls_result_messages(value: object) -> list[object]:
    results = value if isinstance(value, list) else [value]
    messages: list[object] = []
    for result in results:
        converter = getattr(result, "to_openai_messages", None)
        if not callable(converter):
            raise ThinkingCompatibilityBlockedError("TOOL_CALLS_RESULT_UNAVAILABLE")
        try:
            converted = converter()
        except Exception:  # noqa: BLE001 - host serialization may expose opaque data
            raise ThinkingCompatibilityBlockedError("TOOL_CALLS_RESULT_FAILED") from None
        if not isinstance(converted, list):
            raise ThinkingCompatibilityBlockedError("TOOL_CALLS_RESULT_NOT_A_LIST")
        messages.extend(converted)
    return messages


def _rewrite_call_contexts(
    contexts: object,
    tool_calls_result: object,
) -> tuple[object, bool]:
    has_tool_calls_result = tool_calls_result is not _MISSING and tool_calls_result is not None
    if contexts is _MISSING or contexts is None:
        context_list: list[object] | None = None
    elif isinstance(contexts, list):
        context_list = contexts
    else:
        raise ThinkingCompatibilityBlockedError("CONTEXTS_NOT_A_LIST")
    if not has_tool_calls_result:
        if context_list is None:
            return contexts, False
        return _normalize_openai_claude_contexts(context_list), False

    tool_messages = _tool_calls_result_messages(tool_calls_result)
    if not tool_messages:
        if context_list is None:
            return contexts, True
        return _normalize_openai_claude_contexts(context_list), True

    combined = [] if context_list is None else list(context_list)
    combined.extend(tool_messages)
    return _normalize_openai_claude_contexts(combined), True


def _bound_call_value(
    signature: inspect.Signature,
    bound: inspect.BoundArguments,
    name: str,
) -> object:
    if name in signature.parameters and name in bound.arguments:
        return bound.arguments[name]
    for parameter in signature.parameters.values():
        if parameter.kind is not inspect.Parameter.VAR_KEYWORD:
            continue
        values = bound.arguments.get(parameter.name)
        if isinstance(values, Mapping) and name in values:
            return values[name]
    return _MISSING


class ProviderThinkingCompatibility:
    """Version-pinned, reversible egress COW for OpenAI-compatible Claude calls.

    This uses only live provider instances obtained from the public Star
    ``Context`` API. It deliberately never changes request history or imports
    host-private classes.
    """

    def __init__(
        self,
        *,
        openai_compatible_provider_ids: tuple[str, ...] | list[str] = (),
    ) -> None:
        self._configured_provider_ids = frozenset(
            provider_id.strip()
            for provider_id in openai_compatible_provider_ids
            if isinstance(provider_id, str) and provider_id.strip()
        )
        self._bindings: dict[int, _ProviderThinkingBinding] = {}
        self._lock = threading.RLock()

    def ensure(self, context: object) -> int:
        """Attach wrappers to supported currently-live provider instances."""

        getter = getattr(context, "get_all_providers", None)
        if not callable(getter):
            return 0
        try:
            providers = getter()
        except Exception:  # noqa: BLE001 - plugin must not break provider discovery
            return 0
        if not isinstance(providers, list):
            return 0

        wrapped = 0
        with self._lock:
            live_by_id = {id(provider): provider for provider in providers}
            for binding in tuple(self._bindings.values()):
                if live_by_id.get(id(binding.provider)) is not binding.provider:
                    self._disable_existing_binding(binding.provider)
            for provider in providers:
                descriptor = _provider_descriptor(provider)
                if descriptor is None:
                    self._disable_existing_binding(provider)
                    continue
                if not self._is_openai_compatible_descriptor(descriptor):
                    self._disable_existing_binding(provider)
                    continue
                if self._has_active_wrapper(provider):
                    continue
                if self._install(provider):
                    wrapped += 1
        return wrapped

    def terminate(self) -> None:
        """Disable all wrappers and restore attributes when still owned by us."""

        with self._lock:
            bindings = tuple(self._bindings.values())
            self._bindings.clear()
            for binding in bindings:
                binding.active = False
                self._restore(binding, "text_chat")
                self._restore(binding, "text_chat_stream")

    def _has_active_wrapper(self, provider: object) -> bool:
        binding = self._bindings.get(id(provider))
        if binding is None or binding.provider is not provider:
            return False
        if not binding.active:
            return False
        try:
            return (
                getattr(provider, "text_chat")  # noqa: B009 - host object is dynamically typed
                is binding.text_chat_watcher
                and getattr(  # noqa: B009 - host object is dynamically typed
                    provider,
                    "text_chat_stream",
                )
                is binding.text_chat_stream_watcher
            )
        except Exception:  # noqa: BLE001 - provider reloaded during hook execution
            return False

    def _is_openai_compatible_descriptor(self, descriptor: tuple[str, str]) -> bool:
        provider_type, provider_id = descriptor
        if provider_type in _NATIVE_THINKING_PROVIDER_TYPES:
            return False
        return (
            provider_type in _OPENAI_COMPAT_PROVIDER_TYPES
            or provider_id in self._configured_provider_ids
        )

    def _disable_existing_binding(self, provider: object) -> None:
        binding = self._bindings.pop(id(provider), None)
        if binding is None or binding.provider is not provider:
            return
        binding.active = False
        self._restore(binding, "text_chat")
        self._restore(binding, "text_chat_stream")

    def _install(self, provider: object) -> bool:
        self._disable_existing_binding(provider)
        try:
            original_text_chat = getattr(  # noqa: B009 - host object is dynamically typed
                provider,
                "text_chat",
            )
            original_text_chat_stream = getattr(  # noqa: B009 - host object is dynamically typed
                provider,
                "text_chat_stream",
            )
        except Exception:  # noqa: BLE001 - cannot safely wrap this provider
            return False
        if not callable(original_text_chat) or not callable(original_text_chat_stream):
            return False

        binding = _ProviderThinkingBinding(
            provider=provider,
            original_text_chat=original_text_chat,
            original_text_chat_stream=original_text_chat_stream,
            text_chat_watcher=None,
            text_chat_stream_watcher=None,
            text_chat_was_instance_attribute=_instance_has_attribute(provider, "text_chat"),
            text_chat_stream_was_instance_attribute=_instance_has_attribute(
                provider,
                "text_chat_stream",
            ),
        )
        owner_ref = weakref.ref(self)

        @wraps(original_text_chat)
        async def wrapped_text_chat(*args: object, **kwargs: object) -> object:
            owner = owner_ref()
            if owner is None or not binding.active:
                return await binding.original_text_chat(*args, **kwargs)
            forwarded_args, forwarded_kwargs = owner._rewrite_call(
                binding,
                binding.original_text_chat,
                args,
                kwargs,
            )
            return await binding.original_text_chat(*forwarded_args, **forwarded_kwargs)

        @wraps(original_text_chat_stream)
        def wrapped_text_chat_stream(*args: object, **kwargs: object) -> object:
            owner = owner_ref()
            if owner is None or not binding.active:
                return binding.original_text_chat_stream(*args, **kwargs)
            forwarded_args, forwarded_kwargs = owner._rewrite_call(
                binding,
                binding.original_text_chat_stream,
                args,
                kwargs,
            )
            return binding.original_text_chat_stream(*forwarded_args, **forwarded_kwargs)

        binding.text_chat_watcher = wrapped_text_chat
        binding.text_chat_stream_watcher = wrapped_text_chat_stream
        try:
            setattr(  # noqa: B010 - host object is dynamically typed
                provider,
                "text_chat",
                wrapped_text_chat,
            )
            setattr(  # noqa: B010 - host object is dynamically typed
                provider,
                "text_chat_stream",
                wrapped_text_chat_stream,
            )
        except Exception:  # noqa: BLE001 - revert a partial host mutation
            binding.active = False
            self._restore(binding, "text_chat")
            self._restore(binding, "text_chat_stream")
            return False
        self._bindings[id(provider)] = binding
        return True

    @staticmethod
    def _restore(binding: _ProviderThinkingBinding, name: str) -> None:
        wrapper = (
            binding.text_chat_watcher if name == "text_chat" else binding.text_chat_stream_watcher
        )
        original = (
            binding.original_text_chat if name == "text_chat" else binding.original_text_chat_stream
        )
        was_instance_attribute = (
            binding.text_chat_was_instance_attribute
            if name == "text_chat"
            else binding.text_chat_stream_was_instance_attribute
        )
        try:
            if getattr(binding.provider, name) is not wrapper:
                return
            if was_instance_attribute:
                setattr(binding.provider, name, original)
            else:
                delattr(binding.provider, name)
        except Exception:  # noqa: BLE001 - provider teardown is always best effort
            return

    def _rewrite_call(
        self,
        binding: _ProviderThinkingBinding,
        original: Callable[..., object],
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> tuple[tuple[object, ...], dict[str, object]]:
        if not binding.active:
            return args, kwargs
        descriptor = _provider_descriptor(binding.provider)
        if descriptor is None or not self._is_openai_compatible_descriptor(descriptor):
            return args, kwargs

        try:
            signature = inspect.signature(original)
            bound = signature.bind_partial(*args, **kwargs)
        except (TypeError, ValueError):
            return self._rewrite_keyword_only_call(binding.provider, args, kwargs)

        has_var_positional = any(
            parameter.kind is inspect.Parameter.VAR_POSITIONAL
            for parameter in signature.parameters.values()
        )
        model = _effective_model_identity(
            binding.provider,
            _bound_call_value(signature, bound, "model"),
        )
        if model is None or not _CLAUDE_MODEL_PATTERN.search(model):
            return args, kwargs

        if has_var_positional and any(
            parameter.kind is inspect.Parameter.VAR_POSITIONAL
            and bound.arguments.get(parameter.name)
            for parameter in signature.parameters.values()
        ):
            raise ThinkingCompatibilityBlockedError("CALL_SIGNATURE_UNAVAILABLE")

        contexts = _bound_call_value(signature, bound, "contexts")
        tool_calls_result = _bound_call_value(
            signature,
            bound,
            "tool_calls_result",
        )
        rewritten_contexts, consume_tool_calls_result = _rewrite_call_contexts(
            contexts,
            tool_calls_result,
        )
        if rewritten_contexts is contexts and not consume_tool_calls_result:
            return args, kwargs

        var_keyword_parameter = next(
            (
                parameter
                for parameter in signature.parameters.values()
                if parameter.kind is inspect.Parameter.VAR_KEYWORD
            ),
            None,
        )
        keyword_values: dict[str, object] | None = None
        if var_keyword_parameter is not None:
            raw_keyword_values = bound.arguments.get(var_keyword_parameter.name)
            keyword_values = (
                dict(raw_keyword_values) if isinstance(raw_keyword_values, Mapping) else {}
            )

        if rewritten_contexts is not _MISSING:
            if "contexts" in signature.parameters:
                bound.arguments["contexts"] = rewritten_contexts
            elif keyword_values is not None:
                keyword_values["contexts"] = rewritten_contexts
            else:
                raise ThinkingCompatibilityBlockedError("CALL_SIGNATURE_UNAVAILABLE")

        if consume_tool_calls_result:
            if "tool_calls_result" in signature.parameters:
                bound.arguments["tool_calls_result"] = None
            elif keyword_values is not None:
                keyword_values["tool_calls_result"] = None
            else:
                raise ThinkingCompatibilityBlockedError("CALL_SIGNATURE_UNAVAILABLE")

        if keyword_values is not None:
            assert var_keyword_parameter is not None
            bound.arguments[var_keyword_parameter.name] = keyword_values
        return tuple(bound.args), dict(bound.kwargs)

    def _rewrite_keyword_only_call(
        self,
        provider: object,
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> tuple[tuple[object, ...], dict[str, object]]:
        model = _effective_model_identity(provider, kwargs.get("model"))
        if model is None or not _CLAUDE_MODEL_PATTERN.search(model):
            return args, kwargs
        if args:
            # Generic signatures cannot prove that no positional argument is a
            # context/model value; do not let a target Claude call bypass COW.
            raise ThinkingCompatibilityBlockedError("CALL_SIGNATURE_UNAVAILABLE")
        if "contexts" not in kwargs:
            contexts: object = _MISSING
        else:
            contexts = kwargs["contexts"]
        tool_calls_result = kwargs.get("tool_calls_result", _MISSING)
        rewritten_contexts, consume_tool_calls_result = _rewrite_call_contexts(
            contexts,
            tool_calls_result,
        )
        if rewritten_contexts is contexts and not consume_tool_calls_result:
            return args, kwargs
        rewritten = dict(kwargs)
        if rewritten_contexts is not _MISSING:
            rewritten["contexts"] = rewritten_contexts
        if consume_tool_calls_result:
            rewritten["tool_calls_result"] = None
        return args, rewritten
