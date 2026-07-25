from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

import astrcontinuum as ac

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
LEASE_END = NOW + timedelta(minutes=5)


def session_key(session_id: str = "session-1") -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id=session_id,
        group_id=None,
        user_id="user-1",
        conversation_id=f"conversation-{session_id}",
        persona_id=None,
    )


def repository(data_dir: Path) -> ac.SQLiteRepository:
    factory = ac.SQLiteConnectionFactory(data_dir, busy_timeout_ms=5_000)
    ac.SQLiteMigrator(factory).migrate()
    return ac.SQLiteRepository(factory)


def capture_events(
    store: ac.SQLiteRepository,
    count: int,
    *,
    key: ac.SessionKey | None = None,
) -> None:
    key = key or session_key()
    for sequence in range(1, count + 1):
        store.capture_user_event(
            event_id=f"{key.session_id}-event-{sequence}",
            session_key=key,
            content=f"message {sequence}",
            idempotency_key=f"request-{sequence}",
            token_count=2,
            created_at=NOW,
        )


def raise_intent(
    store: ac.SQLiteRepository,
    *,
    target: int,
    job_id: str = "job-1",
    key: ac.SessionKey | None = None,
) -> ac.CompactionJobEnvelope | None:
    return store.raise_compaction_intent(
        job_id=job_id,
        session_key=key or session_key(),
        target_high_water_mark=target,
        now=NOW,
    )


def test_raise_intent_creates_one_pending_job_and_monotonically_folds_target(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture_events(store, 3, key=key)

    first = raise_intent(store, target=1, key=key)
    folded = raise_intent(store, target=3, job_id="ignored-job", key=key)
    duplicate = raise_intent(store, target=2, job_id="also-ignored", key=key)

    assert first is not None
    assert folded is not None
    assert duplicate is not None
    assert first.job_id == folded.job_id == duplicate.job_id == "job-1"
    assert duplicate.state == ac.CompactionJobState.PENDING
    assert duplicate.target_high_water_mark == 1
    assert duplicate.intent_target_high_water_mark == 3
    assert duplicate.base_snapshot_id is None
    assert duplicate.base_pointer_version == 0
    assert duplicate.lease_epoch == 0
    assert duplicate.attempt_count == 0

    with store.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM compaction_jobs").fetchone()[0] == 1


def test_concurrent_intents_converge_on_one_job_and_maximum_target(
    tmp_path: Path,
) -> None:
    setup = repository(tmp_path)
    capture_events(setup, 8)
    targets = list(range(1, 9))
    barrier = Barrier(len(targets))

    def trigger(target: int) -> ac.CompactionJobEnvelope | None:
        store = ac.SQLiteRepository(
            ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=5_000)
        )
        barrier.wait()
        return raise_intent(
            store,
            target=target,
            job_id=f"job-{target}",
        )

    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        jobs = list(pool.map(trigger, targets))

    assert all(job is not None for job in jobs)
    assert len({job.job_id for job in jobs if job is not None}) == 1
    with setup.factory.connection(read_only=True) as connection:
        row = connection.execute(
            """
            SELECT count(*) AS count, max(intent_target_high_water_mark) AS target
            FROM compaction_jobs
            """
        ).fetchone()
        assert (row["count"], row["target"]) == (1, 8)


def test_claim_freezes_intent_and_only_one_connection_wins(tmp_path: Path) -> None:
    setup = repository(tmp_path)
    capture_events(setup, 3)
    raise_intent(setup, target=1)
    raise_intent(setup, target=3)
    count = 6
    barrier = Barrier(count)

    def claim(index: int) -> ac.CompactionJobEnvelope | None:
        store = ac.SQLiteRepository(
            ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=5_000)
        )
        barrier.wait()
        return store.claim_job(
            worker_id=f"worker-{index}",
            now=NOW,
            lease_expires_at=LEASE_END,
        )

    with ThreadPoolExecutor(max_workers=count) as pool:
        outcomes = list(pool.map(claim, range(count)))

    winners = [job for job in outcomes if job is not None]
    assert len(winners) == 1
    winner = winners[0]
    assert winner.state == ac.CompactionJobState.LEASED
    assert winner.target_high_water_mark == 3
    assert winner.intent_target_high_water_mark == 3
    assert winner.lease_owner is not None
    assert winner.lease_epoch == 1
    assert winner.attempt_count == 1
    assert winner.lease_expires_at == LEASE_END


def test_later_intent_does_not_rewrite_active_attempt_target(tmp_path: Path) -> None:
    store = repository(tmp_path)
    capture_events(store, 2)
    raise_intent(store, target=2)
    claimed = store.claim_job(
        worker_id="worker-1",
        now=NOW,
        lease_expires_at=LEASE_END,
    )
    assert claimed is not None

    capture_events(store, 3)
    raised = raise_intent(store, target=3)

    assert raised is not None
    assert raised.state == ac.CompactionJobState.LEASED
    assert raised.target_high_water_mark == 2
    assert raised.intent_target_high_water_mark == 3


