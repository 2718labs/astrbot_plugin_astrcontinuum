from __future__ import annotations

import asyncio
import gc
import inspect
import weakref
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from astrcontinuum.adapters.provider_thinking import (
    ProviderThinkingCompatibility,
    ThinkingCompatibilityBlockedError,
)


@dataclass
class FakeThinkPart:
    think: str
    encrypted: str | None = None
    type: str = "think"


@dataclass
class FakeMessage:
    role: str
    content: object
    tool_calls: list[dict[str, object]] | None = None

    def model_dump(self) -> dict[str, object]:
        payload: dict[str, object] = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            payload["tool_calls"] = self.tool_calls
        return payload


@dataclass
class FakeToolCallsResult:
    messages: list[object]
    calls: int = 0

    def to_openai_messages(self) -> list[object]:
        self.calls += 1
        return self.messages


class FakeProvider:
    def __init__(
        self,
        *,
        provider_type: str,
        model: str,
        provider_id: str = "provider",
    ) -> None:
        self._provider_type = provider_type
        self._model = model
        self._provider_id = provider_id
        self.calls: list[dict[str, object]] = []
        self.stream_calls: list[dict[str, object]] = []
        self.error: BaseException | None = None
        self.stream_error: BaseException | None = None
        self.stream_closed = False

    def meta(self) -> SimpleNamespace:
        return SimpleNamespace(type=self._provider_type, id=self._provider_id)

    def get_model(self) -> str:
        return self._model

    async def text_chat(
        self,
        prompt: str | None = None,
        session_id: str | None = None,
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        func_tool: object | None = None,
        contexts: list[object] | None = None,
        system_prompt: str | None = None,
        tool_calls_result: object | None = None,
        model: str | None = None,
        **kwargs: object,
    ) -> SimpleNamespace:
        del session_id, image_urls, audio_urls, func_tool, system_prompt
        if self.error is not None:
            raise self.error
        self.calls.append(
            {
                "prompt": prompt,
                "contexts": contexts,
                "tool_calls_result": tool_calls_result,
                "model": model,
                "kwargs": kwargs,
            }
        )
        return SimpleNamespace(completion_text="ok")

    async def text_chat_stream(
        self,
        prompt: str | None = None,
        session_id: str | None = None,
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        func_tool: object | None = None,
        contexts: list[object] | None = None,
        system_prompt: str | None = None,
        tool_calls_result: object | None = None,
        model: str | None = None,
        **kwargs: object,
    ):
        del session_id, image_urls, audio_urls, func_tool, system_prompt
        try:
            self.stream_calls.append(
                {
                    "prompt": prompt,
                    "contexts": contexts,
                    "tool_calls_result": tool_calls_result,
                    "model": model,
                    "kwargs": kwargs,
                }
            )
            if self.stream_error is not None:
                raise self.stream_error
            yield SimpleNamespace(is_chunk=False, completion_text="stream ok")
        finally:
            self.stream_closed = True


class FakeProviderContext:
    def __init__(self, providers: list[FakeProvider]) -> None:
        self._providers = providers

    def get_all_providers(self) -> list[FakeProvider]:
        return self._providers


def _tool_call(
    tool_id: str,
    *,
    extra_content: object | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": tool_id,
        "type": "function",
        "function": {"name": "lookup", "arguments": '{"q":"weather"}'},
    }
    if extra_content is not None:
        payload["extra_content"] = extra_content
    return payload


def _thinking_contexts() -> list[object]:
    return [
        FakeMessage(
            role="assistant",
            content=[
                FakeThinkPart("visible reasoning", encrypted="native-signature"),
                {
                    "type": "text",
                    "text": "visible answer",
                    "signature": "content-signature",
                },
            ],
            tool_calls=[_tool_call("tool-1")],
        ),
        {
            "role": "tool",
            "tool_call_id": "tool-1",
            "content": "tool result",
        },
    ]


