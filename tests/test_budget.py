from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

import astrcontinuum as ac
from astrcontinuum import runtime
from astrcontinuum.assembler import ContextAssembler, ContextBlock
from astrcontinuum.models import ContextEvent
from astrcontinuum.token_budget import BudgetConfig as LegacyBudgetConfig


def test_legacy_assembler_still_respects_its_target_budget() -> None:
    assembler = ContextAssembler(
        LegacyBudgetConfig(
            target_input_tokens=100,
            hard_input_ceiling=120,
            recent_raw_tokens=50,
            retrieval_tokens=25,
            snapshot_tokens=20,
        )
    )
    event = ContextEvent("e1", "s", 1, "message", "user", "hello", 1.0)

    result = assembler.build(
        session_id="s",
        snapshot=None,
        delta=[event],
        current_input="current request",
        candidate_blocks=[
            ContextBlock("constraint", "must preserve", 20, 90, True),
            ContextBlock("recent", "recent raw", 60, 70),
            ContextBlock("background", "old", 50, 10),
        ],
    )

    assert result.trace.total_tokens <= 100
    assert "constraint" in result.trace.slot_tokens


def canonical_surface() -> tuple[Any, ...]:
    names = (
        "AssemblyMode",
        "AssemblyResult",
        "AssemblyTrace",
        "BlockRejection",
        "BudgetConfig",
        "BudgetInvariantError",
        "TokenCounter",
        "Utf8ByteTokenCounter",
        "assemble",
    )
    values = tuple(getattr(runtime, name, None) for name in names)
    assert all(value is not None for value in values), "canonical budget surface is missing"
    return values


class LengthCounter:
    def count_text(self, text: str) -> int:
        return len(text)


class InvalidCounter:
    def __init__(self, value: object) -> None:
        self.value = value

    def count_text(self, _text: str) -> object:
        return self.value


class ExplodingCounter:
    def count_text(self, _text: str) -> int:
        raise RuntimeError("PRIVATE COUNTER PAYLOAD")


def session_key() -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id="session-1",
        group_id=None,
        user_id="user-1",
        conversation_id="conversation-1",
        persona_id=None,
    )


def empty_view() -> ac.RequestView:
    return ac.RequestView(
        session_key=session_key(),
        snapshot=None,
        memberships=(),
        pointer_version=0,
        covered_event_end=0,
        high_water_mark=0,
        delta=(),
    )


def block(
    block_id: str,
    slot: runtime.RuntimeSlot,
    text: str,
    *,
    required: bool = False,
    score: float = 1.0,
    event_sequence: int | None = None,
    event_type: ac.EventType | None = None,
    tool_name: str | None = None,
) -> runtime.CandidateBlock:
    kind = (
        runtime.CandidateKind.RAW_EVENT
        if slot in (runtime.RuntimeSlot.RAW_DELTA, runtime.RuntimeSlot.RECENT_RAW)
        else runtime.CandidateKind.TASK_CONTEXT
    )
    return runtime.CandidateBlock(
        block_id=block_id,
        slot=slot,
        kind=kind,
        text=text,
        source_event_ids=(f"source-{block_id}",),
        score=score,
        reason="AC_OWNED_TEST_BLOCK",
        required=required,
        capsule_id=None if kind == runtime.CandidateKind.RAW_EVENT else "capsule-1",
        event_sequence=event_sequence,
        event_type=event_type,
        tool_name=tool_name,
    )


def call_assemble(
    candidates: tuple[runtime.CandidateBlock, ...],
    *,
    current_input: str = "",
    opaque_token_cost: int = 0,
    fixed_required_cost: int = 0,
    counter: Any | None = None,
    config: Any | None = None,
) -> Any:
    *_types, assemble = canonical_surface()
    budget_type = runtime.BudgetConfig
    return assemble(
        empty_view(),
        candidates,
        current_input=current_input,
        opaque_token_cost=opaque_token_cost,
        fixed_required_cost=fixed_required_cost,
        counter=counter or LengthCounter(),
        config=config
        or budget_type(
            target_input_budget=100,
            hard_input_ceiling=100,
            model_context_limit=120,
            reserved_output_and_tools=20,
            safety_margin=0,
        ),
    )