def test_fenced_transitions_follow_only_the_frozen_state_machine(tmp_path: Path) -> None:
    store = repository(tmp_path)
    capture_events(store, 1)
    raise_intent(store, target=1)
    leased = store.claim_job(
        worker_id="worker-1",
        now=NOW,
        lease_expires_at=LEASE_END,
    )
    assert leased is not None

    compiling = store.transition_job(
        job_id=leased.job_id,
        owner="worker-1",
        lease_epoch=leased.lease_epoch,
        to_state=ac.CompactionJobState.COMPILING,
        now=NOW + timedelta(seconds=1),
    )
    ready = store.transition_job(
        job_id=compiling.job_id,
        owner="worker-1",
        lease_epoch=compiling.lease_epoch,
        to_state=ac.CompactionJobState.READY_TO_COMMIT,
        candidate_snapshot_id="snapshot-candidate-1",
        now=NOW + timedelta(seconds=2),
    )

    assert ready.state == ac.CompactionJobState.READY_TO_COMMIT
    assert ready.candidate_snapshot_id == "snapshot-candidate-1"
    transition_error = getattr(ac, "JobTransitionError", None)
    assert transition_error is not None, "JobTransitionError export is missing"
    with pytest.raises(transition_error):
        store.transition_job(
            job_id=ready.job_id,
            owner="worker-1",
            lease_epoch=ready.lease_epoch,
            to_state=ac.CompactionJobState.COMPILING,
            now=NOW + timedelta(seconds=3),
        )


def test_stale_owner_epoch_and_expired_lease_change_nothing(tmp_path: Path) -> None:
    store = repository(tmp_path)
    capture_events(store, 1)
    raise_intent(store, target=1)
    leased = store.claim_job(
        worker_id="worker-1",
        now=NOW,
        lease_expires_at=LEASE_END,
    )
    assert leased is not None
    stale_error = getattr(ac, "StaleLeaseError", None)
    assert stale_error is not None, "StaleLeaseError export is missing"

    with pytest.raises(stale_error):
        store.transition_job(
            job_id=leased.job_id,
            owner="other-worker",
            lease_epoch=leased.lease_epoch,
            to_state=ac.CompactionJobState.COMPILING,
            now=NOW + timedelta(seconds=1),
        )
    with pytest.raises(stale_error):
        store.transition_job(
            job_id=leased.job_id,
            owner="worker-1",
            lease_epoch=leased.lease_epoch + 1,
            to_state=ac.CompactionJobState.COMPILING,
            now=NOW + timedelta(seconds=1),
        )
    with pytest.raises(stale_error):
        store.renew_job_lease(
            job_id=leased.job_id,
            owner="worker-1",
            lease_epoch=leased.lease_epoch,
            now=LEASE_END,
            lease_expires_at=LEASE_END + timedelta(minutes=5),
        )

    with store.factory.connection(read_only=True) as connection:
        row = connection.execute(
            """
            SELECT state, lease_owner, lease_epoch, attempt_count, lease_expires_at
            FROM compaction_jobs
            WHERE job_id = ?
            """,
            (leased.job_id,),
        ).fetchone()
        assert tuple(row) == (
            "LEASED",
            "worker-1",
            1,
            1,
            "2026-07-26T12:05:00.000000Z",
        )


def test_lease_renewal_is_fenced_and_monotonic(tmp_path: Path) -> None:
    store = repository(tmp_path)
    capture_events(store, 1)
    raise_intent(store, target=1)
    leased = store.claim_job(
        worker_id="worker-1",
        now=NOW,
        lease_expires_at=LEASE_END,
    )
    assert leased is not None

    renewed = store.renew_job_lease(
        job_id=leased.job_id,
        owner="worker-1",
        lease_epoch=leased.lease_epoch,
        now=NOW + timedelta(minutes=1),
        lease_expires_at=LEASE_END + timedelta(minutes=5),
    )

    assert renewed.lease_expires_at == LEASE_END + timedelta(minutes=5)
    transition_error = getattr(ac, "JobTransitionError", None)
    assert transition_error is not None, "JobTransitionError export is missing"
    with pytest.raises(transition_error):
        store.renew_job_lease(
            job_id=leased.job_id,
            owner="worker-1",
            lease_epoch=leased.lease_epoch,
            now=NOW + timedelta(minutes=2),
            lease_expires_at=LEASE_END,
        )


def test_claim_injected_failure_rolls_back_epoch_and_attempt(tmp_path: Path) -> None:
    setup = repository(tmp_path)
    capture_events(setup, 1)
    raise_intent(setup, target=1)

    def failpoint(name: str) -> None:
        if name == "claim.after_update":
            raise RuntimeError("injected claim crash")

    store = ac.SQLiteRepository(setup.factory, fault_injector=failpoint)
    with pytest.raises(RuntimeError, match="injected claim crash"):
        store.claim_job(
            worker_id="worker-1",
            now=NOW,
            lease_expires_at=LEASE_END,
        )

    with setup.factory.connection(read_only=True) as connection:
        row = connection.execute(
            """
            SELECT state, lease_epoch, attempt_count, lease_owner
            FROM compaction_jobs
            """
        ).fetchone()
        assert tuple(row) == ("PENDING", 0, 0, None)


def test_intent_rejects_missing_events_or_target_above_high_water(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    invariant_error = getattr(ac, "RepositoryInvariantError", None)
    assert invariant_error is not None

    with pytest.raises(invariant_error):
        raise_intent(store, target=1)

    capture_events(store, 1)
    with pytest.raises(invariant_error):
        raise_intent(store, target=2)
