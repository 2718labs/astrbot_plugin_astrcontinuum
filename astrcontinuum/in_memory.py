from __future__ import annotations

import asyncio
from collections import defaultdict

from .models import ContextEvent, Snapshot


class InMemoryEventStore:
    def __init__(self) -> None:
        self._events: dict[str, list[ContextEvent]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def append(self, event: ContextEvent) -> None:
        async with self._lock:
            events = self._events[event.session_id]
            if events and event.sequence <= events[-1].sequence:
                raise ValueError("event sequence must be strictly increasing")
            events.append(event)

    async def latest_sequence(self, session_id: str) -> int:
        events = self._events.get(session_id, [])
        return events[-1].sequence if events else 0

    async def read_after(self, session_id: str, sequence: int) -> list[ContextEvent]:
        return [event for event in self._events.get(session_id, []) if event.sequence > sequence]

    async def read_range(
        self, session_id: str, start_sequence: int, end_sequence: int
    ) -> list[ContextEvent]:
        return [
            event
            for event in self._events.get(session_id, [])
            if start_sequence <= event.sequence <= end_sequence
        ]


class InMemorySnapshotStore:
    def __init__(self) -> None:
        self._active: dict[str, Snapshot] = {}
        self._lock = asyncio.Lock()

    async def latest_committed(self, session_id: str) -> Snapshot | None:
        return self._active.get(session_id)

    async def commit_candidate(
        self, candidate: Snapshot, expected_base_version: int | None
    ) -> bool:
        if not candidate.committed:
            raise ValueError("candidate must be marked committed")
        async with self._lock:
            current = self._active.get(candidate.session_id)
            current_version = current.version if current else None
            if current_version != expected_base_version:
                return False
            self._active[candidate.session_id] = candidate
            return True
