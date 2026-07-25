from astrcontinuum.assembler import ContextAssembler, ContextBlock
from astrcontinuum.models import ContextEvent
from astrcontinuum.token_budget import BudgetConfig


def test_assembler_respects_target_budget() -> None:
    assembler = ContextAssembler(BudgetConfig(target_input_tokens=100, hard_input_ceiling=120, recent_raw_tokens=50, retrieval_tokens=25, snapshot_tokens=20))
    event = ContextEvent("e1", "s", 1, "message", "user", "hello", 1.0)
    result = assembler.build(session_id="s", snapshot=None, delta=[event], current_input="current request", candidate_blocks=[ContextBlock("constraint", "must preserve", 20, 90, True), ContextBlock("recent", "recent raw", 60, 70), ContextBlock("background", "old", 50, 10)])
    assert result.trace.total_tokens <= 100
    assert "constraint" in result.trace.slot_tokens
