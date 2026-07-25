from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

import astrcontinuum as ac

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def session_key(index: int = 1) -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id=f"session-{index}",
        group_id=None,
        user_id="user-1",
        conversation_id=f"conversation-{index}",
        persona_id=None,
    )


def repository(data_dir: Path) -> ac.SQLiteRepository:
    factory = ac.SQLiteConnectionFactory(data_dir, busy_timeout_ms=5_000)
    ac.SQLiteMigrator(factory).migrate()
    return ac.SQLiteRepository(factory)


def create_claimed_job(
    store: ac.SQLiteRepository,
    *,
    index: int = 1,
    lease_expires_at: datetime | None = None,
) -> ac.CompactionJobEnvelope:
    key = session_key(index)
    store.capture_user_event(
        event_id=f"event-{index}",
        session_key=key,
        content=f"message {index}",
        idempotency_key=f"request-{index}",
        token_count=2,
        created_at=NOW,
    )
    raised = store.raise_compaction_intent(
        job_id=f"job-{index}",
        session_key=key,
        target_high_water_mark=1,
        now=NOW,
    )
    assert raised is not None
    claimed = store.claim_job(
        worker_id=f"worker-{index}",
        now=NOW,
        lease_expires_at=lease_expires_at or NOW + timedelta(minutes=5),
    )
    assert claimed is not None
    return claimed


def transition_to(
    store: ac.SQLiteRepository,
    job: ac.CompactionJobEnvelope,
    state: ac.CompactionJobState,
    *,
    candidate_snapshot_id: str | None = None,
) -> ac.CompactionJobEnvelope:
    return store.transition_job(
        job_id=job.job_id,
        owner=job.lease_owner or "",
        lease_epoch=job.lease_epoch,
        to_state=state,
        candidate_snapshot_id=candidate_snapshot_id,
        now=NOW + timedelta(seconds=1),
    )