@pytest.mark.asyncio
async def test_wrapper_cow_downgrades_thinkpart_for_openai_compatible_claude_call() -> None:
    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-opus-4-6",
    )
    compatibility = ProviderThinkingCompatibility()
    compatibility.ensure(FakeProviderContext([provider]))
    contexts = _thinking_contexts()
    original = deepcopy(contexts)

    await provider.text_chat(
        "prompt",
        None,
        None,
        None,
        None,
        contexts,
        None,
        None,
        None,
    )

    assert contexts == original
    outbound = provider.calls[-1]["contexts"]
    assert isinstance(outbound, list)
    assert outbound is not contexts
    assistant = outbound[0]
    assert isinstance(assistant, dict)
    assert assistant["tool_calls"] == [_tool_call("tool-1")]
    assert assistant["content"] == [
        {
            "type": "text",
            "text": "[assistant reasoning]\nvisible reasoning",
        },
        {"type": "text", "text": "visible answer"},
    ]
    assert "native-signature" not in repr(assistant)
    assert "content-signature" not in repr(assistant)
    assert outbound[1] is contexts[1]


@pytest.mark.asyncio
async def test_wrapper_covers_fallback_stream_and_strips_unsafe_reasoning_fields() -> None:
    primary = FakeProvider(provider_type="openai_chat_completion", model="gpt-4o")
    fallback = FakeProvider(
        provider_type="openrouter_chat_completion",
        model="claude-sonnet-4-5",
    )
    compatibility = ProviderThinkingCompatibility()
    compatibility.ensure(FakeProviderContext([primary, fallback]))
    contexts: list[object] = [
        {
            "role": "assistant",
            "content": "visible answer",
            "reasoning_content": "legacy reasoning",
            "reasoning": "legacy alias",
            "tool_calls": [_tool_call("tool-2")],
        },
        {
            "role": "tool",
            "tool_call_id": "tool-2",
            "content": "tool result",
        },
    ]
    original = deepcopy(contexts)

    stream = fallback.text_chat_stream(prompt="retry", contexts=contexts)
    assert inspect.isasyncgen(stream)
    chunks = [chunk async for chunk in stream]

    assert [chunk.completion_text for chunk in chunks] == ["stream ok"]
    assert contexts == original
    outbound = fallback.stream_calls[-1]["contexts"]
    assert isinstance(outbound, list)
    assistant = outbound[0]
    assert isinstance(assistant, dict)
    assert "reasoning_content" not in assistant
    assert "reasoning" not in assistant
    assert assistant["tool_calls"] == [_tool_call("tool-2")]
    assert isinstance(assistant["content"], str)
    assert "visible answer" in assistant["content"]
    assert "legacy reasoning" in assistant["content"]
    assert outbound[1] is contexts[1]


@pytest.mark.asyncio
async def test_wrapper_never_leaks_redacted_blob_and_skips_native_provider() -> None:
    claude_gateway = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-sonnet-4-5",
    )
    native = FakeProvider(
        provider_type="anthropic_chat_completion",
        model="claude-sonnet-4-5",
        provider_id="native-override",
    )
    gemini = FakeProvider(
        provider_type="googlegenai_chat_completion",
        model="gemini-2.5-pro",
    )
    compatibility = ProviderThinkingCompatibility(
        openai_compatible_provider_ids=("native-override",),
    )
    compatibility.ensure(FakeProviderContext([claude_gateway, native, gemini]))
    contexts: list[object] = [
        {
            "role": "assistant",
            "content": [{"type": "redacted_thinking", "data": "secret-blob"}],
        }
    ]

    await claude_gateway.text_chat(prompt="gateway", contexts=contexts)
    await native.text_chat(prompt="native", contexts=contexts)
    await gemini.text_chat(prompt="gemini", contexts=contexts)

    outbound = claude_gateway.calls[-1]["contexts"]
    assert isinstance(outbound, list)
    assert "secret-blob" not in repr(outbound)
    assert "[assistant reasoning redacted]" in repr(outbound)
    assert native.calls[-1]["contexts"] is contexts
    assert gemini.calls[-1]["contexts"] is contexts
    assert "text_chat" not in native.__dict__
    assert "text_chat" not in gemini.__dict__


