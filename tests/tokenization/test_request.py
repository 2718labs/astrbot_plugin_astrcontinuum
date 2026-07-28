from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace

import pytest

from astrcontinuum import EventType, RequestView, SessionKey, ports, runtime, tokenization


class RecordingCounter:
    def __init__(
        self,
        profile: tokenization.TokenizerProfile,
        *,
        fail_on_call: int | None = None,
        offset: int = 0,
    ) -> None:
        self.profile = profile
        self.fail_on_call = fail_on_call
        self.offset = offset
        self.calls: list[str] = []

    def count_text(self, text: str) -> int:
        self.calls.append(text)
        if len(self.calls) == self.fail_on_call:
            raise tokenization.TokenizerError(
                tokenization.TokenizerErrorCode.TOKENIZER_COUNT_FAILED
            )
        return len(text.encode("utf-8")) + self.offset


class BarrierCounter(RecordingCounter):
    def __init__(
        self,
        profile: tokenization.TokenizerProfile,
        barrier: threading.Barrier,
        *,
        offset: int,
        fail_on_call: int | None = None,
    ) -> None:
        super().__init__(profile, offset=offset, fail_on_call=fail_on_call)
        self.barrier = barrier
        self.waited = False

    def count_text(self, text: str) -> int:
        if not self.waited:
            self.waited = True
            self.barrier.wait(timeout=2)
        return super().count_text(text)


def empty_view() -> RequestView:
    return RequestView(
        session_key=SessionKey(
            platform_instance_id="astrbot-local",
            message_type="friend_message",
            session_id="session-request-budget",
            group_id=None,
            user_id="user-request-budget",
            conversation_id="conversation-request-budget",
            persona_id=None,
        ),
        snapshot=None,
        memberships=(),
        pointer_version=0,
        covered_event_end=0,
        high_water_mark=0,
        delta=(),
    )


def candidate(block_id: str, text: str = "candidate") -> runtime.CandidateBlock:
    return runtime.CandidateBlock(
        block_id=block_id,
        slot=runtime.RuntimeSlot.RAW_DELTA,
        kind=runtime.CandidateKind.RAW_EVENT,
        text=text,
        source_event_ids=(f"source-{block_id}",),
        score=10.0,
        reason="REQUEST_BUDGET_TEST",
        required=True,
        capsule_id=None,
        event_sequence=1,
        event_type=EventType.USER_MESSAGE,
    )


def workload(
    *candidates: runtime.CandidateBlock,
    tokenizer_profile: tokenization.TokenizerProfile = tokenization.OPENAI_O200K,
    host_text: str | None = None,
    compaction_start_ratio: float = 0.75,
    provider_view_switch_ratio: float = 0.80,
    stable_code: str = "NONE",
) -> tokenization.RequestBudgetWorkload:
    secret = "PRIVATE_REQUEST_PAYLOAD"
    return tokenization.RequestBudgetWorkload(
        prepared=tokenization.PreparedBudgetInput(
            current_input="current",
            view=empty_view(),
            candidates=tuple(candidates),
            trusted_token_usage=None,
        ),
        host=tokenization.HostBudgetView(
            all_host_texts=(host_text if host_text is not None else secret * 5,),
            opaque_texts=("opaque",),
            fixed_required_texts=("fixed",),
        ),
        profile=tokenization.RequestBudgetProfile(
            model_identity="test-model",
            context_limit=128,
            context_limit_source=tokenization.ContextLimitSource.MANUAL,
            tokenizer_profile=tokenizer_profile,
            target_input_budget=128,
            hard_input_ceiling=128,
            reserved_output_and_tools=0,
            safety_margin=0,
            compaction_start_ratio=compaction_start_ratio,
            provider_view_switch_ratio=provider_view_switch_ratio,
            stable_code=stable_code,
        ),
    )


def test_request_counter_cache_is_request_local_and_repr_is_content_free() -> None:
    secret = "PRIVATE_COUNTER_PAYLOAD"
    primary = RecordingCounter(tokenization.OPENAI_O200K)
    first = tokenization.RequestScopedTokenCounter(primary)
    second = tokenization.RequestScopedTokenCounter(primary)

    assert first.count_block("block-1", secret) == len(secret)
    assert first.count_block("block-1", secret) == len(secret)
    assert second.count_block("block-1", secret) == len(secret)
    assert primary.calls == [secret, secret]
    assert set(first._cache) == {  # type: ignore[attr-defined]
        ("block-1", tokenization.OPENAI_O200K.profile_id)
    }
    assert secret not in repr(first)
    with pytest.raises(tokenization.RequestBudgetInvariantError) as captured:
        first.count_block("block-1", "different")
    assert captured.value.code == "BLOCK_ID_TEXT_CONFLICT"
    assert primary.calls == [secret, secret]

    request = workload(candidate("candidate-1", secret))
    assert secret not in repr(request)
    assert secret not in repr(request.host)
    assert secret not in repr(request.prepared)
    with pytest.raises(FrozenInstanceError):
        request.prepared.current_input = "mutated"  # type: ignore[misc]


