import asyncio
import time

import pytest

from astrcontinuum.scheduler import CoalescingCompactionScheduler


@pytest.mark.asyncio
async def test_notify_does_not_wait_for_compaction() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_compaction(session_id: str) -> None:
        assert session_id == "s1"
        started.set()
        await release.wait()

    scheduler = CoalescingCompactionScheduler(slow_compaction)
    await scheduler.start()
    try:
        before = time.perf_counter()
        await scheduler.notify("s1")
        assert time.perf_counter() - before < 0.05
        await asyncio.wait_for(started.wait(), timeout=1)
    finally:
        release.set()
        await scheduler.close()
