from __future__ import annotations

from dataclasses import replace

import pytest

import astrcontinuum.context_graph.live as live_module
from astrcontinuum.context_graph import (
    ContextEngineMode,
    EngineOutcome,
    EngineResult,
    ErrorCertificate,
)
from astrcontinuum.context_graph.live import select_live_context
from astrcontinuum.domain import SessionKey
from astrcontinuum.runtime import BudgetConfig, Utf8ByteTokenCounter, assemble
from astrcontinuum.storage import RequestView
from astrcontinuum.tokenization import TokenizerError, TokenizerErrorCode
from tests.context_graph.helpers import candidate


def request_view() -> RequestView:
    return RequestView(
        session_key=SessionKey(
            platform_instance_id="platform",
            message_type="friend",
            session_id="session",
            group_id=None,
            user_id="user",
            conversation_id="conversation",
            persona_id=None,
        ),
        snapshot=None,
        memberships=(),
        pointer_version=0,
        covered_event_end=0,
        high_water_mark=0,
        delta=(),
    )


def budget() -> BudgetConfig:
    return BudgetConfig(
        target_input_budget=120,
        hard_input_ceiling=120,
        model_context_limit=120,
        reserved_output_and_tools=0,
        safety_margin=0,
    )


def verified_result(blocks: tuple[object, ...]) -> EngineResult:
    return EngineResult(
        outcome=EngineOutcome.ACTIVE,
        selected_blocks=blocks,  # type: ignore[arg-type]
        certificate=ErrorCertificate(
            stationarity_error=0.0,
            constraint_error=0.0,
            reconstruction_error=0.0,
            required_blocks_passed=True,
            provenance_passed=True,
            passed=True,
        ),
        solve_status="CONVERGED",
        backend_name="test",
    )


class FixedEngine:
    def __init__(self, selected: tuple[object, ...]) -> None:
        self.selected = selected
        self.activations = []

    def solve(self, _graph: object, activation: object, *, mode: object) -> EngineResult:
        self.activations.append(activation)
        result = verified_result(self.selected)
        if mode is ContextEngineMode.SHADOW:
            return replace(result, outcome=EngineOutcome.SHADOW, selected_blocks=())
        return result


def test_active_replaces_fallback_only_after_verified_final_pack() -> None:
    required = candidate("required", required=True, text="required")
    relevant = candidate("relevant", text="alpha")
    irrelevant = replace(candidate("irrelevant", text="zzzzz"), score=100.0)
    candidates = (required, relevant, irrelevant)
    view = request_view()
    counter = Utf8ByteTokenCounter()
    deterministic = assemble(
        view,
        candidates,
        current_input="alpha",
        opaque_token_cost=0,
        fixed_required_cost=0,
        counter=counter,
        config=budget(),
    )

    selected = select_live_context(
        view,
        candidates,
        current_input="alpha",
        opaque_token_cost=0,
        fixed_required_cost=0,
        counter=counter,
        budget_config=budget(),
        mode=ContextEngineMode.ACTIVE,
        engine=FixedEngine((required, relevant)),
    )

    assert selected.trace.outcome is EngineOutcome.ACTIVE
    assert selected.assembly != deterministic
    assert tuple(item.block_id for item in selected.assembly.selected_blocks) == (
        "required",
        "relevant",
    )
    assert selected.trace.required_passed is True
    assert selected.trace.provenance_passed is True


def test_off_mode_canonicalizes_duplicate_ids_before_counting_and_fallback() -> None:
    class RecordingCounter:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def count_text(self, text: str) -> int:
            self.texts.append(text)
            return len(text)

    canonical = candidate("duplicate", text="a")
    duplicate = candidate("duplicate", text="z")
    counter = RecordingCounter()

    selected = select_live_context(
        request_view(),
        (duplicate, canonical),
        current_input="",
        opaque_token_cost=0,
        fixed_required_cost=0,
        counter=counter,
        budget_config=budget(),
        mode=ContextEngineMode.OFF,
        engine=FixedEngine(()),
    )

    assert selected.trace.outcome is EngineOutcome.OFF
    assert selected.trace.candidate_count == 1
    assert selected.assembly.projected_text == canonical.text
    assert duplicate.text not in counter.texts


def test_validated_block_counts_are_reused_without_counting_candidates_again() -> None:
    class RecordingCounter:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def count_text(self, text: str) -> int:
            self.texts.append(text)
            return len(text)

    alpha = candidate("alpha", text="alpha")
    beta = candidate("beta", text="beta")
    counter = RecordingCounter()

    selected = select_live_context(
        request_view(),
        (alpha, beta),
        current_input="",
        opaque_token_cost=0,
        fixed_required_cost=0,
        counter=counter,
        budget_config=budget(),
        block_token_counts={"alpha": 5, "beta": 4},
        mode=ContextEngineMode.OFF,
        engine=FixedEngine(()),
    )

    assert selected.trace.outcome is EngineOutcome.OFF
    assert alpha.text not in counter.texts
    assert beta.text not in counter.texts
    assert "alpha\n\nbeta" in counter.texts


