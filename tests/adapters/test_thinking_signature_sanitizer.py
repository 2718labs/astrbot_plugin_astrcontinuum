from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from astrcontinuum.adapters.astrbot import sanitize_claude_openai_contexts


def test_sanitizer_removes_real_astrbot_v4267_thinking_schema_without_mutation() -> None:
    original = [
        {
            "role": "assistant",
            "content": [
                {"type": "think", "think": "private", "encrypted": "signature"},
                {"type": "text", "text": "answer"},
                {"type": "image_url", "image_url": {"url": "https://example.invalid/a"}},
            ],
            "reasoning_content": "stale",
            "reasoning": "stale alias",
            "custom": {"preserve": True},
        }
    ]
    original_copy = deepcopy(original)
    request = SimpleNamespace(contexts=original)

    assert sanitize_claude_openai_contexts(
        request,
        provider_type="openai_chat_completion",
        model_identity="哈基米😋/claude-opus-4.6",
    )
    assert request.contexts is not original
    assert original == original_copy
    assert request.contexts == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "answer"},
                {"type": "image_url", "image_url": {"url": "https://example.invalid/a"}},
            ],
            "custom": {"preserve": True},
        }
    ]


def test_sanitizer_clears_stale_reasoning_from_non_list_content_copy_on_write() -> None:
    original = [
        {
            "role": "assistant",
            "content": "ordinary answer",
            "reasoning_content": "stale",
            "reasoning": "stale alias",
        }
    ]
    request = SimpleNamespace(contexts=original)

    assert sanitize_claude_openai_contexts(
        request,
        provider_type="openai_chat_completion",
        model_identity="anthropic/claude-3-7-sonnet",
    )
    assert request.contexts is not original
    assert original[0]["reasoning_content"] == "stale"
    assert request.contexts == [{"role": "assistant", "content": "ordinary answer"}]


def test_sanitizer_handles_think_only_and_tool_call_assistants() -> None:
    request = SimpleNamespace(
        contexts=[
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "x"}]},
            {
                "role": "assistant",
                "content": [{"type": "redacted_thinking", "data": "x"}],
                "tool_calls": [{"id": "tool-1", "type": "function"}],
            },
        ]
    )

    assert sanitize_claude_openai_contexts(
        request,
        provider_type="openai_chat_completion",
        model_identity="CLAUDE3",
    )
    assert request.contexts[0]["content"] == [
        {"type": "text", "text": "[assistant reasoning omitted]"}
    ]
    assert request.contexts[1]["content"] is None
    assert request.contexts[1]["tool_calls"] == [{"id": "tool-1", "type": "function"}]


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("哈基米😋/claude-opus-4.6", True),
        ("anthropic/claude-3-7-sonnet", True),
        ("CLAUDE3", True),
        ("claudette", False),
        ("myclaude", False),
        ("deepseek-claudex", False),
    ],
)
def test_sanitizer_uses_bounded_claude_model_matching(model: str, expected: bool) -> None:
    original = [{"role": "assistant", "content": [{"type": "think", "think": "x"}]}]
    request = SimpleNamespace(contexts=original)

    assert (
        sanitize_claude_openai_contexts(
            request,
            provider_type="openai_chat_completion",
            model_identity=model,
        )
        is expected
    )
    assert (request.contexts is not original) is expected


def test_sanitizer_keeps_non_target_context_identity_and_fails_open_on_setattr() -> None:
    original = [{"role": "assistant", "content": [{"type": "think", "think": "x"}]}]
    request = SimpleNamespace(contexts=original)
    assert not sanitize_claude_openai_contexts(
        request,
        provider_type="anthropic",
        model_identity="claude-opus-4.6",
    )
    assert request.contexts is original

    class LockedRequest:
        @property
        def contexts(self) -> list[object]:
            return original

        @contexts.setter
        def contexts(self, _value: object) -> None:
            raise RuntimeError("unavailable")

    assert not sanitize_claude_openai_contexts(
        LockedRequest(),
        provider_type="openai_chat_completion",
        model_identity="claude-opus-4.6",
    )
