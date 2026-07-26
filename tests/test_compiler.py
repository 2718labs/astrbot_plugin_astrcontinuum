from __future__ import annotations

import importlib
from dataclasses import FrozenInstanceError, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

import astrcontinuum as ac

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(days=1)
SECRET = "SENTINEL-PRIVATE-COMPILER-CONTENT"
COMPILER_SURFACE_NAMES = (
    "CompilationRequest",
    "CompilerOutput",
    "CompilerBackend",
    "CompilationCandidate",
    "CompilerErrorCode",
    "CompilerInvariantError",
    "compile_candidate",
    "validate_candidate",
)


def compiler_surface() -> dict[str, Any]:
    values = {name: getattr(ac, name, None) for name in COMPILER_SURFACE_NAMES}
    missing = tuple(name for name, value in values.items() if value is None)
    assert not missing, f"canonical compiler surface is missing: {', '.join(missing)}"
    return values


def session_key(name: str = "session-1") -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id=name,
        group_id=None,
        user_id=f"user-{name}",
        conversation_id=f"conversation-{name}",
        persona_id=None,
    )


def event(
    sequence: int,
    *,
    key: ac.SessionKey | None = None,
    content: str | None = None,
) -> ac.EventEnvelope:
    return ac.EventEnvelope.create(
        event_id=f"event-{sequence}",
        session_key=key if key is not None else session_key(),
        sequence=sequence,
        event_type=ac.EventType.USER_MESSAGE,
        content=content if content is not None else f"message {sequence}",
        idempotency_key=f"request-{sequence}",
        token_count=2,
        created_at=NOW,
    )


def claim(claim_id: str, source_event_id: str) -> ac.CapsuleClaim:
    return ac.CapsuleClaim(
        claim_id=claim_id,
        text=f"claim {claim_id}",
        status=ac.SemanticStatus.ACTIVE,
        confidence=1.0,
        source_event_ids=(source_event_id,),
    )


def anchor(anchor_id: str, source_event_id: str) -> ac.CapsuleAnchor:
    return ac.CapsuleAnchor(
        anchor_id=anchor_id,
        anchor_type=ac.AnchorType.NAME,
        exact_text=f"anchor {anchor_id}",
        source_event_ids=(source_event_id,),
        status=ac.AnchorStatus.ACTIVE,
        importance=1.0,
    )


def quality() -> ac.CapsuleQuality:
    return ac.CapsuleQuality(
        mechanical_passed=True,
        source_coverage=1.0,
        anchor_recall=1.0,
        unsupported_critical_claims=0,
        coverage_gap=0,
    )


def capsule(
    capsule_id: str = "capsule-1",
    *,
    key: ac.SessionKey | None = None,
    start: int = 1,
    end: int = 1,
    source_event_ids: tuple[str, ...] | None = None,
    goals: tuple[ac.CapsuleClaim, ...] | None = None,
    anchors: tuple[ac.CapsuleAnchor, ...] | None = None,
    token_cost: int = 3,
    narrative_summary: str | None = None,
) -> ac.ContextCapsuleEnvelope:
    sources = source_event_ids if source_event_ids is not None else (f"event-{start}",)
    return ac.ContextCapsuleEnvelope(
        capsule_id=capsule_id,
        schema_version="1.0.0",
        level=ac.CapsuleLevel.MICRO,
        session_key=key if key is not None else session_key(),
        covered_event_start=start,
        covered_event_end=end,
        source_event_ids=sources,
        goals=goals if goals is not None else (claim(f"goal-{capsule_id}", sources[0]),),
        constraints=(),
        decisions=(),
        progress=(),
        open_loops=(),
        preferences=(),
        entities=(),
        emotional_context=(),
        exact_anchors=anchors
        if anchors is not None
        else (anchor(f"anchor-{capsule_id}", sources[0]),),
        dependencies=(),
        narrative_summary=narrative_summary
        if narrative_summary is not None
        else f"capsule {capsule_id}",
        token_cost=token_cost,
        quality=quality(),
        created_at=NOW,
    )


