#!/usr/bin/env python3
"""Probe AstrContinuum against real AstrBot APIs without sending an LLM request."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import os
import socket
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

EXPECTED_HOOKS = {
    "on_agent_begin": 2,
    "on_agent_done": 2,
    "on_llm_request": 1,
    "on_llm_response": 1,
    "on_llm_tool_respond": 1,
    "on_using_llm_tool": 1,
}


class ProbeError(RuntimeError):
    """A public AstrBot compatibility contract was not satisfied."""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-astrbot-version",
        help="optionally require one exact installed AstrBot version",
    )
    return parser.parse_args()


def _import_plugin_with_hook_evidence() -> tuple[type[Any], Counter[str]]:
    from astrbot.api.event import filter
    from astrbot.api.star import Star

    records: Counter[str] = Counter()
    originals: dict[str, Any] = {}
    for hook_name in EXPECTED_HOOKS:
        original = getattr(filter, hook_name)
        originals[hook_name] = original

        def recording_hook(
            *args: object,
            _hook_name: str = hook_name,
            _original: Any = original,
            **kwargs: object,
        ) -> Any:
            decorator = _original(*args, **kwargs)

            def record(function: Any) -> Any:
                records[_hook_name] += 1
                return decorator(function)

            return record

        setattr(filter, hook_name, recording_hook)

    try:
        sys.modules.pop("main", None)
        plugin_module = importlib.import_module("main")
    finally:
        for hook_name, original in originals.items():
            setattr(filter, hook_name, original)

    plugin_class = getattr(plugin_module, "AstrContinuumPlugin", None)
    if not isinstance(plugin_class, type) or not issubclass(plugin_class, Star):
        raise ProbeError("plugin did not import as a public AstrBot Star")
    if dict(records) != EXPECTED_HOOKS:
        raise ProbeError(
            f"public hook registration mismatch: expected={EXPECTED_HOOKS}, actual={dict(records)}"
        )
    return plugin_class, records


def _probe_provider_metadata() -> tuple[str, int, int]:
    from astrbot.api.provider import Provider, ProviderRequest
    from astrbot.api.star import Context

    from astrcontinuum.tokenization import resolve_astrbot_request_metadata

    class ProbeProvider(Provider):
        def __init__(self) -> None:
            super().__init__(
                {
                    "id": "astrcontinuum-release-probe",
                    "max_context_tokens": 131_072,
                },
                {},
            )
            self.model_name = "gpt-4o-mini"
            self.llm_calls = 0

        def get_current_key(self) -> str:
            return "redacted-probe-key"

        def set_key(self, key: str) -> None:
            del key

        def get_models(self) -> list[str]:
            return [self.model_name]

        async def text_chat(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            self.llm_calls += 1
            raise ProbeError("the release probe must not send an LLM request")

    class ProbeProviderManager:
        def __init__(self, provider: Provider) -> None:
            self.provider = provider
            self.calls: list[dict[str, object]] = []

        def get_using_provider(self, **kwargs: object) -> Provider:
            self.calls.append(dict(kwargs))
            return self.provider

    class ProbeEvent:
        unified_msg_origin = "release-probe:platform:conversation"

    provider = ProbeProvider()
    manager = ProbeProviderManager(provider)
    context = object.__new__(Context)
    context.provider_manager = manager
    request = ProviderRequest(model=None)

    metadata = resolve_astrbot_request_metadata(context, ProbeEvent(), request)
    if (
        metadata.request_model is not None
        or metadata.provider_model != provider.model_name
        or metadata.model_identity != provider.model_name
        or metadata.provider_limit != 131_072
    ):
        raise ProbeError("real Provider/ProviderRequest metadata extraction failed")
    if len(manager.calls) != 1:
        raise ProbeError("Context.get_using_provider was not exercised exactly once")
    if manager.calls[0].get("umo") != ProbeEvent.unified_msg_origin:
        raise ProbeError("Context.get_using_provider did not receive the unified origin")
    return provider.model_name, metadata.provider_limit, provider.llm_calls


def _probe_offline_registry() -> int:
    from astrcontinuum.tokenization import OPENAI_O200K, TokenizerRegistry

    def block_network(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise ProbeError("tokenizer attempted network access")

    original_connect = socket.socket.connect
    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo
    previous_cache = os.environ.get("TIKTOKEN_CACHE_DIR")
    previous_data_cache = os.environ.get("DATA_GYM_CACHE_DIR")

    with tempfile.TemporaryDirectory(prefix="astrcontinuum-tokenizer-probe-") as temporary:
        cache = Path(temporary)
        os.environ["TIKTOKEN_CACHE_DIR"] = str(cache)
        os.environ["DATA_GYM_CACHE_DIR"] = str(cache)
        socket.socket.connect = block_network
        socket.create_connection = block_network
        socket.getaddrinfo = block_network
        try:
            registry = TokenizerRegistry()
            counter = registry.counter_for(OPENAI_O200K)
            count = counter.count_text("offline 中文 🚀 <|endoftext|>")
        finally:
            socket.socket.connect = original_connect
            socket.create_connection = original_create_connection
            socket.getaddrinfo = original_getaddrinfo
            if previous_cache is None:
                os.environ.pop("TIKTOKEN_CACHE_DIR", None)
            else:
                os.environ["TIKTOKEN_CACHE_DIR"] = previous_cache
            if previous_data_cache is None:
                os.environ.pop("DATA_GYM_CACHE_DIR", None)
            else:
                os.environ["DATA_GYM_CACHE_DIR"] = previous_data_cache

        if any(cache.iterdir()):
            raise ProbeError("offline registry wrote a mutable tokenizer cache")
    if count <= 0:
        raise ProbeError("offline registry returned a non-positive count")
    return count


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

    try:
        astrbot_version = importlib.metadata.version("astrbot")
    except importlib.metadata.PackageNotFoundError:
        print("AstrBot probe failed: install a pinned astrbot distribution first")
        return 1
    if (
        args.expected_astrbot_version is not None
        and astrbot_version != args.expected_astrbot_version
    ):
        print(
            "AstrBot probe failed: "
            f"expected {args.expected_astrbot_version}, found {astrbot_version}"
        )
        return 1

    try:
        plugin_class, hooks = _import_plugin_with_hook_evidence()
        provider_model, provider_limit, llm_calls = _probe_provider_metadata()
        token_count = _probe_offline_registry()
    except (ImportError, ProbeError, TypeError, ValueError) as error:
        print(f"AstrBot probe failed: {error}")
        return 1

    print(f"astrbot_version={astrbot_version}")
    print(f"plugin_star={plugin_class.__name__}")
    print(f"hook_registrations={sum(hooks.values())}")
    print(f"provider_model_shape={provider_model}")
    print(f"provider_context_limit={provider_limit}")
    print(f"offline_token_count={token_count}")
    print(f"llm_requests_sent={llm_calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
