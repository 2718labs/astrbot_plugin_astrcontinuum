import pytest
from astrcontinuum.in_memory import InMemorySnapshotStore
from astrcontinuum.models import Snapshot


def snap(version: int, committed: bool = True) -> Snapshot:
    return Snapshot(f"s{version}", "session", version, version * 10, (), committed, float(version), version - 1 if version > 1 else None)


@pytest.mark.asyncio
async def test_stale_candidate_cannot_replace_newer_snapshot() -> None:
    store = InMemorySnapshotStore()
    assert await store.commit_candidate(snap(1), None)
    assert await store.commit_candidate(snap(2), 1)
    stale = Snapshot("stale", "session", 2, 15, (), True, 3.0, 1)
    assert not await store.commit_candidate(stale, 1)
    active = await store.latest_committed("session")
    assert active is not None and active.snapshot_id == "s2"


@pytest.mark.asyncio
async def test_uncommitted_candidate_is_rejected() -> None:
    store = InMemorySnapshotStore()
    with pytest.raises(ValueError):
        await store.commit_candidate(snap(1, False), None)
