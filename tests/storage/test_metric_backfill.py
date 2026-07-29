from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

import astrcontinuum as ac
from astrcontinuum.compaction.rendering import render_capsule
from astrcontinuum.storage import (
    ArtifactKind,
    CanonicalMetricObservation,
    StorageSecurityError,
    TokenMetric,
    TokenMetricConflict,
    TokenMetricStore,
)
from astrcontinuum.tokenization import CANONICAL_O200K
from tests.storage import test_repository_publication as publication
from tests.storage.security_testkit import secure_repository, storage_test_codec

NOW = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)


def session_key(name: str = "backfill") -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id=f"session-{name}",
        group_id=None,
        user_id=f"user-{name}",
        conversation_id=f"conversation-{name}",
        persona_id=None,
    )


def repository(data_dir: Path) -> ac.SQLiteRepository:
    return secure_repository(ac.SQLiteConnectionFactory(data_dir, busy_timeout_ms=5_000))


def capture(
    store: ac.SQLiteRepository,
    sequence: int,
    *,
    key: ac.SessionKey | None = None,
) -> ac.EventEnvelope:
    selected_key = key or session_key()
    return store.capture_user_event(
        event_id=f"{selected_key.session_id}-event-{sequence}",
        session_key=selected_key,
        content=f"canonical text {sequence}",
        idempotency_key=f"request-{sequence}",
        token_count=10_000 + sequence,
        canonical=CanonicalMetricObservation(CANONICAL_O200K.profile_id, None),
        created_at=NOW + timedelta(seconds=sequence),
    )


def read_metric(
    store: ac.SQLiteRepository,
    artifact_id: str,
) -> TokenMetric | None:
    metric_store = TokenMetricStore(storage_test_codec())
    with store.factory.connection(read_only=True) as connection:
        connection.execute("BEGIN")
        try:
            return metric_store.get_in_transaction(
                connection,
                artifact_kind=ArtifactKind.EVENT,
                artifact_id=artifact_id,
                tokenizer_profile_id=CANONICAL_O200K.profile_id,
            )
        finally:
            connection.rollback()


