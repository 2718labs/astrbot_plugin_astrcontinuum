from __future__ import annotations

import time
import uuid

from .auditor import DeterministicLossAuditor
from .models import Snapshot
from .ports import CapsuleCompiler, EventStore, SnapshotStore


class CompactionService:
    def __init__(self, *, events: EventStore, snapshots: SnapshotStore, compiler: CapsuleCompiler, auditor: DeterministicLossAuditor) -> None:
        self._events = events
        self._snapshots = snapshots
        self._compiler = compiler
        self._auditor = auditor

    async def compact_session(self, session_id: str) -> bool:
        base = await self._snapshots.latest_committed(session_id)
        covered = base.covered_event_seq if base else 0
        delta = await self._events.read_after(session_id, covered)
        if not delta:
            return False
        capsules = await self._compiler.compile(session_id, base, delta)
        candidate = Snapshot(str(uuid.uuid4()), session_id, base.version + 1 if base else 1, delta[-1].sequence, capsules, False, time.time(), base.version if base else None)
        events = await self._events.read_range(session_id, 1, candidate.covered_event_seq)
        report = await self._auditor.audit(base, candidate, events)
        if not report.passed:
            return False
        committed = Snapshot(candidate.snapshot_id, candidate.session_id, candidate.version, candidate.covered_event_seq, candidate.capsules, True, candidate.created_at, candidate.base_version)
        return await self._snapshots.commit_candidate(committed, base.version if base else None)
