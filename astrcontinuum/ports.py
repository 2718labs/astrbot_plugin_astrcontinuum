from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .models import AuditReport, ContextCapsule, ContextEvent, Snapshot
from .runtime.types import TokenCounter as TokenCounter  # noqa: PLC0414


class EventStore(Protocol):
    async def append(self, event: ContextEvent) -> None: ...
    async def latest_sequence(self, session_id: str) -> int: ...
    async def read_after(self, session_id: str, sequence: int) -> list[ContextEvent]: ...
    async def read_range(
        self, session_id: str, start_sequence: int, end_sequence: int
    ) -> list[ContextEvent]: ...


class SnapshotStore(Protocol):
    async def latest_committed(self, session_id: str) -> Snapshot | None: ...
    async def commit_candidate(
        self, candidate: Snapshot, expected_base_version: int | None
    ) -> bool: ...


class CapsuleCompiler(Protocol):
    async def compile(
        self, session_id: str, base_snapshot: Snapshot | None, events: Sequence[ContextEvent]
    ) -> tuple[ContextCapsule, ...]: ...


class LossAuditor(Protocol):
    async def audit(
        self, previous: Snapshot | None, candidate: Snapshot, covered_events: Sequence[ContextEvent]
    ) -> AuditReport: ...


class ExternalMemoryProvider(Protocol):
    async def retrieve(self, session_id: str, query: str, budget_tokens: int) -> list[str]: ...
    async def importance_hints(self, session_id: str, texts: list[str]) -> list[float]: ...