def snapshot(
    capsules: tuple[ac.ContextCapsuleEnvelope, ...],
    *,
    snapshot_id: str = "snapshot-base",
    key: ac.SessionKey | None = None,
    coverage: int = 1,
    base_snapshot_id: str | None = None,
    exact_anchor_ids: tuple[str, ...] | None = None,
    state: ac.SnapshotState = ac.SnapshotState.COMMITTED,
) -> ac.SnapshotEnvelope:
    anchors = (
        exact_anchor_ids
        if exact_anchor_ids is not None
        else tuple(
            item.anchor_id
            for item in capsules
            for item in item.exact_anchors
            if item.status is ac.AnchorStatus.ACTIVE
        )
    )
    return ac.SnapshotEnvelope(
        snapshot_id=snapshot_id,
        session_key=key if key is not None else session_key(),
        base_snapshot_id=base_snapshot_id,
        covered_event_end=coverage,
        source_high_water_mark=coverage,
        capsule_ids=tuple(item.capsule_id for item in capsules),
        exact_anchor_ids=anchors,
        rendered_context=f"context {snapshot_id}",
        token_cost=sum(item.token_cost for item in capsules),
        audit_outcome=ac.SnapshotAuditOutcome(
            mechanical_passed=True,
            semantic_status=ac.SemanticAuditStatus.NOT_RUN,
            failure_codes=(),
        ),
        state=state,
        created_at=NOW,
        committed_at=NOW if state is ac.SnapshotState.COMMITTED else None,
    )


def compiler_output(
    capsules: object,
    rendered_context: object = "compiled context",
) -> Any:
    output_type = compiler_surface()["CompilerOutput"]
    return output_type(capsules=capsules, rendered_context=rendered_context)


class RecordingBackend:
    def __init__(self, output: object = None, *, error: Exception | None = None) -> None:
        self.output = output
        self.error = error
        self.requests: list[Any] = []

    async def compile(self, request: Any) -> Any:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.output


