from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

CompactionCallback = Callable[[str], Awaitable[None]]


class CoalescingCompactionScheduler:
    """Repeated notifications coalesce; notify never waits for actual compaction."""

    def __init__(self, callback: CompactionCallback) -> None:
        self._callback = callback
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._pending: set[str] = set()
        self._lock = asyncio.Lock()
        self._worker: asyncio.Task[None] | None = None
        self._closed = False

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="astrcontinuum-compactor")

    async def notify(self, session_id: str) -> None:
        async with self._lock:
            if self._closed or session_id in self._pending:
                return
            self._pending.add(session_id)
            self._queue.put_nowait(session_id)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    async def _run(self) -> None:
        while True:
            session_id = await self._queue.get()
            try:
                await self._callback(session_id)
            finally:
                async with self._lock:
                    self._pending.discard(session_id)
                self._queue.task_done()