@pytest.mark.asyncio
async def test_wrapper_bypasses_and_reconciles_when_live_provider_type_drifts() -> None:
    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-opus-4-6",
    )
    context = FakeProviderContext([provider])
    compatibility = ProviderThinkingCompatibility()
    compatibility.ensure(context)
    assert "text_chat" in provider.__dict__

    provider._provider_type = "anthropic_chat_completion"
    contexts = _thinking_contexts()
    await provider.text_chat(prompt="native after drift", contexts=contexts)

    assert provider.calls[-1]["contexts"] is contexts
    compatibility.ensure(context)
    assert "text_chat" not in provider.__dict__


@pytest.mark.asyncio
async def test_terminate_restores_original_methods_and_propagates_provider_errors() -> None:
    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-opus-4-6",
    )
    compatibility = ProviderThinkingCompatibility()
    compatibility.ensure(FakeProviderContext([provider]))
    provider.error = RuntimeError("provider failure")

    with pytest.raises(RuntimeError, match="provider failure"):
        await provider.text_chat(prompt="fail", contexts=_thinking_contexts())

    compatibility.terminate()
    assert "text_chat" not in provider.__dict__
    assert "text_chat_stream" not in provider.__dict__
    provider.error = None
    contexts = _thinking_contexts()
    await provider.text_chat(prompt="unwrapped", contexts=contexts)
    assert provider.calls[-1]["contexts"] is contexts


@pytest.mark.asyncio
async def test_wrapper_is_reentrant_idempotent_and_uses_actual_call_model() -> None:
    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-opus-4-6",
    )
    compatibility = ProviderThinkingCompatibility()
    context = FakeProviderContext([provider])
    compatibility.ensure(context)
    first_wrapper = provider.__dict__["text_chat"]
    compatibility.ensure(context)
    assert provider.__dict__["text_chat"] is first_wrapper
    reloaded = FakeProvider(
        provider_type="openrouter_chat_completion",
        model="claude-sonnet-4-5",
        provider_id="reloaded-provider",
    )
    context._providers.append(reloaded)
    compatibility.ensure(context)
    assert "text_chat" in reloaded.__dict__

    plaintext_contexts = _thinking_contexts()
    await provider.text_chat(
        "explicit non-target model",
        None,
        None,
        None,
        None,
        plaintext_contexts,
        None,
        None,
        "gpt-4o",
    )
    assert provider.calls[-1]["contexts"] is plaintext_contexts

    first = _thinking_contexts()
    second = _thinking_contexts()
    await asyncio.gather(
        provider.text_chat(prompt="first", contexts=first),
        provider.text_chat(prompt="second", contexts=second),
    )
    outbound_contexts = [call["contexts"] for call in provider.calls[-2:]]
    assert isinstance(outbound_contexts[0], list)
    assert isinstance(outbound_contexts[1], list)
    assert outbound_contexts[0] is not first
    assert outbound_contexts[1] is not second
    assert all("native-signature" not in repr(value) for value in outbound_contexts)
    assert "native-signature" in repr(first)
    assert "native-signature" in repr(second)
    context._providers[:] = [reloaded]
    compatibility.ensure(context)
    assert "text_chat" not in provider.__dict__


@pytest.mark.asyncio
async def test_explicit_provider_id_allows_custom_openai_route_but_not_unknown_routes() -> None:
    configured = FakeProvider(
        provider_type="custom_chat_completion",
        model="claude-sonnet-4-5",
        provider_id="known-openai-gateway",
    )
    unknown = FakeProvider(
        provider_type="custom_chat_completion",
        model="claude-sonnet-4-5",
        provider_id="unknown-gateway",
    )
    compatibility = ProviderThinkingCompatibility(
        openai_compatible_provider_ids=("known-openai-gateway",),
    )
    compatibility.ensure(FakeProviderContext([configured, unknown]))

    contexts = _thinking_contexts()
    await configured.text_chat(prompt="configured", contexts=contexts)
    await unknown.text_chat(prompt="unknown", contexts=contexts)

    assert configured.calls[-1]["contexts"] is not contexts
    assert unknown.calls[-1]["contexts"] is contexts
    assert "text_chat" in configured.__dict__
    assert "text_chat" not in unknown.__dict__


