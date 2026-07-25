"""AstrContinuum AstrBot plugin host scaffold.

Codex must verify all AstrBot imports and hook signatures against the target
AstrBot version before treating this as production-ready.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

try:
    from astrbot.api import logger
    from astrbot.api.event import AstrMessageEvent, filter
    from astrbot.api.star import Context, Star, register
except ImportError:
    import logging
    logger = logging.getLogger("astrbot_plugin_astrcontinuum")

    class _Filter:
        def on_llm_request(self): return lambda fn: fn
        def on_agent_done(self): return lambda fn: fn
        def command(self, *_args: Any, **_kwargs: Any): return lambda fn: fn
    filter = _Filter()

    class AstrMessageEvent: pass
    class Context: pass
    class Star:
        def __init__(self, context: Any = None, config: dict[str, Any] | None = None):
            self.context = context
            self.config = config or {}
    def register(*_args: Any, **_kwargs: Any): return lambda cls: cls

from astrcontinuum.auditor import DeterministicLossAuditor
from astrcontinuum.compiler_stub import ScaffoldCapsuleCompiler
from astrcontinuum.in_memory import InMemoryEventStore, InMemorySnapshotStore
from astrcontinuum.models import ContextEvent
from astrcontinuum.scheduler import CoalescingCompactionScheduler
from astrcontinuum.service import CompactionService


@register("astrbot_plugin_astrcontinuum", "Ayleovelle", "Non-blocking infinite context runtime for AstrBot", "0.1.0-alpha")
class AstrContinuumPlugin(Star):
    def __init__(self, context: Context, config: dict[str, Any] | None = None):
        super().__init__(context)
        self.config = config or {}
        self.events = InMemoryEventStore()
        self.snapshots = InMemorySnapshotStore()
        self.compaction = CompactionService(events=self.events, snapshots=self.snapshots, compiler=ScaffoldCapsuleCompiler(), auditor=DeterministicLossAuditor())
        self.scheduler = CoalescingCompactionScheduler(self.compaction.compact_session)
        self._started = False
        self._sequence: dict[str, int] = {}

    async def _ensure_started(self) -> None:
        if not self._started:
            await self.scheduler.start()
            self._started = True

    def _session_id(self, event: AstrMessageEvent) -> str:
        origin = getattr(event, "unified_msg_origin", None)
        if isinstance(origin, str) and origin:
            return origin
        return str(getattr(event, "session_id", "unknown"))

    async def _append_event(self, session_id: str, *, role: str, content: str, event_type: str) -> None:
        sequence = self._sequence.get(session_id, 0) + 1
        self._sequence[session_id] = sequence
        await self.events.append(ContextEvent(str(uuid.uuid4()), session_id, sequence, event_type, role, content, time.time()))

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: Any) -> None:
        """Fast path only: never await compaction or a summary provider."""
        await self._ensure_started()
        # TODO: committed snapshot + delta + retrieval + budget assembly.
        _ = (event, req)

    @filter.on_agent_done()
    async def on_agent_done(self, event: AstrMessageEvent, run_context: Any, resp: Any) -> None:
        del run_context
        await self._ensure_started()
        session_id = self._session_id(event)
        text = str(getattr(resp, "completion_text", "") or "")
        await self._append_event(session_id, role="assistant", content=text, event_type="assistant_message")
        await self.scheduler.notify(session_id)

    @filter.command("context_status")
    async def context_status(self, event: AstrMessageEvent):
        session_id = self._session_id(event)
        snapshot = await self.snapshots.latest_committed(session_id)
        latest = await self.events.latest_sequence(session_id)
        covered = snapshot.covered_event_seq if snapshot else 0
        return {"session_id": session_id, "snapshot_version": snapshot.version if snapshot else None, "covered_event_seq": covered, "latest_event_seq": latest, "delta_events": latest - covered}