class RecordingCounter:
    def __init__(self, result: object = 7, *, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.texts: list[str] = []

    def count_text(self, text: str) -> Any:
        self.texts.append(text)
        if self.error is not None:
            raise self.error
        return self.result


async def compile_with(
    *,
    source_events: tuple[ac.EventEnvelope, ...],
    backend: RecordingBackend,
    counter: RecordingCounter,
    target: object,
    base_snapshot: ac.SnapshotEnvelope | None = None,
    base_capsules: tuple[ac.ContextCapsuleEnvelope, ...] = (),
    token_ceiling: object = 100,
    now: object = NOW,
    segmenter_config: ac.SegmenterConfig | None = None,
    preferred_end_sequences: tuple[int, ...] = (),
) -> Any:
    compile_candidate = compiler_surface()["compile_candidate"]
    return await compile_candidate(
        base_snapshot=base_snapshot,
        base_capsules=base_capsules,
        source_events=source_events,
        target_high_water_mark=target,
        token_ceiling=token_ceiling,
        backend=backend,
        counter=counter,
        now=now,
        segmenter_config=segmenter_config if segmenter_config is not None else ac.SegmenterConfig(),
        preferred_end_sequences=preferred_end_sequences,
    )


def assert_stable_error(error: Any, expected_name: str) -> None:
    surface = compiler_surface()
    expected_code = getattr(surface["CompilerErrorCode"], expected_name)
    assert error.code is expected_code
    assert str(error).split("|", maxsplit=1)[0] == expected_code.value
    assert SECRET not in str(error)


def test_compiler_is_exported_on_both_canonical_surfaces() -> None:
    root_surface = compiler_surface()
    compaction = importlib.import_module("astrcontinuum.compaction")

    assert {name: getattr(compaction, name) for name in COMPILER_SURFACE_NAMES} == root_surface
    assert getattr(root_surface["CompilerBackend"], "_is_protocol", False)


@pytest.mark.asyncio
async def test_bootstrap_compiles_complete_request_into_frozen_candidate() -> None:
    events = (event(1), event(2))
    output_capsule = capsule(
        source_event_ids=("event-1", "event-2"),
        end=2,
        goals=(claim("goal-current", "event-2"),),
        anchors=(anchor("anchor-current", "event-1"),),
    )
    output = compiler_output((output_capsule,), "rendered canonical context")
    backend = RecordingBackend(output)
    counter = RecordingCounter(9)

    candidate = await compile_with(
        source_events=events,
        backend=backend,
        counter=counter,
        target=2,
        segmenter_config=ac.SegmenterConfig(
            max_events_per_segment=1,
            max_tokens_per_segment=100,
        ),
        preferred_end_sequences=(1,),
    )

    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert request.base_snapshot is None
    assert request.base_capsules == ()
    assert all(
        actual is expected for actual, expected in zip(request.source_events, events, strict=True)
    )
    assert tuple(
        tuple(item.sequence for item in segment.events) for segment in request.segments
    ) == ((1,), (2,))
    assert request.target_high_water_mark == 2
    assert request.token_ceiling == 100
    assert counter.texts == ["rendered canonical context"]

    assert candidate.snapshot.session_key == session_key()
    assert candidate.snapshot.base_snapshot_id is None
    assert candidate.snapshot.covered_event_end == 2
    assert candidate.snapshot.source_high_water_mark == 2
    assert candidate.snapshot.capsule_ids == ("capsule-1",)
    assert candidate.snapshot.exact_anchor_ids == ("anchor-current",)
    assert candidate.snapshot.rendered_context == "rendered canonical context"
    assert candidate.snapshot.token_cost == 9
    assert candidate.snapshot.audit_outcome.mechanical_passed
    assert candidate.snapshot.audit_outcome.semantic_status is ac.SemanticAuditStatus.NOT_RUN
    assert candidate.snapshot.audit_outcome.failure_codes == ()
    assert candidate.snapshot.state is ac.SnapshotState.CANDIDATE
    assert candidate.snapshot.created_at == NOW
    assert candidate.snapshot.committed_at is None
    assert len(candidate.snapshot.snapshot_id) == 64
    int(candidate.snapshot.snapshot_id, 16)
    assert tuple(item.ordinal for item in candidate.memberships) == (0,)
    assert tuple(item.slot for item in candidate.memberships) == ("memory",)
    assert candidate.memberships[0].capsule is output_capsule
    assert candidate.segments == request.segments
    assert candidate.permanent_report.passed
    assert candidate.permanent_report.failure_codes == ()

    assert is_dataclass(request)
    assert is_dataclass(output)
    assert is_dataclass(candidate)
    with pytest.raises(FrozenInstanceError):
        request.token_ceiling = 0
    with pytest.raises(FrozenInstanceError):
        output.rendered_context = "changed"
    with pytest.raises(FrozenInstanceError):
        candidate.segments = ()


@pytest.mark.asyncio
async def test_committed_base_and_complete_capsule_order_reach_backend_unchanged() -> None:
    base_a = capsule("base-a", start=1, end=1)
    base_b = capsule("base-b", start=2, end=2)
    base_capsules = (base_a, base_b)
    base = snapshot(base_capsules, coverage=2)
    events = (event(3), event(4))
    delta_capsule = capsule(
        "delta",
        start=3,
        end=4,
        source_event_ids=("event-3", "event-4"),
    )
    complete_output = (base_a, base_b, delta_capsule)
    backend = RecordingBackend(compiler_output(complete_output, "complete candidate"))
    counter = RecordingCounter(11)

    candidate = await compile_with(
        base_snapshot=base,
        base_capsules=base_capsules,
        source_events=events,
        backend=backend,
        counter=counter,
        target=4,
    )

    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert request.base_snapshot is base
    assert request.base_capsules == base_capsules
    assert all(
        actual is expected
        for actual, expected in zip(request.base_capsules, base_capsules, strict=True)
    )
    assert request.source_events == events
    assert candidate.snapshot.base_snapshot_id == base.snapshot_id
    assert candidate.snapshot.capsule_ids == ("base-a", "base-b", "delta")
    assert tuple(item.ordinal for item in candidate.memberships) == (0, 1, 2)
    assert tuple(item.capsule for item in candidate.memberships) == complete_output
    assert all(item.slot == "memory" for item in candidate.memberships)
    assert candidate.permanent_report.passed


@pytest.mark.asyncio
async def test_snapshot_identity_is_stable_across_now_and_sensitive_to_owned_content() -> None:
    events = (event(1),)
    item = capsule()
    first_backend = RecordingBackend(compiler_output((item,), "stable context"))
    second_backend = RecordingBackend(compiler_output((item,), "stable context"))
    changed_backend = RecordingBackend(compiler_output((item,), "changed context"))

    first = await compile_with(
        source_events=events,
        backend=first_backend,
        counter=RecordingCounter(7),
        target=1,
        now=NOW,
    )
    second = await compile_with(
        source_events=events,
        backend=second_backend,
        counter=RecordingCounter(7),
        target=1,
        now=LATER,
    )
    changed = await compile_with(
        source_events=events,
        backend=changed_backend,
        counter=RecordingCounter(7),
        target=1,
        now=NOW,
    )

    assert first.snapshot.snapshot_id == second.snapshot.snapshot_id
    assert first.snapshot.created_at != second.snapshot.created_at
    assert changed.snapshot.snapshot_id != first.snapshot.snapshot_id


def test_validate_candidate_delegates_to_domain_validator_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = compiler_surface()
    validate_candidate = surface["validate_candidate"]
    domain = importlib.import_module("astrcontinuum.domain")
    item = capsule()
    candidate_snapshot = snapshot((item,), state=ac.SnapshotState.CANDIDATE)
    events = (event(1),)
    expected_report = ac.PermanentValidationReport(
        passed=True,
        failure_codes=(),
        source_coverage=1.0,
        anchor_recall=1.0,
        coverage_gap=0,
        unsupported_critical_claims=0,
    )
    calls: list[dict[str, Any]] = []

    def recording_validator(**kwargs: Any) -> ac.PermanentValidationReport:
        calls.append(kwargs)
        return expected_report

    monkeypatch.setattr(domain, "validate_permanent", recording_validator)

    report = validate_candidate(
        base_snapshot=None,
        base_capsules=(),
        candidate_snapshot=candidate_snapshot,
        candidate_capsules=(item,),
        source_events=events,
        target_high_water_mark=1,
        token_ceiling=100,
    )

    assert report is expected_report
    assert len(calls) == 1
    assert calls[0] == {
        "previous_snapshot": None,
        "previous_capsules": (),
        "candidate_snapshot": candidate_snapshot,
        "candidate_capsules": (item,),
        "source_events": events,
        "target_high_water_mark": 1,
        "token_ceiling": 100,
    }


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("bootstrap_capsules", "BASE_CAPSULE_MISMATCH"),
        ("missing_capsules", "BASE_CAPSULE_MISMATCH"),
        ("capsule_order", "BASE_CAPSULE_MISMATCH"),
        ("capsule_session", "BASE_CAPSULE_MISMATCH"),
        ("anchor_alignment", "BASE_CAPSULE_MISMATCH"),
        ("candidate_base", "BASE_INVALID"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_base_is_rejected_before_backend(
    case: str,
    expected_code: str,
) -> None:
    base_a = capsule("base-a", start=1, end=1)
    base_b = capsule("base-b", start=1, end=1)
    base_capsules = (base_a, base_b)
    base: ac.SnapshotEnvelope | None = snapshot(base_capsules, coverage=1)
    supplied_capsules = base_capsules
    events = (event(2, content=SECRET),)
    target = 2

    if case == "bootstrap_capsules":
        base = None
        supplied_capsules = (base_a,)
        events = (event(1, content=SECRET),)
        target = 1
    elif case == "missing_capsules":
        supplied_capsules = ()
    elif case == "capsule_order":
        supplied_capsules = (base_b, base_a)
    elif case == "capsule_session":
        supplied_capsules = (
            capsule("base-a", key=session_key("other"), start=1, end=1),
            base_b,
        )
    elif case == "anchor_alignment":
        base = snapshot(base_capsules, coverage=1, exact_anchor_ids=("wrong-anchor",))
    elif case == "candidate_base":
        base = snapshot(base_capsules, coverage=1, state=ac.SnapshotState.CANDIDATE)

    backend = RecordingBackend(object())
    counter = RecordingCounter()
    error_type = compiler_surface()["CompilerInvariantError"]

    with pytest.raises(error_type) as caught:
        await compile_with(
            base_snapshot=base,
            base_capsules=supplied_capsules,
            source_events=events,
            backend=backend,
            counter=counter,
            target=target,
        )

    assert_stable_error(caught.value, expected_code)
    assert backend.requests == []
    assert counter.texts == []


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("wrong_first", "SOURCE_COVERAGE_MISMATCH"),
        ("wrong_last", "SOURCE_COVERAGE_MISMATCH"),
        ("missing_middle", "SOURCE_COVERAGE_MISMATCH"),
        ("mixed_session", "SOURCE_SESSION_MISMATCH"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_delta_is_rejected_before_backend(
    case: str,
    expected_code: str,
) -> None:
    key = session_key()
    events = (event(1, key=key, content=SECRET), event(2, key=key))
    target = 2
    if case == "wrong_first":
        events = (event(2, key=key, content=SECRET), event(3, key=key))
        target = 3
    elif case == "wrong_last":
        target = 3
    elif case == "missing_middle":
        events = (event(1, key=key, content=SECRET), event(3, key=key))
        target = 3
    elif case == "mixed_session":
        events = (
            event(1, key=key, content=SECRET),
            event(2, key=session_key("other")),
        )

    backend = RecordingBackend(object())
    counter = RecordingCounter()
    error_type = compiler_surface()["CompilerInvariantError"]

    with pytest.raises(error_type) as caught:
        await compile_with(
            source_events=events,
            backend=backend,
            counter=counter,
            target=target,
        )

    assert_stable_error(caught.value, expected_code)
    assert backend.requests == []
    assert counter.texts == []


@pytest.mark.parametrize("target", [True, 0, -1, 1.0, "1"])
@pytest.mark.asyncio
async def test_invalid_target_is_rejected_before_backend(target: object) -> None:
    backend = RecordingBackend(object())
    counter = RecordingCounter()
    error_type = compiler_surface()["CompilerInvariantError"]

    with pytest.raises(error_type) as caught:
        await compile_with(
            source_events=(event(1, content=SECRET),),
            backend=backend,
            counter=counter,
            target=target,
        )

    assert_stable_error(caught.value, "TARGET_INVALID")
    assert backend.requests == []
    assert counter.texts == []


@pytest.mark.parametrize("token_ceiling", [True, -1, 1.0, "100"])
@pytest.mark.asyncio
async def test_invalid_token_ceiling_is_rejected_before_backend(
    token_ceiling: object,
) -> None:
    backend = RecordingBackend(object())
    counter = RecordingCounter()
    error_type = compiler_surface()["CompilerInvariantError"]

    with pytest.raises(error_type) as caught:
        await compile_with(
            source_events=(event(1, content=SECRET),),
            backend=backend,
            counter=counter,
            target=1,
            token_ceiling=token_ceiling,
        )

    assert_stable_error(caught.value, "TOKEN_CEILING_INVALID")
    assert backend.requests == []
    assert counter.texts == []


@pytest.mark.parametrize("now", [NOW.replace(tzinfo=None), "2026-07-26T12:00:00Z"])
@pytest.mark.asyncio
async def test_invalid_or_naive_now_is_rejected_before_backend(now: object) -> None:
    backend = RecordingBackend(object())
    counter = RecordingCounter()
    error_type = compiler_surface()["CompilerInvariantError"]

    with pytest.raises(error_type) as caught:
        await compile_with(
            source_events=(event(1, content=SECRET),),
            backend=backend,
            counter=counter,
            target=1,
            now=now,
        )

    assert_stable_error(caught.value, "NOW_INVALID")
    assert backend.requests == []
    assert counter.texts == []


@pytest.mark.asyncio
async def test_backend_exception_is_called_once_and_redacted() -> None:
    backend = RecordingBackend(error=RuntimeError(f"provider failed: {SECRET}"))
    counter = RecordingCounter()
    error_type = compiler_surface()["CompilerInvariantError"]

    with pytest.raises(error_type) as caught:
        await compile_with(
            source_events=(event(1, content=SECRET),),
            backend=backend,
            counter=counter,
            target=1,
        )

    assert_stable_error(caught.value, "BACKEND_FAILURE")
    assert len(backend.requests) == 1
    assert counter.texts == []


@pytest.mark.parametrize("case", ["wrong_type", "capsule_list", "bad_capsule", "bad_context"])
@pytest.mark.asyncio
async def test_invalid_backend_output_is_rejected_before_counting(case: str) -> None:
    item = capsule(narrative_summary=SECRET)
    if case == "wrong_type":
        output: object = object()
    elif case == "capsule_list":
        output = compiler_output([item], SECRET)
    elif case == "bad_capsule":
        output = compiler_output((object(),), SECRET)
    else:
        output = compiler_output((item,), 42)
    backend = RecordingBackend(output)
    counter = RecordingCounter()
    error_type = compiler_surface()["CompilerInvariantError"]

    with pytest.raises(error_type) as caught:
        await compile_with(
            source_events=(event(1, content=SECRET),),
            backend=backend,
            counter=counter,
            target=1,
        )

    assert_stable_error(caught.value, "BACKEND_OUTPUT_INVALID")
    assert len(backend.requests) == 1
    assert counter.texts == []


@pytest.mark.parametrize("case", ["capsules", "context"])
@pytest.mark.asyncio
async def test_empty_backend_output_is_rejected_before_counting(case: str) -> None:
    item = capsule(narrative_summary=SECRET)
    output = compiler_output((), SECRET) if case == "capsules" else compiler_output((item,), "")
    backend = RecordingBackend(output)
    counter = RecordingCounter()
    error_type = compiler_surface()["CompilerInvariantError"]

    with pytest.raises(error_type) as caught:
        await compile_with(
            source_events=(event(1, content=SECRET),),
            backend=backend,
            counter=counter,
            target=1,
        )

    assert_stable_error(caught.value, "BACKEND_OUTPUT_EMPTY")
    assert len(backend.requests) == 1
    assert counter.texts == []


@pytest.mark.parametrize("counter_result", [True, -1, 1.0, "7", None])
@pytest.mark.asyncio
async def test_invalid_counter_result_is_rejected_after_one_call(
    counter_result: object,
) -> None:
    item = capsule(narrative_summary=SECRET)
    backend = RecordingBackend(compiler_output((item,), SECRET))
    counter = RecordingCounter(counter_result)
    error_type = compiler_surface()["CompilerInvariantError"]

    with pytest.raises(error_type) as caught:
        await compile_with(
            source_events=(event(1, content=SECRET),),
            backend=backend,
            counter=counter,
            target=1,
        )

    assert_stable_error(caught.value, "TOKEN_COUNTER_INVALID")
    assert len(backend.requests) == 1
    assert counter.texts == [SECRET]


@pytest.mark.asyncio
async def test_counter_exception_is_called_once_and_redacted() -> None:
    item = capsule(narrative_summary=SECRET)
    backend = RecordingBackend(compiler_output((item,), SECRET))
    counter = RecordingCounter(error=RuntimeError(f"counter failed: {SECRET}"))
    error_type = compiler_surface()["CompilerInvariantError"]

    with pytest.raises(error_type) as caught:
        await compile_with(
            source_events=(event(1, content=SECRET),),
            backend=backend,
            counter=counter,
            target=1,
        )

    assert_stable_error(caught.value, "TOKEN_COUNTER_FAILURE")
    assert len(backend.requests) == 1
    assert counter.texts == [SECRET]


@pytest.mark.asyncio
async def test_dropped_prior_semantic_is_rejected_after_permanent_validation() -> None:
    base_capsule = capsule(
        "base",
        goals=(claim("goal-prior", "event-1"),),
        anchors=(anchor("anchor-prior", "event-1"),),
        narrative_summary=SECRET,
    )
    base = snapshot((base_capsule,), coverage=1)
    replacement = capsule(
        "replacement",
        start=1,
        end=2,
        source_event_ids=("event-1", "event-2"),
        goals=(claim("goal-new", "event-2"),),
        anchors=(anchor("anchor-prior", "event-1"),),
        narrative_summary=SECRET,
    )
    backend = RecordingBackend(compiler_output((replacement,), SECRET))
    counter = RecordingCounter(5)
    error_type = compiler_surface()["CompilerInvariantError"]

    with pytest.raises(error_type) as caught:
        await compile_with(
            base_snapshot=base,
            base_capsules=(base_capsule,),
            source_events=(event(2, content=SECRET),),
            backend=backend,
            counter=counter,
            target=2,
        )

    assert_stable_error(caught.value, "PERMANENT_VALIDATION_FAILED")
    assert ac.PermanentFailureCode.MISSING_PRIOR_SEMANTIC in caught.value.report.failure_codes
    assert len(backend.requests) == 1
    assert counter.texts == [SECRET]


@pytest.mark.asyncio
async def test_dropped_prior_anchor_is_rejected_after_permanent_validation() -> None:
    base_capsule = capsule(
        "base",
        goals=(claim("goal-prior", "event-1"),),
        anchors=(anchor("anchor-prior", "event-1"),),
        narrative_summary=SECRET,
    )
    base = snapshot((base_capsule,), coverage=1)
    replacement = capsule(
        "replacement",
        start=1,
        end=2,
        source_event_ids=("event-1", "event-2"),
        goals=(claim("goal-prior", "event-1"),),
        anchors=(anchor("anchor-new", "event-2"),),
        narrative_summary=SECRET,
    )
    backend = RecordingBackend(compiler_output((replacement,), SECRET))
    counter = RecordingCounter(5)
    error_type = compiler_surface()["CompilerInvariantError"]

    with pytest.raises(error_type) as caught:
        await compile_with(
            base_snapshot=base,
            base_capsules=(base_capsule,),
            source_events=(event(2, content=SECRET),),
            backend=backend,
            counter=counter,
            target=2,
        )

    assert_stable_error(caught.value, "PERMANENT_VALIDATION_FAILED")
    assert ac.PermanentFailureCode.MISSING_REQUIRED_ANCHOR in caught.value.report.failure_codes
    assert len(backend.requests) == 1
    assert counter.texts == [SECRET]


@pytest.mark.asyncio
async def test_rendered_context_token_overflow_is_permanently_rejected() -> None:
    item = capsule(narrative_summary=SECRET, token_cost=3)
    backend = RecordingBackend(compiler_output((item,), SECRET))
    counter = RecordingCounter(6)
    error_type = compiler_surface()["CompilerInvariantError"]

    with pytest.raises(error_type) as caught:
        await compile_with(
            source_events=(event(1, content=SECRET),),
            backend=backend,
            counter=counter,
            target=1,
            token_ceiling=5,
        )

    assert_stable_error(caught.value, "PERMANENT_VALIDATION_FAILED")
    assert ac.PermanentFailureCode.TOKEN_CEILING_EXCEEDED in caught.value.report.failure_codes
    assert len(backend.requests) == 1
    assert counter.texts == [SECRET]