def test_backfill_is_stable_bounded_and_discovers_artifacts_without_intents(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    key = session_key()
    events = tuple(capture(store, sequence, key=key) for sequence in range(1, 4))
    with store.factory.transaction(immediate=True) as connection:
        connection.execute("DELETE FROM token_metric_backfill_intents")

    first = store.read_metric_backfill_batch(
        key,
        profile_id=CANONICAL_O200K.profile_id,
        limit=2,
    )

    assert len(first) == 2
    assert tuple(item.sort_key for item in first) == tuple(sorted(item.sort_key for item in first))
    assert tuple(item.artifact_id for item in first) == tuple(
        event.event_id for event in events[:2]
    )
    assert tuple(item.text for item in first) == ("canonical text 1", "canonical text 2")
    assert "canonical text" not in repr(first)

    metrics = tuple(
        TokenMetric(
            item.artifact_kind,
            item.artifact_id,
            CANONICAL_O200K.profile_id,
            len(item.text),
        )
        for item in first
    )
    assert store.write_metric_backfill_batch(key, metrics, now=NOW) == metrics
    assert store.write_metric_backfill_batch(key, metrics, now=NOW) == metrics

    remaining = store.read_metric_backfill_batch(
        key,
        profile_id=CANONICAL_O200K.profile_id,
        limit=32,
    )
    assert tuple(item.artifact_id for item in remaining) == (events[2].event_id,)
    assert read_metric(store, events[0].event_id) == metrics[0]
    assert read_metric(store, events[1].event_id) == metrics[1]


def test_backfill_reads_only_the_requested_session(tmp_path: Path) -> None:
    store = repository(tmp_path)
    first_key = session_key("one")
    second_key = session_key("two")
    first = capture(store, 1, key=first_key)
    capture(store, 1, key=second_key)

    batch = store.read_metric_backfill_batch(
        first_key,
        profile_id=CANONICAL_O200K.profile_id,
        limit=32,
    )

    assert tuple(item.artifact_id for item in batch) == (first.event_id,)


def test_backfill_renders_capsules_and_reads_snapshot_context_then_clears_intents(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    publication.capture(store, 1)
    item = publication.capsule(1)
    members = publication.memberships(item)
    snapshot = publication.candidate_snapshot("snapshot-backfill", members, target=1)
    job = publication.ready_job(
        store,
        target=1,
        candidate_snapshot_id=snapshot.snapshot_id,
        job_id="job-backfill",
    )
    publication.publish(store, job, snapshot, members)
    with store.factory.transaction(immediate=True) as connection:
        connection.execute(
            "DELETE FROM token_metrics WHERE artifact_kind IN ('CAPSULE', 'SNAPSHOT')"
        )
        connection.executemany(
            """
            INSERT INTO token_metric_backfill_intents (
                session_key_hash,
                artifact_kind,
                artifact_id,
                tokenizer_profile_id,
                created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                (
                    publication.session_key().session_key_hash,
                    kind.value,
                    artifact_id,
                    CANONICAL_O200K.profile_id,
                    "2026-07-28T08:00:00.000000Z",
                )
                for kind, artifact_id in (
                    (ArtifactKind.CAPSULE, item.capsule_id),
                    (ArtifactKind.SNAPSHOT, snapshot.snapshot_id),
                )
            ),
        )

    batch = store.read_metric_backfill_batch(
        publication.session_key(),
        profile_id=CANONICAL_O200K.profile_id,
        limit=32,
    )
    selected = tuple(
        artifact
        for artifact in batch
        if artifact.artifact_kind in {ArtifactKind.CAPSULE, ArtifactKind.SNAPSHOT}
    )

    assert tuple(artifact.artifact_kind for artifact in selected) == (
        ArtifactKind.CAPSULE,
        ArtifactKind.SNAPSHOT,
    )
    assert selected[0].text == render_capsule(item)
    assert selected[1].text == snapshot.rendered_context
    metrics = tuple(
        TokenMetric(
            artifact.artifact_kind,
            artifact.artifact_id,
            CANONICAL_O200K.profile_id,
            len(artifact.text),
        )
        for artifact in selected
    )
    store.write_metric_backfill_batch(publication.session_key(), metrics, now=NOW)

    restarted = repository(tmp_path)
    remaining = restarted.read_metric_backfill_batch(
        publication.session_key(),
        profile_id=CANONICAL_O200K.profile_id,
        limit=32,
    )
    assert all(artifact.artifact_kind is ArtifactKind.EVENT for artifact in remaining)
    with restarted.factory.connection(read_only=True) as connection:
        assert (
            connection.execute(
                """
                SELECT count(*)
                FROM token_metric_backfill_intents
                WHERE artifact_kind IN ('CAPSULE', 'SNAPSHOT')
                """
            ).fetchone()[0]
            == 0
        )


def test_backfill_authenticates_artifact_text_before_returning_it(tmp_path: Path) -> None:
    store = repository(tmp_path)
    key = session_key()
    event = capture(store, 1, key=key)
    with store.factory.transaction(immediate=True) as connection:
        connection.execute("DROP TRIGGER journal_events_immutable_update")
        connection.execute(
            "UPDATE journal_events SET content = 'acenc:v1:tampered' WHERE event_id = ?",
            (event.event_id,),
        )

    with pytest.raises(StorageSecurityError):
        store.read_metric_backfill_batch(
            key,
            profile_id=CANONICAL_O200K.profile_id,
            limit=32,
        )


def test_backfill_concurrent_logical_cas_allows_only_one_count(tmp_path: Path) -> None:
    first_store = repository(tmp_path)
    second_store = repository(tmp_path)
    key = session_key()
    event = capture(first_store, 1, key=key)
    barrier = Barrier(2)

    def write(store: ac.SQLiteRepository, count: int) -> TokenMetric:
        metric = TokenMetric(
            ArtifactKind.EVENT,
            event.event_id,
            CANONICAL_O200K.profile_id,
            count,
        )
        barrier.wait()
        return store.write_metric_backfill_batch(key, (metric,), now=NOW)[0]

    outcomes: list[TokenMetric | BaseException] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(write, first_store, 3),
            executor.submit(write, second_store, 4),
        )
        for future in futures:
            try:
                outcomes.append(future.result())
            except TokenMetricConflict as error:
                outcomes.append(error)

    assert sum(isinstance(outcome, TokenMetric) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, TokenMetricConflict) for outcome in outcomes) == 1
    stored = read_metric(first_store, event.event_id)
    assert stored is not None
    assert stored.token_count in {3, 4}
