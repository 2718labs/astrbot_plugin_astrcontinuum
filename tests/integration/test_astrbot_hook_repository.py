from __future__ import annotations

import json
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrcontinuum.adapters import (
    AdapterErrorCode,
    AstrBotAdapterError,
    AstrBotHookBridge,
)
from astrcontinuum.runtime import TokenCounter
from astrcontinuum.storage import (
    KeyMaterial,
    KeySource,
    ResolvedKeyMaterial,
    SecureCodec,
    SQLiteConnectionFactory,
    SQLiteRepository,
    activate_storage_security,
)
from astrcontinuum.tokenization import (
    BYTE_FALLBACK,
    CANONICAL_O200K,
    ContextLimitSource,
    RequestBudgetProfile,
    TokenizerProfile,
)

TEST_KEY = bytes(range(32))


class FakeMessageType(str, Enum):
    FRIEND = "FriendMessage"


class FakeEvent:
    def __init__(
        self,
        *,
        message_id: str = "message-42",
        sender_id: str = "user-5",
    ) -> None:
        self.message_obj = SimpleNamespace(
            message_id=message_id,
            timestamp=1_727_000_000,
            type=FakeMessageType.FRIEND,
            session_id="session-9",
        )
        self._sender_id = sender_id

    def get_platform_id(self) -> str:
        return "platform-instance-1"

    def get_message_type(self) -> FakeMessageType:
        return FakeMessageType.FRIEND

    def get_session_id(self) -> str:
        return "session-9"

    def get_group_id(self) -> str:
        return ""

    def get_sender_id(self) -> str:
        return self._sender_id


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        prompt="hello from the user",
        contexts=[{"role": "assistant", "content": "native history"}],
        conversation=SimpleNamespace(cid="conversation-3", persona_id=None),
    )


def _budget_profile() -> RequestBudgetProfile:
    return RequestBudgetProfile(
        model_identity=None,
        context_limit=128_000,
        context_limit_source=ContextLimitSource.AUTO_SAFE_FALLBACK,
        tokenizer_profile=BYTE_FALLBACK,
        target_input_budget=100_000,
        hard_input_ceiling=110_000,
    )


class DistinctCanonicalCounter:
    profile = CANONICAL_O200K

    def count_text(self, text: str) -> int:
        return len(text.encode("utf-8")) + 1_000


def _bridge(
    tmp_path: Path,
    *,
    counter_provider: Callable[[TokenizerProfile], TokenCounter] | None = None,
) -> tuple[AstrBotHookBridge, SecureCodec]:
    factory = SQLiteConnectionFactory(tmp_path, busy_timeout_ms=5_000)
    activation = activate_storage_security(
        factory,
        ResolvedKeyMaterial(
            active=KeyMaterial.from_raw(TEST_KEY),
            previous=None,
            source=KeySource.ENVIRONMENT,
            local_degraded=False,
        ),
    )
    return (
        AstrBotHookBridge(
            SQLiteRepository(factory, codec=activation.codec),
            counter_provider=counter_provider or (lambda _profile: DistinctCanonicalCounter()),
        ),
        activation.codec,
    )


