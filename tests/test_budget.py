from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, replace
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


def test_duplicate_rejection_uses_the_rejected_texts_own_token_cost() -> None:
    retained = block(
        "duplicate",
        runtime.RuntimeSlot.ACTIVE_TASK,
        "a",
    )
    rejected = replace(retained, text="long duplicate")

    result = call_assemble((rejected, retained))

    assert result.projected_text == "a"
    duplicate_rejection = next(
        item for item in result.trace.rejections if item.reason == "DUPLICATE_BLOCK_ID"
    )
    assert duplicate_rejection.token_cost == len(rejected.text)


def test_emergency_uses_latest_raw_suffix_before_later_slots() -> None:
    canonical_surface()
    config = runtime.BudgetConfig(
        target_input_budget=22,
        hard_input_ceiling=22,
        model_context_limit=22,
        reserved_output_and_tools=0,
        safety_margin=0,
    )
    candidates = (
        block("goal", runtime.RuntimeSlot.ACTIVE_GOAL, "GGGG", required=True),
        block(
            "raw-1",
            runtime.RuntimeSlot.RAW_DELTA,
            "11111111",
            event_sequence=1,
        ),
        block(
            "raw-2",
            runtime.RuntimeSlot.RAW_DELTA,
            "2222",
            event_sequence=2,
        ),
        block(
            "raw-3",
            runtime.RuntimeSlot.RAW_DELTA,
            "3333",
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
        "anchor",
    )
    assert result.projected_text == "GGGG\n\n2222\n\n3333\n\nAAAA"
    assert result.trace.ac_selected_cost == 22
    rejected = {item.block_id: item.reason for item in result.trace.rejections}
    assert rejected["raw-1"] == "EMERGENCY_RAW_PREFIX_OMITTED"
    assert rejected["optional"] == "EMERGENCY_OPTIONAL_OMITTED"
    assert result.trace.total_input_cost <= result.trace.b_input


def test_required_exact_anchor_overflow_is_explicit() -> None:
    canonical_surface()
    config = runtime.BudgetConfig(
        target_input_budget=9,
        hard_input_ceiling=9,
        model_context_limit=9,
        reserved_output_and_tools=0,
        safety_margin=0,
    )
    candidates = (
        block("goal", runtime.RuntimeSlot.ACTIVE_GOAL, "GGGG", required=True),
        block("anchor", runtime.RuntimeSlot.EXACT_ANCHOR, "AAAA", required=True),
    )

    with pytest.raises(runtime.BudgetInvariantError) as caught:
        call_assemble(candidates, config=config)

    assert caught.value.code == "REQUIRED_INPUT_EXCEEDS_BUDGET"


def test_required_tool_round_overflow_is_explicit_and_never_split() -> None:
    config = runtime.BudgetConfig(
        target_input_budget=6,
        hard_input_ceiling=6,
        model_context_limit=6,
        reserved_output_and_tools=0,
        safety_margin=0,
    )
    candidates = (
        replace(
            block(
                "tool-round",
                runtime.RuntimeSlot.RAW_DELTA,
                "CALL\nRESULT\nASSISTANT",
                required=True,
                event_sequence=1,
            ),
            source_event_ids=("call", "result", "assistant"),
        ),
    )

    with pytest.raises(runtime.BudgetInvariantError) as captured:
        call_assemble(candidates, config=config)

    assert captured.value.code == "REQUIRED_INPUT_EXCEEDS_BUDGET"


@pytest.mark.parametrize("event_type", (ac.EventType.TOOL_CALL, ac.EventType.TOOL_RESULT))
def test_emergency_budget_omits_unmatched_tool_event(event_type: ac.EventType) -> None:
    config = runtime.BudgetConfig(
        target_input_budget=4,
        hard_input_ceiling=4,
        model_context_limit=4,
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

    with pytest.raises(runtime.BudgetInvariantError) as captured:
        call_assemble(candidates, config=config)

    assert captured.value.code == "REQUIRED_INPUT_EXCEEDS_BUDGET"


def test_zero_ac_budget_and_unfittable_required_block_are_explicit() -> None:
    canonical_surface()
    config = runtime.BudgetConfig(
        target_input_budget=20,
        hard_input_ceiling=20,
        model_context_limit=20,
        reserved_output_and_tools=0,
        safety_margin=5,
    )

    with pytest.raises(runtime.BudgetInvariantError) as captured:
        call_assemble(
            (block("goal", runtime.RuntimeSlot.ACTIVE_GOAL, "goal", required=True),),
            current_input="12345",
            opaque_token_cost=5,
            fixed_required_cost=5,
            config=config,
        )

    assert captured.value.code == "REQUIRED_INPUT_EXCEEDS_BUDGET"


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


def test_candidate_counts_are_bounded_and_growing_prefixes_are_not_encoded() -> None:
    class RecordingCounter:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def count_text(self, text: str) -> int:
            self.texts.append(text)
            return len(text)

    candidates = tuple(
        block(
            f"candidate-{index:03d}",
            runtime.RuntimeSlot.ACTIVE_TASK,
            f"payload-{index:03d}",
            score=float(256 - index),
        )
        for index in range(256)
    )
    counter = RecordingCounter()
    result = call_assemble(
        candidates,
        counter=counter,
        config=runtime.BudgetConfig(
            target_input_budget=100_000,
            hard_input_ceiling=100_000,
            model_context_limit=100_000,
            reserved_output_and_tools=0,
            safety_margin=0,
        ),
    )

    assert all(counter.texts.count(candidate.text) == 1 for candidate in candidates)
    growing_prefixes = {
        "\n\n".join(candidate.text for candidate in candidates[:end])
        for end in range(2, len(candidates))
    }
    assert growing_prefixes.isdisjoint(counter.texts)
    assert result.projected_text in counter.texts


def test_exact_projection_cost_can_make_projection_overhead_negative() -> None:
    class MergingCounter:
        def count_text(self, text: str) -> int:
            costs = {
                "left": 10,
                "right": 10,
                "\n\n": 1,
                "left\n\nright": 5,
            }
            return costs.get(text, len(text))

    result = call_assemble(
        (
            block(
                "left",
                runtime.RuntimeSlot.ACTIVE_TASK,
                "left",
                score=2.0,
            ),
            block(
                "right",
                runtime.RuntimeSlot.ACTIVE_TASK,
                "right",
            ),
        ),
        counter=MergingCounter(),
        config=runtime.BudgetConfig(
            target_input_budget=30,
            hard_input_ceiling=30,
            model_context_limit=30,
            reserved_output_and_tools=0,
            safety_margin=0,
        ),
    )

    assert result.trace.ac_selected_cost == 5
    assert result.trace.projection_overhead_cost == -15
    assert (
        sum(item.token_cost for item in result.trace.selections)
        + result.trace.projection_overhead_cost
        == result.trace.ac_selected_cost
    )
    assert result.trace.total_input_cost <= result.trace.b_input


def test_emergency_raw_suffix_uses_one_block_map_before_required_overflow() -> None:
    class RecordingCounter:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def count_text(self, text: str) -> int:
            self.texts.append(text)
            return len(text)

    candidates = (
        block(
            "goal",
            runtime.RuntimeSlot.ACTIVE_GOAL,
            "goal",
            required=True,
        ),
        *(
            block(
                f"raw-{index:03d}",
                runtime.RuntimeSlot.RAW_DELTA,
                f"raw-{index:03d}",
                required=True,
                event_sequence=index + 1,
            )
            for index in range(255)
        ),
    )
    counter = RecordingCounter()
    with pytest.raises(runtime.BudgetInvariantError) as captured:
        call_assemble(
            candidates,
            counter=counter,
            config=runtime.BudgetConfig(
                target_input_budget=40,
                hard_input_ceiling=40,
                model_context_limit=40,
                reserved_output_and_tools=0,
                safety_margin=0,
            ),
        )

    assert captured.value.code == "REQUIRED_INPUT_EXCEEDS_BUDGET"
    assert all(counter.texts.count(candidate.text) == 1 for candidate in candidates)
    composite_counts = [text for text in counter.texts if text != "\n\n" and "\n\n" in text]
    assert composite_counts == []


def test_exact_overflow_removes_an_optional_parallel_tool_round_atomically() -> None:
    class ExpandingCounter:
        def count_text(self, text: str) -> int:
            costs = {
                "goal": 1,
                "CALL-A\nCALL-B\nRESULT-B\nRESULT-A\nASSISTANT": 1,
                "\n\n": 0,
                "goal\n\nCALL-A\nCALL-B\nRESULT-B\nRESULT-A\nASSISTANT": 10,
            }
            return costs.get(text, len(text))

    candidates = (
        block(
            "goal",
            runtime.RuntimeSlot.ACTIVE_GOAL,
            "goal",
            required=True,
        ),
        replace(
            block(
                "parallel-tool-round",
                runtime.RuntimeSlot.RAW_DELTA,
                "CALL-A\nCALL-B\nRESULT-B\nRESULT-A\nASSISTANT",
                event_sequence=1,
            ),
            source_event_ids=("call-a", "call-b", "result-b", "result-a", "assistant"),
        ),
    )

    result = call_assemble(
        candidates,
        counter=ExpandingCounter(),
        config=runtime.BudgetConfig(
            target_input_budget=3,
            hard_input_ceiling=3,
            model_context_limit=3,
            reserved_output_and_tools=0,
            safety_margin=0,
        ),
    )

    assert tuple(item.block_id for item in result.selected_blocks) == ("goal",)
    exact_removed = {
        item.block_id
        for item in result.trace.rejections
        if item.reason == "EXACT_BUDGET_COMPONENT_REMOVED"
    }
    assert exact_removed == {"parallel-tool-round"}
    assert result.trace.ac_selected_cost == 1
    assert result.trace.total_input_cost <= result.trace.b_input


def test_raw_tail_breaks_when_an_atomic_unit_is_missing() -> None:
    first = replace(
        block("round-one", runtime.RuntimeSlot.RAW_DELTA, "round one", event_sequence=1),
        source_event_ids=("event-1", "event-2", "event-3"),
    )
    after_gap = replace(
        block("round-three", runtime.RuntimeSlot.RAW_DELTA, "round three", event_sequence=7),
        source_event_ids=("event-7",),
    )

    raw_tail = runtime.budget._contiguous_raw_tail((first, after_gap))

    assert raw_tail == (after_gap,)


def test_cross_slot_dependency_with_raw_companion_is_selected_once() -> None:
    dependency = replace(
        block(
            "dependency",
            runtime.RuntimeSlot.ACTIVE_GOAL,
            "D",
            required=True,
        ),
        kind=runtime.CandidateKind.DEPENDENCY,
        capsule_id="cross-slot",
        source_event_ids=("source-cross-slot",),
    )
    raw_companion = replace(
        block(
            "raw-companion",
            runtime.RuntimeSlot.RAW_DELTA,
            "R",
            event_sequence=1,
        ),
        source_event_ids=("source-cross-slot",),
    )
    candidates = (
        dependency,
        block(
            "normal-only-optional",
            runtime.RuntimeSlot.HARD_CONSTRAINT,
            "OO",
        ),
        raw_companion,
        block(
            "anchor",
            runtime.RuntimeSlot.EXACT_ANCHOR,
            "A",
            required=True,
        ),
    )

    result = call_assemble(
        candidates,
        config=runtime.BudgetConfig(
            target_input_budget=8,
            hard_input_ceiling=8,
            model_context_limit=8,
            reserved_output_and_tools=0,
            safety_margin=0,
        ),
    )

    assert result.trace.mode is runtime.AssemblyMode.EMERGENCY_ASSEMBLY
    assert tuple(item.block_id for item in result.selected_blocks) == (
        "dependency",
        "raw-companion",
        "anchor",
    )
    assert result.projected_text == "D\n\nR\n\nA"
    assert result.trace.ac_selected_cost == 7
    assert sum(item.block_id == "raw-companion" for item in result.trace.selections) == 1