def test_fail_job_enters_retry_wait_then_reclaim_increments_fence(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    leased = create_claimed_job(store)
    compiling = transition_to(store, leased, ac.CompactionJobState.COMPILING)
    retry_at = NOW + timedelta(minutes=2)

    retrying = store.fail_job(
        job_id=compiling.job_id,
        owner="worker-1",
        lease_epoch=compiling.lease_epoch,
        now=NOW + timedelta(minutes=1),
        error_stage="compiler",
        error_code="provider_timeout",
        error_message="provider timed out",
        retry_at=retry_at,
    )

    assert retrying.state == ac.CompactionJobState.RETRY_WAIT
    assert retrying.lease_owner is None
    assert retrying.lease_expires_at is None
    assert retrying.lease_epoch == 1
    assert retrying.attempt_count == 1
    assert retrying.next_retry_at == retry_at
    assert (
        retrying.error_stage,
        retrying.error_code,
        retrying.error_message,
    ) == ("compiler", "provider_timeout", "provider timed out")
    assert (
        store.claim_job(
            worker_id="worker-2",
            now=retry_at - timedelta(microseconds=1),
            lease_expires_at=retry_at + timedelta(minutes=5),
        )
        is None
    )

    reclaimed = store.claim_job(
        worker_id="worker-2",
        now=retry_at,
        lease_expires_at=retry_at + timedelta(minutes=5),
    )
    assert reclaimed is not None
    assert reclaimed.state == ac.CompactionJobState.LEASED
    assert reclaimed.lease_owner == "worker-2"
    assert reclaimed.lease_epoch == 2
    assert reclaimed.attempt_count == 2
    assert reclaimed.error_stage is None


def test_terminal_failure_clears_ready_candidate_and_lease(tmp_path: Path) -> None:
    store = repository(tmp_path)
    leased = create_claimed_job(store)
    compiling = transition_to(store, leased, ac.CompactionJobState.COMPILING)
    ready = transition_to(
        store,
        compiling,
        ac.CompactionJobState.READY_TO_COMMIT,
        candidate_snapshot_id="candidate-1",
    )

    failed = store.fail_job(
        job_id=ready.job_id,
        owner="worker-1",
        lease_epoch=ready.lease_epoch,
        now=NOW + timedelta(minutes=1),
        error_stage="publish",
        error_code="mechanical_reject",
        error_message="candidate failed permanent validation",
        retry_at=None,
    )

    assert failed.state == ac.CompactionJobState.FAILED
    assert failed.candidate_snapshot_id is None
    assert failed.lease_owner is None
    assert failed.lease_expires_at is None
    assert failed.next_retry_at is None
    assert failed.committed_at is None


def test_fail_job_is_fenced_and_injected_failure_rolls_back(tmp_path: Path) -> None:
    setup = repository(tmp_path)
    leased = create_claimed_job(setup)
    stale_error = getattr(ac, "StaleLeaseError", None)
    assert stale_error is not None

    with pytest.raises(stale_error):
        setup.fail_job(
            job_id=leased.job_id,
            owner="wrong-worker",
            lease_epoch=leased.lease_epoch,
            now=NOW + timedelta(seconds=1),
            error_stage="compiler",
            error_code="failed",
            error_message="safe error",
            retry_at=None,
        )

    def failpoint(name: str) -> None:
        if name == "failure.after_update":
            raise RuntimeError("injected failure crash")

    crashing = ac.SQLiteRepository(setup.factory, fault_injector=failpoint)
    with pytest.raises(RuntimeError, match="injected failure crash"):
        crashing.fail_job(
            job_id=leased.job_id,
            owner="worker-1",
            lease_epoch=leased.lease_epoch,
            now=NOW + timedelta(seconds=1),
            error_stage="compiler",
            error_code="failed",
            error_message="safe error",
            retry_at=None,
        )

    with setup.factory.connection(read_only=True) as connection:
        row = connection.execute(
            """
            SELECT state, lease_owner, lease_epoch, error_stage
            FROM compaction_jobs
            """
        ).fetchone()
        assert tuple(row) == ("LEASED", "worker-1", 1, None)


def test_fail_job_rejects_partial_or_invalid_error_and_retry_time(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    leased = create_claimed_job(store)

    with pytest.raises(ValueError):
        store.fail_job(
            job_id=leased.job_id,
            owner="worker-1",
            lease_epoch=leased.lease_epoch,
            now=NOW + timedelta(seconds=1),
            error_stage="",
            error_code="failed",
            error_message="safe error",
            retry_at=None,
        )
    with pytest.raises(ValueError):
        store.fail_job(
            job_id=leased.job_id,
            owner="worker-1",
            lease_epoch=leased.lease_epoch,
            now=NOW + timedelta(seconds=1),
            error_stage="compiler",
            error_code="failed",
            error_message="safe error",
            retry_at=NOW,
        )


def test_recovery_requeues_every_expired_working_state_and_clears_candidate(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    expiry = NOW + timedelta(minutes=1)
    states: dict[int, ac.CompactionJobEnvelope] = {}

    states[1] = create_claimed_job(store, index=1, lease_expires_at=expiry)
    states[2] = transition_to(
        store,
        create_claimed_job(store, index=2, lease_expires_at=expiry),
        ac.CompactionJobState.COMPILING,
    )
    compiling = transition_to(
        store,
        create_claimed_job(store, index=3, lease_expires_at=expiry),
        ac.CompactionJobState.COMPILING,
    )
    states[3] = transition_to(store, compiling, ac.CompactionJobState.AUDITING)
    compiling = transition_to(
        store,
        create_claimed_job(store, index=4, lease_expires_at=expiry),
        ac.CompactionJobState.COMPILING,
    )
    states[4] = transition_to(
        store,
        compiling,
        ac.CompactionJobState.READY_TO_COMMIT,
        candidate_snapshot_id="candidate-4",
    )
    live = create_claimed_job(
        store,
        index=5,
        lease_expires_at=NOW + timedelta(minutes=10),
    )

    recovered = store.recover_expired_leases(now=NOW + timedelta(minutes=2))

    assert {job.job_id for job in recovered} == {
        "job-1",
        "job-2",
        "job-3",
        "job-4",
    }
    assert all(job.state == ac.CompactionJobState.PENDING for job in recovered)
    assert all(job.lease_owner is None for job in recovered)
    assert all(job.lease_expires_at is None for job in recovered)
    assert all(job.lease_epoch == 1 for job in recovered)
    assert all(job.attempt_count == 1 for job in recovered)
    assert all(job.candidate_snapshot_id is None for job in recovered)

    with store.factory.connection(read_only=True) as connection:
        live_row = connection.execute(
            "SELECT state, lease_owner FROM compaction_jobs WHERE job_id = ?",
            (live.job_id,),
        ).fetchone()
        assert tuple(live_row) == ("LEASED", "worker-5")
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 5
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0


def test_concurrent_recovery_has_one_effective_winner(tmp_path: Path) -> None:
    setup = repository(tmp_path)
    create_claimed_job(
        setup,
        lease_expires_at=NOW + timedelta(minutes=1),
    )
    barrier = Barrier(2)

    def recover() -> tuple[ac.CompactionJobEnvelope, ...]:
        store = ac.SQLiteRepository(ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=5_000))
        barrier.wait()
        return store.recover_expired_leases(now=NOW + timedelta(minutes=2))

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: recover(), range(2)))

    assert sorted(len(items) for items in outcomes) == [0, 1]


def test_recovery_injected_failure_rolls_back_whole_scan(tmp_path: Path) -> None:
    setup = repository(tmp_path)
    for index in (1, 2):
        create_claimed_job(
            setup,
            index=index,
            lease_expires_at=NOW + timedelta(minutes=1),
        )
    calls = 0

    def failpoint(name: str) -> None:
        nonlocal calls
        if name == "recovery.after_update":
            calls += 1
            if calls == 2:
                raise RuntimeError("injected recovery crash")

    crashing = ac.SQLiteRepository(setup.factory, fault_injector=failpoint)
    with pytest.raises(RuntimeError, match="injected recovery crash"):
        crashing.recover_expired_leases(now=NOW + timedelta(minutes=2))

    with setup.factory.connection(read_only=True) as connection:
        rows = connection.execute(
            "SELECT state, lease_owner, lease_epoch FROM compaction_jobs ORDER BY job_id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("LEASED", "worker-1", 1),
            ("LEASED", "worker-2", 1),
        ]