def test_exact_budget_arithmetic_and_projection_cost() -> None:
    canonical_surface()
    config = runtime.BudgetConfig(
        target_input_budget=100,
        hard_input_ceiling=90,
        model_context_limit=120,
        reserved_output_and_tools=20,
        safety_margin=5,
    )
    candidates = (
        block("task-a", runtime.RuntimeSlot.ACTIVE_TASK, "A" * 10),
        block("evidence-b", runtime.RuntimeSlot.RELEVANT_EVIDENCE, "B" * 20),
    )

    result = call_assemble(
        candidates,
        current_input="12345",
        opaque_token_cost=15,
        fixed_required_cost=10,
        config=config,
    )

    assert result.projected_text == f"{'A' * 10}\n\n{'B' * 20}"
    assert result.trace.b_input == 90
    assert result.trace.current_input_cost == 5
    assert result.trace.fixed_required_cost == 10
    assert result.trace.safety_margin == 5
    assert result.trace.b_required == 20
    assert result.trace.opaque_token_cost == 15
    assert result.trace.b_ac == 55
    assert result.trace.ac_selected_cost == 32
    assert result.trace.projection_overhead_cost == 2
    assert (
        sum(item.token_cost for item in result.trace.selections)
        + result.trace.projection_overhead_cost
        == result.trace.ac_selected_cost
    )
    assert result.trace.total_input_cost == 67
    assert result.trace.total_input_cost <= result.trace.b_input


def test_normal_mode_uses_frozen_priority_deduplicates_and_is_deterministic() -> None:
    canonical_surface()
    ordered = (
        block("goal", runtime.RuntimeSlot.ACTIVE_GOAL, "goal", required=True),
        block(
            "constraint",
            runtime.RuntimeSlot.HARD_CONSTRAINT,
            "constraint",
            required=True,
        ),
        block(
            "raw-1",
            runtime.RuntimeSlot.RAW_DELTA,
            "raw",
            required=True,
            event_sequence=1,
        ),
        block("anchor", runtime.RuntimeSlot.EXACT_ANCHOR, "anchor", required=True),
        block("task", runtime.RuntimeSlot.ACTIVE_TASK, "task"),
        block("evidence", runtime.RuntimeSlot.RELEVANT_EVIDENCE, "evidence"),
        block("background", runtime.RuntimeSlot.GLOBAL_BACKGROUND, "background"),
    )
    shuffled_with_duplicate = (
        ordered[-1],
        ordered[5],
        ordered[3],
        ordered[1],
        ordered[0],
        ordered[2],
        ordered[4],
        ordered[4],
    )

    first = call_assemble(shuffled_with_duplicate)
    second = call_assemble(tuple(reversed(shuffled_with_duplicate)))

    assert first.trace.mode == runtime.AssemblyMode.NORMAL
    assert tuple(item.block_id for item in first.selected_blocks) == tuple(
        item.block_id for item in ordered
    )
    assert first.projected_text == second.projected_text
    assert first.trace == second.trace
    assert sum(item.block_id == "task" for item in first.selected_blocks) == 1
    assert any(
        item.block_id == "task" and item.reason == "DUPLICATE_BLOCK_ID"
        for item in first.trace.rejections
    )


def test_emergency_uses_latest_raw_suffix_before_later_slots() -> None:
    canonical_surface()
    config = runtime.BudgetConfig(
        target_input_budget=18,
        hard_input_ceiling=18,
        model_context_limit=18,
        reserved_output_and_tools=0,
        safety_margin=0,
    )
    candidates = (
        block("goal", runtime.RuntimeSlot.ACTIVE_GOAL, "GGGG", required=True),
        block(
            "raw-1",
            runtime.RuntimeSlot.RAW_DELTA,
            "1111",
            required=True,
            event_sequence=1,
        ),
        block(
            "raw-2",
            runtime.RuntimeSlot.RAW_DELTA,
            "2222",
            required=True,
            event_sequence=2,
        ),
        block(
            "raw-3",
            runtime.RuntimeSlot.RAW_DELTA,
            "3333",
            required=True,
            event_sequence=3,
        ),
        block("anchor", runtime.RuntimeSlot.EXACT_ANCHOR, "AAAA", required=True),
        block("optional", runtime.RuntimeSlot.ACTIVE_TASK, "optional"),
    )

    result = call_assemble(candidates, config=config)

    assert result.trace.mode == runtime.AssemblyMode.EMERGENCY_ASSEMBLY
    assert tuple(item.block_id for item in result.selected_blocks) == (
        "goal",
        "raw-2",
        "raw-3",
    )
    assert result.projected_text == "GGGG\n\n2222\n\n3333"
    assert result.trace.ac_selected_cost == 16
    rejected = {item.block_id: item.reason for item in result.trace.rejections}
    assert rejected["raw-1"] == "EMERGENCY_RAW_PREFIX_OMITTED"
    assert rejected["anchor"] == "EMERGENCY_REQUIRED_NOT_FIT"
    assert rejected["optional"] == "EMERGENCY_OPTIONAL_OMITTED"
    assert result.trace.total_input_cost <= result.trace.b_input


