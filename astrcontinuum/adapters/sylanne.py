from __future__ import annotations

import importlib
from typing import Any


class SylanneAdapter:
    """Optional read-only bridge; use a stable public API in production."""

    def __init__(self) -> None:
        self._module: Any | None = None

    def detect(self) -> bool:
        try:
            self._module = importlib.import_module("sylanne_alpha")
        except ImportError:
            self._module = None
        return self._module is not None

    async def retrieve(self, session_id: str, query: str, budget_tokens: int) -> list[str]:
        del session_id, query, budget_tokens
        return []

    async def importance_hints(self, session_id: str, texts: list[str]) -> list[float]:
        del session_id
        return [0.0 for _ in texts]