def test_generic_text_cache_cannot_collide_with_a_real_block_id() -> None:
    secret = "PRIVATE_SYNTHETIC_NAMESPACE_PAYLOAD"
    synthetic_id = f"text:{hashlib.sha256(secret.encode('utf-8')).hexdigest()}"
    primary = RecordingCounter(tokenization.OPENAI_O200K)
    scoped = tokenization.RequestScopedTokenCounter(primary)

    scoped.count_block(synthetic_id, "real candidate")

    assert scoped.count_text(secret) == len(secret)
    assert primary.calls == ["real candidate", secret]


def test_duplicate_block_id_is_a_hard_conflict_before_any_count_or_fallback() -> None:
    primary = RecordingCounter(tokenization.OPENAI_O200K)
    fallback = RecordingCounter(tokenization.BYTE_FALLBACK)

    with pytest.raises(tokenization.RequestBudgetInvariantError) as captured:
        tokenization.run_request_budget(
            workload(candidate("duplicate"), candidate("duplicate")),
            primary=primary,
            fallback=fallback,
        )

    assert captured.value.code == "DUPLICATE_BLOCK_ID"
    assert primary.calls == []
    assert fallback.calls == []


def test_primary_count_failure_discards_the_run_and_restarts_with_byte_fallback() -> None:
    primary = RecordingCounter(
        tokenization.OPENAI_O200K,
        fail_on_call=5,
        offset=1_000,
    )
    fallback = RecordingCounter(tokenization.BYTE_FALLBACK)
    request = workload(candidate("candidate-1"), stable_code="ROUTE_STABLE")

    outcome = tokenization.run_request_budget(
        request,
        primary=primary,
        fallback=fallback,
    )

    assert len(primary.calls) == 5
    assert outcome.tokenizer_profile_id == tokenization.BYTE_FALLBACK.profile_id
    assert outcome.profile.profile_id == tokenization.BYTE_FALLBACK.profile_id
    assert outcome.tokenizer_mode == tokenization.TokenizerMode.BYTE_FALLBACK.value
    assert outcome.fallback_code == "TOKENIZER_BYTE_FALLBACK"
    assert outcome.stable_code == "ROUTE_STABLE"
    assert outcome.primary_result_discarded is True
    assert outcome.assembly is not None
    assert "PRIVATE_REQUEST_PAYLOAD" not in repr(outcome)
    assert all(selection.token_cost < 1_000 for selection in outcome.assembly.trace.selections)


def test_request_profile_ratios_drive_pressure_thresholds() -> None:
    request = workload(
        candidate("candidate-1"),
        host_text="h" * 50,
        compaction_start_ratio=0.10,
        provider_view_switch_ratio=0.90,
    )

    outcome = tokenization.run_request_budget(
        request,
        primary=RecordingCounter(tokenization.OPENAI_O200K),
        fallback=RecordingCounter(tokenization.BYTE_FALLBACK),
    )

    assert outcome.pressure.compact_at == 13
    assert outcome.pressure.project_at == 116
    assert outcome.pressure.should_compact is True
    assert outcome.pressure.should_project is False
    assert outcome.assembly is None
    assert outcome.mutation_allowed is False


