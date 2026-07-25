import pytest
from astrcontinuum.in_memory import InMemoryEventStore
from astrcontinuum.models import ContextEvent


def event(seq: int) -> ContextEvent:
    return ContextEvent(f"e{seq}", "s", seq, "message", "user", f"message {seq}", float(seq))


@pytest.mark.asyncio
async def test_delta_contains_every_event_after_snapshot_boundary() -> None:
    store = InMemoryEventStore()
    for seq in range(1, 11):
        await store.append(event(seq))
    delta = await store.read_after("s", 6)
    assert [item.sequence for item in delta] == [7, 8, 9, 10]


@pytest.mark.asyncio
async def test_event_sequence_is_append_only() -> None:
    store = InMemoryEventStore()
    await store.append(event(1))
    with pytest.raises(ValueError):
        await store.append(event(1))