@pytest.mark.asyncio
async def test_target_route_blocks_when_assistant_history_cannot_be_safely_parsed() -> None:
    class OpaqueAssistant:
        role = "assistant"
        content = object()

    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-opus-4-6",
    )
    compatibility = ProviderThinkingCompatibility()
    compatibility.ensure(FakeProviderContext([provider]))

    with pytest.raises(ThinkingCompatibilityBlockedError):
        await provider.text_chat(prompt="blocked", contexts=[OpaqueAssistant()])

    assert provider.calls == []


@pytest.mark.asyncio
async def test_wrapper_preserves_reasoning_order_without_duplicate_or_opaque_fields() -> None:
    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-opus-4-6",
    )
    compatibility = ProviderThinkingCompatibility()
    compatibility.ensure(FakeProviderContext([provider]))
    contexts: list[object] = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "think",
                    "think": "same reasoning",
                    "encrypted": "think-signature",
                },
                {
                    "type": "text",
                    "text": "visible answer",
                    "signature": "text-signature",
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://example.invalid/image",
                        "id": "image-1",
                        "signature": "image-signature",
                    },
                },
                {
                    "type": "audio_url",
                    "audio_url": {
                        "url": "https://example.invalid/audio",
                        "id": "audio-1",
                        "encrypted": "audio-signature",
                    },
                },
            ],
            "reasoning_content": "same reasoning",
            "reasoning": "second reasoning",
            "signature": "top-level-signature",
            "tool_calls": [
                _tool_call(
                    "tool-3",
                    extra_content={
                        "google": {"thought_signature": "tool-sidecar-signature"},
                        "opaque": "tool-sidecar-opaque",
                    },
                )
            ],
        },
        {"role": "tool", "tool_call_id": "tool-3", "content": "tool fact"},
    ]

    await provider.text_chat(prompt="ordered", contexts=contexts)

    outbound = provider.calls[-1]["contexts"]
    assert isinstance(outbound, list)
    assistant = outbound[0]
    assert isinstance(assistant, dict)
    assert assistant["content"] == [
        {"type": "text", "text": "[assistant reasoning]\nsame reasoning"},
        {"type": "text", "text": "[assistant reasoning]\nsecond reasoning"},
        {"type": "text", "text": "visible answer"},
        {
            "type": "image_url",
            "image_url": {"url": "https://example.invalid/image", "id": "image-1"},
        },
        {
            "type": "audio_url",
            "audio_url": {"url": "https://example.invalid/audio", "id": "audio-1"},
        },
    ]
    assert assistant["tool_calls"] == [_tool_call("tool-3")]
    assert outbound[1] is contexts[1]
    for canary in (
        "think-signature",
        "text-signature",
        "image-signature",
        "audio-signature",
        "top-level-signature",
        "tool-sidecar-signature",
        "tool-sidecar-opaque",
    ):
        assert canary not in repr(assistant)


@pytest.mark.asyncio
async def test_cow_preserves_interleaved_content_order_and_duplicate_think_blocks() -> None:
    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-opus-4-6",
    )
    compatibility = ProviderThinkingCompatibility()
    compatibility.ensure(FakeProviderContext([provider]))
    contexts: list[object] = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "before"},
                {
                    "type": "think",
                    "think": "repeated reasoning",
                    "signature": "first-think-signature",
                },
                {"type": "text", "text": "between"},
                {
                    "type": "think",
                    "think": "repeated reasoning",
                    "signature": "second-think-signature",
                },
                {"type": "text", "text": "after"},
            ],
            "reasoning_content": "repeated reasoning",
            "reasoning": "repeated reasoning",
        }
    ]
    original = deepcopy(contexts)

    await provider.text_chat(prompt="ordered duplicates", contexts=contexts)

    outbound = provider.calls[-1]["contexts"]
    assert isinstance(outbound, list)
    assistant = outbound[0]
    assert isinstance(assistant, dict)
    assert assistant["content"] == [
        {"type": "text", "text": "before"},
        {
            "type": "text",
            "text": "[assistant reasoning]\nrepeated reasoning",
        },
        {"type": "text", "text": "between"},
        {
            "type": "text",
            "text": "[assistant reasoning]\nrepeated reasoning",
        },
        {"type": "text", "text": "after"},
    ]
    assert "first-think-signature" not in repr(assistant)
    assert "second-think-signature" not in repr(assistant)
    assert contexts == original


