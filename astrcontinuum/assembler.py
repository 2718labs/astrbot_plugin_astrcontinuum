from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import AssemblyTrace, ContextEvent, Snapshot
from .token_budget import BudgetConfig, BudgetLedger


@dataclass(frozen=True, slots=True)
class ContextBlock:
    slot: str
    text: str
    tokens: int
    priority: int
    required: bool = False


@dataclass(frozen=True, slots=True)
class AssemblyResult:
    blocks: tuple[ContextBlock, ...]
    trace: AssemblyTrace

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks if block.text)


class ContextAssembler:
    """Deterministic fast-path assembler. It must never call an LLM."""

    def __init__(self, config: BudgetConfig) -> None:
        config.validate()
        self._config = config

    def build(self, *, session_id: str, snapshot: Snapshot | None, delta: Iterable[ContextEvent], current_input: str, candidate_blocks: Iterable[ContextBlock]) -> AssemblyResult:
        ledger = BudgetLedger(self._config.target_input_tokens)
        selected: list[ContextBlock] = []
        current = ContextBlock("current_input", current_input, max(1, len(current_input) // 4), 100, True)
        if current.tokens > self._config.hard_input_ceiling:
            raise ValueError("current input alone exceeds hard ceiling")
        ledger.reserve(current.tokens)
        selected.append(current)

        for block in sorted(candidate_blocks, key=lambda item: item.priority, reverse=True):
            if block.tokens <= ledger.remaining:
                ledger.reserve(block.tokens)
                selected.append(block)
            elif block.required:
                raise ValueError(f"required block does not fit: {block.slot}")

        delta_list = list(delta)
        latest_seq = delta_list[-1].sequence if delta_list else (snapshot.covered_event_seq if snapshot else 0)
        slot_tokens: dict[str, int] = {}
        for block in selected:
            slot_tokens[block.slot] = slot_tokens.get(block.slot, 0) + block.tokens
        return AssemblyResult(tuple(selected), AssemblyTrace(session_id, snapshot.version if snapshot else None, snapshot.covered_event_seq if snapshot else 0, latest_seq, ledger.used, slot_tokens))