def test_emergency_follows_frozen_slot_priority_before_exact_anchor() -> None:
    canonical_surface()
    config = runtime.BudgetConfig(
        target_input_budget=10,
        hard_input_ceiling=10,
        model_context_limit=10,
        reserved_output_and_tools=0,
        safety_margin=0,
    )
    candidates = (
        block("goal", runtime.RuntimeSlot.ACTIVE_GOAL, "GGGG", required=True),
        block(
            "raw-1",
            runtime.RuntimeSlot.RAW_DELTA,
            "RRRR",
            required=True,
            event_sequence=1,
        ),
        block("anchor", runtime.RuntimeSlot.EXACT_ANCHOR, "AAAA", required=True),
    )

    result = call_assemble(candidates, config=config)

    assert result.trace.mode == runtime.AssemblyMode.EMERGENCY_ASSEMBLY
    assert tuple(item.block_id for item in result.selected_blocks) == (
        "goal",
        "raw-1",
    )
    assert result.projected_text == "GGGG\n\nRRRR"
    assert result.trace.ac_selected_cost == 10
    assert any(
        item.block_id == "anchor" and item.reason == "EMERGENCY_REQUIRED_NOT_FIT"
        for item in result.trace.rejections
    )


def test_emergency_budget_never_selects_tool_result_without_its_call() -> None:
    config = runtime.BudgetConfig(
        target_input_budget=1,
        hard_input_ceiling=1,
        model_context_limit=1,
        reserved_output_and_tools=0,
        safety_margin=0,
    )
    candidates = (
        block(
            "tool-call",
            runtime.RuntimeSlot.RAW_DELTA,
            "CALL",
            required=True,
            event_sequence=1,
            event_type=ac.EventType.TOOL_CALL,
            tool_name="weather",
        ),
        block(
            "tool-result",
            runtime.RuntimeSlot.RAW_DELTA,
            "R",
            required=True,
            event_sequence=2,
            event_type=ac.EventType.TOOL_RESULT,
            tool_name="weather",
        ),
    )

    result = call_assemble(candidates, config=config)

    assert result.trace.mode is runtime.AssemblyMode.EMERGENCY_ASSEMBLY
    assert result.selected_blocks == ()
    assert {item.block_id for item in result.trace.rejections} == {
        "tool-call",
        "tool-result",
    }


@pytest.mark.parametrize("event_type", (ac.EventType.TOOL_CALL, ac.EventType.TOOL_RESULT))
def test_emergency_budget_omits_unmatched_tool_event(event_type: ac.EventType) -> None:
    config = runtime.BudgetConfig(
        target_input_budget=1,
        hard_input_ceiling=1,
        model_context_limit=1,
        reserved_output_and_tools=0,
        safety_margin=0,
    )
    kind = "call" if event_type is ac.EventType.TOOL_CALL else "result"
    candidates = (
        block("goal", runtime.RuntimeSlot.ACTIVE_GOAL, "GG", required=True),
        block(
            f"tool-{kind}",
            runtime.RuntimeSlot.RAW_DELTA,
            "T",
            required=True,
            event_sequence=1,
            event_type=event_type,
            tool_name="weather",
        ),
    )

    result = call_assemble(candidates, config=config)

    assert result.trace.mode is runtime.AssemblyMode.EMERGENCY_ASSEMBLY
    assert result.selected_blocks == ()


def test_zero_ac_budget_and_unfittable_required_block_are_explicit() -> None:
    canonical_surface()
    config = runtime.BudgetConfig(
        target_input_budget=20,
        hard_input_ceiling=20,
        model_context_limit=20,
        reserved_output_and_tools=0,
        safety_margin=5,
    )

    result = call_assemble(
        (block("goal", runtime.RuntimeSlot.ACTIVE_GOAL, "goal", required=True),),
        current_input="12345",
        opaque_token_cost=5,
        fixed_required_cost=5,
        config=config,
    )

    assert result.trace.b_required == 15
    assert result.trace.b_ac == 0
    assert result.trace.mode == runtime.AssemblyMode.EMERGENCY_ASSEMBLY
    assert result.selected_blocks == ()
    assert result.trace.rejections[0].reason == "EMERGENCY_REQUIRED_NOT_FIT"
    assert result.trace.total_input_cost == result.trace.b_input == 20