@pytest.mark.asyncio
async def test_target_route_projects_tool_sidecars_without_reasoning_fields() -> None:
    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-opus-4-6",
    )
    compatibility = ProviderThinkingCompatibility()
    compatibility.ensure(FakeProviderContext([provider]))
    contexts: list[object] = [
        {
            "role": "assistant",
            "content": "calling a tool",
            "tool_calls": [
                _tool_call(
                    "tool-sidecar-only",
                    extra_content={
                        "google": {"thought_signature": "tool-signature-canary"},
                        "opaque": "tool-opaque-canary",
                    },
                )
            ],
        }
    ]
    original = deepcopy(contexts)

    await provider.text_chat(prompt="tool-sidecar", contexts=contexts)

    outbound = provider.calls[-1]["contexts"]
    assert isinstance(outbound, list)
    assert outbound is not contexts
    assert outbound[0] == {
        "role": "assistant",
        "content": "calling a tool",
        "tool_calls": [_tool_call("tool-sidecar-only")],
    }
    assert "tool-signature-canary" not in repr(outbound)
    assert "tool-opaque-canary" not in repr(outbound)
    assert contexts == original


@pytest.mark.asyncio
async def test_target_route_projects_tool_calls_result_once_after_contexts() -> None:
    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-opus-4-6",
    )
    compatibility = ProviderThinkingCompatibility()
    compatibility.ensure(FakeProviderContext([provider]))
    contexts: list[object] = [
        {"role": "user", "content": "lookup weather"},
    ]
    original_contexts = deepcopy(contexts)
    tool_messages: list[object] = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "before"},
                {
                    "type": "think",
                    "think": "tool reasoning",
                    "signature": "duplicate-think-signature",
                },
                {"type": "text", "text": "between"},
                {
                    "type": "think",
                    "think": "tool reasoning",
                    "encrypted": "interleaved-think-encrypted",
                },
                {"type": "text", "text": "after"},
            ],
            "tool_calls": [
                _tool_call(
                    "tool-result-1",
                    extra_content={
                        "google": {"thought_signature": "tool-sidecar-signature"},
                        "opaque": "tool-sidecar-opaque",
                    },
                )
            ],
        },
        {"role": "tool", "tool_call_id": "tool-result-1", "content": "sunny"},
    ]
    original_tool_messages = deepcopy(tool_messages)
    tool_calls_result = FakeToolCallsResult(tool_messages)

    await provider.text_chat(
        prompt="tool result",
        contexts=contexts,
        tool_calls_result=tool_calls_result,
    )

    assert contexts == original_contexts
    assert tool_messages == original_tool_messages
    assert tool_calls_result.calls == 1
    call = provider.calls[-1]
    assert call["tool_calls_result"] is None
    outbound = call["contexts"]
    assert isinstance(outbound, list)
    assert outbound is not contexts
    assert [message["role"] for message in outbound] == ["user", "assistant", "tool"]
    assistant = outbound[1]
    assert isinstance(assistant, dict)
    assert assistant["content"] == [
        {"type": "text", "text": "before"},
        {"type": "text", "text": "[assistant reasoning]\ntool reasoning"},
        {"type": "text", "text": "between"},
        {"type": "text", "text": "[assistant reasoning]\ntool reasoning"},
        {"type": "text", "text": "after"},
    ]
    assert assistant["tool_calls"] == [_tool_call("tool-result-1")]
    assert outbound[2] is tool_messages[1]
    for canary in (
        "duplicate-think-signature",
        "interleaved-think-encrypted",
        "tool-sidecar-signature",
        "tool-sidecar-opaque",
    ):
        assert canary not in repr(outbound)