def test_primary_fallback_does_not_change_a_concurrent_request_profile() -> None:
    barrier = threading.Barrier(2)
    o200k = BarrierCounter(
        tokenization.OPENAI_O200K,
        barrier,
        offset=1_000,
        fail_on_call=4,
    )
    cl100k = BarrierCounter(tokenization.OPENAI_CL100K, barrier, offset=10)
    fallback_o200k = RecordingCounter(tokenization.BYTE_FALLBACK)
    fallback_cl100k = RecordingCounter(tokenization.BYTE_FALLBACK)

    def run(
        profile: tokenization.TokenizerProfile,
        primary: RecordingCounter,
        fallback: RecordingCounter,
    ) -> tokenization.RequestBudgetOutcome:
        return tokenization.run_request_budget(
            workload(candidate("candidate-1"), tokenizer_profile=profile),
            primary=primary,
            fallback=fallback,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            run,
            tokenization.OPENAI_O200K,
            o200k,
            fallback_o200k,
        )
        second = executor.submit(
            run,
            tokenization.OPENAI_CL100K,
            cl100k,
            fallback_cl100k,
        )
        outcomes = (first.result(timeout=3), second.result(timeout=3))

    assert tuple(outcome.tokenizer_profile_id for outcome in outcomes) == (
        tokenization.BYTE_FALLBACK.profile_id,
        tokenization.OPENAI_CL100K.profile_id,
    )
    assert outcomes[0].fallback_code == "TOKENIZER_BYTE_FALLBACK"
    assert outcomes[1].fallback_code == "NONE"
    assert fallback_o200k.calls
    assert fallback_cl100k.calls == []
    assert outcomes[0].assembly is not None
    assert outcomes[1].assembly is not None
    assert outcomes[0].assembly.trace.selections[0].token_cost < 10
    assert outcomes[1].assembly.trace.selections[0].token_cost >= 10


def test_required_input_overflow_keeps_its_code_and_does_not_fallback() -> None:
    request = workload(candidate("candidate-1"))
    request = replace(
        request,
        profile=replace(
            request.profile,
            context_limit=20,
            target_input_budget=20,
            hard_input_ceiling=20,
        ),
    )
    primary = RecordingCounter(tokenization.OPENAI_O200K)
    fallback = RecordingCounter(tokenization.BYTE_FALLBACK)

    outcome = tokenization.run_request_budget(
        request,
        primary=primary,
        fallback=fallback,
    )

    assert outcome.stable_code == "REQUIRED_INPUT_EXCEEDS_BUDGET"
    assert outcome.mutation_allowed is False
    assert outcome.assembly is None
    assert outcome.fallback_code == "NONE"
    assert outcome.primary_result_discarded is False
    assert outcome.tokenizer_profile_id == tokenization.OPENAI_O200K.profile_id
    assert "candidate" in primary.calls
    assert fallback.calls == []


def test_fallback_failure_is_not_recursively_retried() -> None:
    primary = RecordingCounter(tokenization.OPENAI_O200K, fail_on_call=1)
    fallback = RecordingCounter(tokenization.BYTE_FALLBACK, fail_on_call=1)

    with pytest.raises(tokenization.TokenizerError) as captured:
        tokenization.run_request_budget(
            workload(candidate("candidate-1")),
            primary=primary,
            fallback=fallback,
        )

    assert captured.value.code is tokenization.TokenizerErrorCode.TOKENIZER_COUNT_FAILED
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


def test_canonical_runtime_counter_aliases_remain_identical() -> None:
    import astrcontinuum as ac

    assert ports.TokenCounter is runtime.TokenCounter
    assert ac.RuntimeTokenCounter is runtime.TokenCounter
    assert ac.RuntimeUtf8ByteTokenCounter is runtime.Utf8ByteTokenCounter


@pytest.mark.parametrize(
    "factory",
    [
        lambda: tokenization.HostBudgetView(
            all_host_texts=["invalid"],  # type: ignore[arg-type]
            opaque_texts=(),
            fixed_required_texts=(),
        ),
        lambda: tokenization.PreparedBudgetInput(
            current_input=object(),  # type: ignore[arg-type]
            view=empty_view(),
            candidates=(),
            trusted_token_usage=None,
        ),
        lambda: tokenization.PreparedBudgetInput(
            current_input="current",
            view=empty_view(),
            candidates=(),
            trusted_token_usage=True,  # type: ignore[arg-type]
        ),
        lambda: tokenization.RequestBudgetWorkload(
            prepared=object(),  # type: ignore[arg-type]
            host=tokenization.HostBudgetView((), (), ()),
            profile=tokenization.RequestBudgetProfile(
                model_identity=None,
                context_limit=128,
                context_limit_source=tokenization.ContextLimitSource.MANUAL,
                tokenizer_profile=tokenization.OPENAI_O200K,
                target_input_budget=128,
                hard_input_ceiling=128,
            ),
        ),
        lambda: tokenization.RequestBudgetProfile(
            model_identity=None,
            context_limit=128,
            context_limit_source=tokenization.ContextLimitSource.MANUAL,
            tokenizer_profile=tokenization.OPENAI_O200K,
            target_input_budget=128,
            hard_input_ceiling=128,
            compaction_start_ratio=0.90,
            provider_view_switch_ratio=0.80,
        ),
    ],
)
def test_invalid_workload_fields_are_rejected_before_orchestration(
    factory: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()  # type: ignore[operator]