def test_off_shadow_and_fault_return_the_exact_deterministic_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    required = candidate("required", required=True, text="required")
    optional = candidate("optional", text="alpha")
    candidates = (required, optional)
    view = request_view()
    counter = Utf8ByteTokenCounter()
    expected = assemble(
        view,
        candidates,
        current_input="alpha",
        opaque_token_cost=0,
        fixed_required_cost=0,
        counter=counter,
        config=budget(),
    )
    observed_views: list[RequestView] = []
    original_builder = live_module.build_context_graph

    def same_view_builder(
        actual_view: RequestView,
        *args: object,
        **kwargs: object,
    ):
        observed_views.append(actual_view)
        return original_builder(actual_view, *args, **kwargs)

    monkeypatch.setattr(live_module, "build_context_graph", same_view_builder)
    off = select_live_context(
        view,
        candidates,
        current_input="alpha",
        opaque_token_cost=0,
        fixed_required_cost=0,
        counter=counter,
        budget_config=budget(),
        mode=ContextEngineMode.OFF,
        engine=FixedEngine((required, optional)),
    )
    shadow = select_live_context(
        view,
        candidates,
        current_input="alpha",
        opaque_token_cost=0,
        fixed_required_cost=0,
        counter=counter,
        budget_config=budget(),
        mode=ContextEngineMode.SHADOW,
        engine=FixedEngine((required, optional)),
    )

    class FaultingEngine:
        def solve(self, *_args: object, **_kwargs: object) -> EngineResult:
            raise RuntimeError("private solver failure")

    fault = select_live_context(
        view,
        candidates,
        current_input="alpha",
        opaque_token_cost=0,
        fixed_required_cost=0,
        counter=counter,
        budget_config=budget(),
        mode=ContextEngineMode.ACTIVE,
        engine=FaultingEngine(),
    )

    assert off.assembly == expected
    assert shadow.assembly == expected
    assert fault.assembly == expected
    assert fault.assembly.projected_text.encode() == expected.projected_text.encode()
    assert off.trace.outcome is EngineOutcome.OFF
    assert shadow.trace.outcome is EngineOutcome.SHADOW
    assert fault.trace.outcome is EngineOutcome.DEGRADED_RAW
    assert fault.trace.error_code == "LIVE_ENGINE_FAILURE"
    assert observed_views == [view, view]


def test_adaptive_retry_only_adds_retained_coordinates() -> None:
    required = candidate("required", required=True, text="required")
    relevant = candidate("relevant", text="alpha")
    irrelevant = candidate("irrelevant", text="zzzzz")

    class RetryEngine(FixedEngine):
        def solve(self, _graph: object, activation: object, *, mode: object) -> EngineResult:
            self.activations.append(activation)
            if len(self.activations) == 1:
                return EngineResult(
                    outcome=EngineOutcome.DEGRADED_RAW,
                    error_code="REDUCTION_SOLVE_FAILED",
                )
            return verified_result((required, relevant))

    engine = RetryEngine((required, relevant))
    selected = select_live_context(
        request_view(),
        (required, relevant, irrelevant),
        current_input="alpha",
        opaque_token_cost=0,
        fixed_required_cost=0,
        counter=Utf8ByteTokenCounter(),
        budget_config=budget(),
        mode=ContextEngineMode.ACTIVE,
        engine=engine,
    )

    first = engine.activations[0]
    second = engine.activations[1]
    assert first.scores == second.scores
    assert first.provenance_event_ids == second.provenance_event_ids
    assert set(first.retained_coordinate_ids) < set(second.retained_coordinate_ids)
    assert selected.trace.adaptive_retry_count == 1
    assert selected.trace.outcome is EngineOutcome.ACTIVE


def test_incomplete_required_selection_forces_content_free_raw_fallback() -> None:
    incomplete = replace(
        candidate("required", required=True, text="required"),
        required_selection_complete=False,
    )
    view = request_view()
    counter = Utf8ByteTokenCounter()
    expected = assemble(
        view,
        (incomplete,),
        current_input="",
        opaque_token_cost=0,
        fixed_required_cost=0,
        counter=counter,
        config=budget(),
    )
    engine = FixedEngine((incomplete,))

    selected = select_live_context(
        view,
        (incomplete,),
        current_input="",
        opaque_token_cost=0,
        fixed_required_cost=0,
        counter=counter,
        budget_config=budget(),
        mode=ContextEngineMode.ACTIVE,
        engine=engine,
    )

    assert selected.assembly == expected
    assert selected.trace.outcome is EngineOutcome.DEGRADED_RAW
    assert selected.trace.error_code == "LIVE_REQUIRED_CANDIDATE_TRUNCATED"
    assert engine.activations == []
    assert not hasattr(selected.trace, "fallback_block_ids")
    assert not hasattr(selected.trace, "final_block_ids")


def test_final_validation_does_not_redact_or_degrade_tokenizer_failures() -> None:
    class ArmableCounter:
        def __init__(self) -> None:
            self.armed = False
            self.texts: list[str] = []

        def count_text(self, text: str) -> int:
            self.texts.append(text)
            if self.armed and text == "required\n\noptional":
                raise TokenizerError(TokenizerErrorCode.TOKENIZER_COUNT_FAILED)
            return len(text.encode("utf-8"))

    required = candidate("required", required=True, text="required")
    optional = candidate("optional", text="optional")
    counter = ArmableCounter()

    class ArmingEngine(FixedEngine):
        def solve(
            self,
            _graph: object,
            activation: object,
            *,
            mode: object,
        ) -> EngineResult:
            self.activations.append(activation)
            counter.armed = True
            return verified_result((required, optional))

    with pytest.raises(TokenizerError) as captured:
        select_live_context(
            request_view(),
            (required, optional),
            current_input="",
            opaque_token_cost=0,
            fixed_required_cost=0,
            counter=counter,
            budget_config=budget(),
            mode=ContextEngineMode.ACTIVE,
            engine=ArmingEngine((required, optional)),
        )

    assert captured.value.code is TokenizerErrorCode.TOKENIZER_COUNT_FAILED
    assert counter.texts.count(required.text) == 1
    assert counter.texts.count(optional.text) == 1
