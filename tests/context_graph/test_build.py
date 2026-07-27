from __future__ import annotations

from dataclasses import replace

from astrcontinuum.context_graph import ContextGraphConfig
from astrcontinuum.context_graph.build import build_context_graph
from astrcontinuum.domain import SessionKey
from astrcontinuum.runtime.types import CandidateKind, RuntimeSlot
from astrcontinuum.storage import RequestView
from tests.context_graph.helpers import candidate


def request_view() -> RequestView:
    return RequestView(
        session_key=SessionKey(
            platform_instance_id="platform",
            message_type="friend",
            session_id="session",
            group_id=None,
            user_id="user",
            conversation_id="conversation",
            persona_id=None,
        ),
        snapshot=None,
        memberships=(),
        pointer_version=0,
        covered_event_end=0,
        high_water_mark=0,
        delta=(),
    )


def test_build_is_deterministic_bounded_and_uses_existing_candidate_facts() -> None:
    required = replace(
        candidate("required", required=True, source_event_ids=("event-1",), text="hard rule"),
        kind=CandidateKind.CONSTRAINT,
        slot=RuntimeSlot.HARD_CONSTRAINT,
        capsule_id="capsule-1",
    )
    relevant = replace(
        candidate("relevant", source_event_ids=("event-1",), text="alpha evidence"),
        capsule_id="capsule-1",
    )
    adjacent = replace(
        candidate("adjacent", source_event_ids=("event-2",), text="other"),
        kind=CandidateKind.RAW_EVENT,
        slot=RuntimeSlot.RECENT_RAW,
        event_sequence=2,
    )
    candidates = (required, relevant, adjacent)

    first = build_context_graph(
        request_view(),
        candidates,
        "ALPHA",
        config=ContextGraphConfig(),
    )
    second = build_context_graph(
        request_view(),
        candidates,
        "ALPHA",
        config=ContextGraphConfig(),
    )

    assert first.graph == second.graph
    assert first.activation == second.activation
    assert tuple(item.coordinate_id for item in first.graph.coordinates) == (
        "required",
        "relevant",
        "adjacent",
    )
    assert tuple(item.constraint_id for item in first.graph.constraints) == ("required:required",)
    assert len(first.graph.relations) >= 1
    assert dict(first.activation.scores)["relevant"] > dict(first.activation.scores)["adjacent"]
    assert first.activation.provenance_event_ids == ("event-1", "event-2")
    assert "required" in first.activation.retained_coordinate_ids
    assert "relevant" in first.activation.retained_coordinate_ids