@pytest.mark.asyncio
async def test_target_route_projects_nested_media_sidecars_without_reasoning() -> None:
    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-opus-4-6",
    )
    compatibility = ProviderThinkingCompatibility()
    compatibility.ensure(FakeProviderContext([provider]))
    contexts: list[object] = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://example.invalid/image",
                        "id": "image-1",
                        "signature": "nested-image-signature",
                    },
                },
                {
                    "type": "audio_url",
                    "audio_url": {
                        "url": "https://example.invalid/audio",
                        "id": "audio-1",
                        "encrypted": "nested-audio-encrypted",
                    },
                },
            ],
        }
    ]
    original = deepcopy(contexts)

    await provider.text_chat(prompt="media only", contexts=contexts)

    outbound = provider.calls[-1]["contexts"]
    assert isinstance(outbound, list)
    assert outbound is not contexts
    assert outbound[0] == {
        "role": "assistant",
        "content": [
            {
                "type": "image_url",
                "image_url": {
                    "url": "https://example.invalid/image",
                    "id": "image-1",
                },
            },
            {
                "type": "audio_url",
                "audio_url": {
                    "url": "https://example.invalid/audio",
                    "id": "audio-1",
                },
            },
        ],
    }
    assert "nested-image-signature" not in repr(outbound)
    assert "nested-audio-encrypted" not in repr(outbound)
    assert contexts == original

    with pytest.raises(ThinkingCompatibilityBlockedError, match="MEDIA_PART_UNSAFE"):
        await provider.text_chat(
            prompt="unknown media sidecar",
            contexts=[
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "https://example.invalid/image",
                                "opaque_sidecar": "must-not-pass",
                            },
                        }
                    ],
                }
            ],
        )
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_target_route_blocks_unknown_assistant_content_instead_of_replacing_it() -> None:
    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-opus-4-6",
    )
    compatibility = ProviderThinkingCompatibility()
    compatibility.ensure(FakeProviderContext([provider]))
    contexts: list[object] = [
        {
            "role": "assistant",
            "content": [{"type": "opaque_content", "data": "opaque-canary"}],
        }
    ]

    with pytest.raises(ThinkingCompatibilityBlockedError):
        await provider.text_chat(prompt="unsafe", contexts=contexts)

    assert provider.calls == []


@pytest.mark.asyncio
async def test_target_context_none_is_transparent_but_non_list_is_blocked() -> None:
    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-opus-4-6",
    )
    compatibility = ProviderThinkingCompatibility()
    compatibility.ensure(FakeProviderContext([provider]))

    await provider.text_chat(prompt="empty", contexts=None)
    assert provider.calls[-1]["contexts"] is None

    with pytest.raises(ThinkingCompatibilityBlockedError):
        await provider.text_chat(prompt="tuple", contexts=tuple(_thinking_contexts()))

    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_stream_close_and_cancellation_keep_the_original_async_generator_semantics() -> None:
    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-opus-4-6",
    )
    compatibility = ProviderThinkingCompatibility()
    compatibility.ensure(FakeProviderContext([provider]))

    stream = provider.text_chat_stream(prompt="close", contexts=_thinking_contexts())
    assert inspect.isasyncgen(stream)
    assert (await anext(stream)).completion_text == "stream ok"
    await stream.aclose()
    assert provider.stream_closed

    provider.stream_closed = False
    provider.stream_error = asyncio.CancelledError()
    cancelled = provider.text_chat_stream(prompt="cancel", contexts=_thinking_contexts())
    with pytest.raises(asyncio.CancelledError):
        await anext(cancelled)
    assert provider.stream_closed