@pytest.mark.asyncio
async def test_authoritative_hooks_are_idempotent_and_allocate_contiguous_rows(
    tmp_path: Path,
) -> None:
    bridge, codec = _bridge(tmp_path)
    event = FakeEvent()
    request = _request()
    contexts = request.contexts
    contexts_before = tuple(contexts)

    profile = _budget_profile()
    first_prepare = await bridge.prepare_request(
        event,
        request,
        budget_profile=profile,
    )
    replay_prepare = await bridge.prepare_request(
        event,
        request,
        budget_profile=profile,
    )
    tool = SimpleNamespace(name="weather")
    first_call = await bridge.capture_tool_call(
        first_prepare,
        tool,
        {"city": "杭州"},
        ordinal=0,
    )
    replay_call = await bridge.capture_tool_call(
        replay_prepare,
        tool,
        {"city": "杭州"},
        ordinal=0,
    )
    first_result = await bridge.capture_tool_result(
        first_prepare,
        tool,
        {"city": "杭州"},
        {"temperature": 28},
        ordinal=0,
    )
    replay_result = await bridge.capture_tool_result(
        replay_prepare,
        tool,
        {"city": "杭州"},
        {"temperature": 28},
        ordinal=0,
    )
    first_assistant = await bridge.capture_assistant(first_prepare, "done")
    replay_assistant = await bridge.capture_assistant(replay_prepare, "done")
    first_job = await bridge.raise_compaction_intent(
        first_prepare,
        target_high_water_mark=first_assistant.sequence,
    )
    replay_job = await bridge.raise_compaction_intent(
        replay_prepare,
        target_high_water_mark=replay_assistant.sequence,
    )

    assert first_prepare.user_event == replay_prepare.user_event
    assert first_prepare.budget_profile is profile
    assert first_prepare.user_event.token_count == len(request.prompt.encode("utf-8"))
    assert bridge.repository.read_event_token_counts(
        first_prepare.turn.session_key,
        (first_prepare.user_event.event_id,),
        profile_id=CANONICAL_O200K.profile_id,
    ) == {first_prepare.user_event.event_id: DistinctCanonicalCounter().count_text(request.prompt)}
    assert first_prepare.user_event.token_count != DistinctCanonicalCounter().count_text(
        request.prompt
    )
    assert first_call == replay_call
    assert first_result == replay_result
    assert first_assistant == replay_assistant
    assert first_job == replay_job
    assert request.contexts is contexts
    assert tuple(request.contexts) == contexts_before
    assert first_prepare.view.high_water_mark == 1
    assert [item.event_id for item in first_prepare.view.delta] == [
        first_prepare.user_event.event_id
    ]
    assert first_prepare.candidates == ()

    with bridge.repository.factory.connection(read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT event_id, sequence, event_type, role, source_hook, content
            FROM journal_events
            ORDER BY sequence
            """
        ).fetchall()
        assert [tuple(row)[1:5] for row in rows] == [
            (1, "USER_MESSAGE", "USER", "ON_LLM_REQUEST"),
            (2, "TOOL_CALL", "TOOL", "ON_USING_LLM_TOOL"),
            (3, "TOOL_RESULT", "TOOL", "ON_LLM_TOOL_RESPOND"),
            (4, "ASSISTANT_MESSAGE", "ASSISTANT", "ON_AGENT_DONE"),
        ]
        contents = [
            codec.decrypt_text(
                "journal_events",
                "content",
                row["event_id"],
                row["content"],
            )
            for row in rows
        ]
        assert contents[0] == "hello from the user"
        assert json.loads(contents[1])["kind"] == "call"
        assert json.loads(contents[2])["kind"] == "result"
        assert contents[3] == "done"
        assert connection.execute("SELECT next_event_sequence FROM sessions").fetchone()[0] == 5
        assert (
            connection.execute("SELECT count(*) FROM token_metric_backfill_intents").fetchone()[0]
            == 0
        )
        assert connection.execute("SELECT count(*) FROM token_metrics").fetchone()[0] == 4
        job_rows = connection.execute(
            """
            SELECT state, target_high_water_mark, intent_target_high_water_mark
            FROM compaction_jobs
            """
        ).fetchall()
        assert [tuple(row) for row in job_rows] == [("PENDING", 4, 4)]


@pytest.mark.asyncio
async def test_missing_stable_identity_skips_every_durable_write(tmp_path: Path) -> None:
    bridge, _ = _bridge(tmp_path)
    request = _request()
    contexts_before = tuple(request.contexts)

    with pytest.raises(AstrBotAdapterError) as caught:
        await bridge.prepare_request(
            FakeEvent(sender_id=""),
            request,
            budget_profile=_budget_profile(),
        )

    assert caught.value.code == AdapterErrorCode.HOST_IDENTITY_MISSING.value
    assert tuple(request.contexts) == contexts_before
    with bridge.repository.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM compaction_jobs").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_canonical_asset_failure_keeps_byte_event_and_one_backfill_intent(
    tmp_path: Path,
) -> None:
    def fail_counter(_profile: TokenizerProfile) -> TokenCounter:
        raise RuntimeError("private tokenizer asset path")

    bridge, _ = _bridge(tmp_path, counter_provider=fail_counter)
    request = _request()

    prepared = await bridge.prepare_request(
        FakeEvent(),
        request,
        budget_profile=_budget_profile(),
    )

    assert prepared.user_event.token_count == len(request.prompt.encode("utf-8"))
    assert (
        bridge.repository.read_event_token_counts(
            prepared.turn.session_key,
            (prepared.user_event.event_id,),
            profile_id=CANONICAL_O200K.profile_id,
        )
        == {}
    )
    with bridge.repository.factory.connection(read_only=True) as connection:
        assert (
            connection.execute(
                """
                SELECT count(*)
                FROM token_metric_backfill_intents
                WHERE artifact_kind = 'EVENT'
                  AND artifact_id = ?
                  AND tokenizer_profile_id = ?
                """,
                (
                    prepared.user_event.event_id,
                    CANONICAL_O200K.profile_id,
                ),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.asyncio
async def test_mismatched_canonical_counter_profile_creates_one_backfill_intent(
    tmp_path: Path,
) -> None:
    class WrongProfileCounter:
        profile = BYTE_FALLBACK

        def __init__(self) -> None:
            self.calls = 0

        def count_text(self, _text: str) -> int:
            self.calls += 1
            return 1

    wrong = WrongProfileCounter()
    bridge, _ = _bridge(
        tmp_path,
        counter_provider=lambda _profile: wrong,
    )
    request = _request()

    prepared = await bridge.prepare_request(
        FakeEvent(),
        request,
        budget_profile=_budget_profile(),
    )

    assert prepared.user_event.token_count == len(request.prompt.encode("utf-8"))
    assert wrong.calls == 0
    assert (
        bridge.repository.read_event_token_counts(
            prepared.turn.session_key,
            (prepared.user_event.event_id,),
            profile_id=CANONICAL_O200K.profile_id,
        )
        == {}
    )
    with bridge.repository.factory.connection(read_only=True) as connection:
        assert (
            connection.execute(
                """
                SELECT count(*)
                FROM token_metric_backfill_intents
                WHERE artifact_kind = 'EVENT'
                  AND artifact_id = ?
                  AND tokenizer_profile_id = ?
                """,
                (
                    prepared.user_event.event_id,
                    CANONICAL_O200K.profile_id,
                ),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.asyncio
async def test_unprofiled_canonical_counter_creates_one_backfill_intent(
    tmp_path: Path,
) -> None:
    class UnprofiledByteCounter:
        def __init__(self) -> None:
            self.calls = 0

        def count_text(self, text: str) -> int:
            self.calls += 1
            return len(text.encode("utf-8"))

    unprofiled = UnprofiledByteCounter()
    bridge, _ = _bridge(
        tmp_path,
        counter_provider=lambda _profile: unprofiled,
    )
    request = _request()

    prepared = await bridge.prepare_request(
        FakeEvent(),
        request,
        budget_profile=_budget_profile(),
    )

    assert prepared.user_event.token_count == len(request.prompt.encode("utf-8"))
    assert unprofiled.calls == 0
    assert (
        bridge.repository.read_event_token_counts(
            prepared.turn.session_key,
            (prepared.user_event.event_id,),
            profile_id=CANONICAL_O200K.profile_id,
        )
        == {}
    )
    with bridge.repository.factory.connection(read_only=True) as connection:
        assert (
            connection.execute(
                """
                SELECT count(*)
                FROM token_metric_backfill_intents
                WHERE artifact_kind = 'EVENT'
                  AND artifact_id = ?
                  AND tokenizer_profile_id = ?
                """,
                (
                    prepared.user_event.event_id,
                    CANONICAL_O200K.profile_id,
                ),
            ).fetchone()[0]
            == 1
        )
