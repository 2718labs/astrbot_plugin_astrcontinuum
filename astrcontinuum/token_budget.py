from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BudgetConfig:
    target_input_tokens: int = 130_000
    hard_input_ceiling: int = 150_000
    recent_raw_tokens: int = 50_000
    retrieval_tokens: int = 25_000
    snapshot_tokens: int = 20_000

    def validate(self) -> None:
        if self.target_input_tokens <= 0:
            raise ValueError("target_input_tokens must be positive")
        if self.hard_input_ceiling < self.target_input_tokens:
            raise ValueError("hard_input_ceiling must be >= target_input_tokens")


@dataclass(slots=True)
class BudgetLedger:
    limit: int
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def reserve(self, requested: int) -> int:
        granted = min(max(0, requested), self.remaining)
        self.used += granted
        return granted