@pytest.mark.asyncio
async def test_wrapper_can_chain_after_an_earlier_third_party_wrapper() -> None:
    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-opus-4-6",
    )
    original = provider.text_chat
    third_party_calls: list[object] = []

    async def earlier_wrapper(*args: object, **kwargs: object) -> object:
        third_party_calls.append((args, kwargs))
        return await original(*args, **kwargs)

    provider.text_chat = earlier_wrapper
    compatibility = ProviderThinkingCompatibility()
    compatibility.ensure(FakeProviderContext([provider]))

    contexts = _thinking_contexts()
    await provider.text_chat(prompt="chained", contexts=contexts)
    assert third_party_calls
    assert provider.calls[-1]["contexts"] is not contexts

    compatibility.terminate()
    assert provider.text_chat is earlier_wrapper
    await provider.text_chat(prompt="after", contexts=contexts)
    assert provider.calls[-1]["contexts"] is contexts


@pytest.mark.asyncio
async def test_generic_wrapper_honors_keyword_claude_override_and_blocks_positional_calls() -> None:
    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="gpt-4o",
    )
    original = provider.text_chat
    third_party_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def generic_wrapper(*args: object, **kwargs: object) -> object:
        third_party_calls.append((args, kwargs))
        return await original(*args, **kwargs)

    provider.text_chat = generic_wrapper
    compatibility = ProviderThinkingCompatibility()
    compatibility.ensure(FakeProviderContext([provider]))

    contexts = _thinking_contexts()
    await provider.text_chat(
        model="claude-opus-4-6",
        contexts=contexts,
    )

    outbound = provider.calls[-1]["contexts"]
    assert isinstance(outbound, list)
    assert outbound is not contexts
    assert third_party_calls[-1][1]["contexts"] is outbound

    with pytest.raises(ThinkingCompatibilityBlockedError):
        await provider.text_chat(
            "positional prompt",
            model="claude-opus-4-6",
            contexts=_thinking_contexts(),
        )

    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_terminate_does_not_overwrite_a_later_wrapper_and_disables_its_own_layer() -> None:
    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-opus-4-6",
    )
    compatibility = ProviderThinkingCompatibility()
    compatibility.ensure(FakeProviderContext([provider]))
    prior = provider.text_chat

    async def later_wrapper(*args: object, **kwargs: object) -> object:
        return await prior(*args, **kwargs)

    provider.text_chat = later_wrapper
    compatibility.terminate()
    assert provider.text_chat is later_wrapper

    contexts = _thinking_contexts()
    await provider.text_chat(prompt="after terminate", contexts=contexts)
    assert provider.calls[-1]["contexts"] is contexts


@pytest.mark.asyncio
async def test_post_wrapper_reloads_release_owner_without_accumulating_layers() -> None:
    provider = FakeProvider(
        provider_type="openai_chat_completion",
        model="claude-opus-4-6",
    )
    first_compatibility = ProviderThinkingCompatibility()
    first_compatibility.ensure(FakeProviderContext([provider]))
    captured_compatibility_wrapper = provider.text_chat
    third_party_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def third_party_wrapper(*args: object, **kwargs: object) -> object:
        third_party_calls.append((args, kwargs))
        return await captured_compatibility_wrapper(*args, **kwargs)

    provider.text_chat = third_party_wrapper
    old_owner = weakref.ref(first_compatibility)
    first_compatibility.terminate()
    del first_compatibility
    gc.collect()

    assert old_owner() is None
    assert provider.text_chat is third_party_wrapper

    for reload_index in range(3):
        compatibility = ProviderThinkingCompatibility()
        compatibility.ensure(FakeProviderContext([provider]))
        reload_wrapper = provider.text_chat
        assert reload_wrapper is not third_party_wrapper
        assert reload_wrapper.__wrapped__ is third_party_wrapper

        contexts = _thinking_contexts()
        await provider.text_chat(
            prompt=f"reload {reload_index}",
            contexts=contexts,
        )
        assert provider.calls[-1]["contexts"] is not contexts

        compatibility.terminate()
        assert provider.text_chat is third_party_wrapper

    contexts = _thinking_contexts()
    provider_call_count = len(provider.calls)
    third_party_call_count = len(third_party_calls)
    await provider.text_chat(prompt="after reloads", contexts=contexts)

    assert provider.text_chat is third_party_wrapper
    assert len(third_party_calls) == third_party_call_count + 1
    assert len(provider.calls) == provider_call_count + 1
    assert provider.calls[-1]["contexts"] is contexts