@pytest.mark.parametrize(
    ("kwargs", "expected_code"),
    [
        ({"opaque_token_cost": -1}, "OPAQUE_TOKEN_COST_INVALID"),
        ({"opaque_token_cost": True}, "OPAQUE_TOKEN_COST_INVALID"),
        ({"fixed_required_cost": -1}, "FIXED_REQUIRED_COST_INVALID"),
        ({"counter": InvalidCounter(-1)}, "TOKEN_COUNTER_INVALID"),
        ({"counter": InvalidCounter("not-an-int")}, "TOKEN_COUNTER_INVALID"),
        ({"counter": ExplodingCounter()}, "TOKEN_COUNTER_FAILURE"),
    ],
)
def test_invalid_costs_and_counter_fail_with_content_free_codes(
    kwargs: dict[str, object],
    expected_code: str,
) -> None:
    canonical_surface()
    error_type = runtime.BudgetInvariantError

    with pytest.raises(error_type) as caught:
        call_assemble((), **kwargs)

    assert caught.value.code == expected_code
    assert str(caught.value) == expected_code
    assert "PRIVATE COUNTER PAYLOAD" not in str(caught.value)


def test_utf8_counter_blocks_chinese_and_emoji_len_div_four_undercount() -> None:
    canonical_surface()
    text = "你好🙂"
    counter = runtime.Utf8ByteTokenCounter()
    assert counter.count_text(text) == len(text.encode("utf-8"))
    assert counter.count_text(text) > len(text) // 4
    config = runtime.BudgetConfig(
        target_input_budget=9,
        hard_input_ceiling=9,
        model_context_limit=9,
        reserved_output_and_tools=0,
        safety_margin=0,
    )

    with pytest.raises(runtime.BudgetInvariantError) as caught:
        call_assemble((), current_input=text, counter=counter, config=config)

    assert caught.value.code == "REQUIRED_INPUT_EXCEEDS_BUDGET"


def test_trace_is_content_free_and_request_view_remains_immutable() -> None:
    canonical_surface()
    view = empty_view()
    candidate = block(
        "safe-id",
        runtime.RuntimeSlot.ACTIVE_TASK,
        "AC-OWNED-SECRET-TEXT",
    )
    before = (
        view.session_key,
        view.snapshot,
        view.memberships,
        view.pointer_version,
        view.covered_event_end,
        view.high_water_mark,
        view.delta,
    )

    result = runtime.assemble(
        view,
        (candidate,),
        current_input="PRIVATE-CURRENT-INPUT",
        opaque_token_cost=0,
        fixed_required_cost=0,
        counter=LengthCounter(),
        config=runtime.BudgetConfig(
            target_input_budget=100,
            hard_input_ceiling=100,
            model_context_limit=100,
            reserved_output_and_tools=0,
            safety_margin=0,
        ),
    )

    assert "AC-OWNED-SECRET-TEXT" in result.projected_text
    assert "AC-OWNED-SECRET-TEXT" not in repr(result.trace)
    assert "PRIVATE-CURRENT-INPUT" not in repr(result.trace)
    assert before == (
        view.session_key,
        view.snapshot,
        view.memberships,
        view.pointer_version,
        view.covered_event_end,
        view.high_water_mark,
        view.delta,
    )
    with pytest.raises(FrozenInstanceError):
        result.trace.b_ac = 0


def test_opaque_api_is_cost_only_and_top_level_legacy_trace_is_preserved() -> None:
    canonical_surface()
    parameters = inspect.signature(runtime.assemble).parameters

    assert "opaque_token_cost" in parameters
    assert all(
        forbidden not in parameters
        for forbidden in ("opaque_text", "opaque_id", "opaque_hash", "embedding")
    )
    assert ac.AssemblyTrace.__module__ == "astrcontinuum.models"
    assert ac.RuntimeAssemblyTrace is runtime.AssemblyTrace
    assert ac.RuntimeAssemblyResult is runtime.AssemblyResult
    assert ac.RuntimeBudgetConfig is runtime.BudgetConfig
