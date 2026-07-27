from __future__ import annotations

from dataclasses import replace

import pytest

import astrcontinuum.context_graph.candidate_verification as verification_module
from astrcontinuum.compaction.types import AuditedCandidate
from astrcontinuum.context_graph.candidate_verification import (
    CandidateVerificationError,
    CandidateVerificationErrorCode,
    build_candidate_blocks,
    verify_candidate,
)
from astrcontinuum.domain import (
    CapsuleClaim,
    CapsuleLevel,
    Decision,
    Dependency,
    Entity,
    SemanticStatus,
)
from astrcontinuum.runtime.types import CandidateKind, RetrievalConfig
from tests.test_semantic_auditor import SECRET, compilation_candidate


def audited_candidate() -> AuditedCandidate:
    candidate = compilation_candidate()
    return AuditedCandidate(
        snapshot=candidate.snapshot,
        memberships=candidate.memberships,
        segments=candidate.segments,
        permanent_report=candidate.permanent_report,
        semantic_report=None,
    )


def assert_stable_rejection(error: CandidateVerificationError) -> None:
    assert error.code is CandidateVerificationErrorCode.REJECTED
    assert str(error) == "CANDIDATE_GRAPH_VERIFICATION_FAILED"
    assert SECRET not in str(error)


def test_verified_candidate_uses_fixed_content_free_probes_deterministically() -> None:
    candidate = audited_candidate()

    first = verify_candidate(candidate)
    second = verify_candidate(candidate)
    blocks = build_candidate_blocks(candidate)

    assert first == second
    assert first.passed is True
    assert first.probe_ids == ("required", "evidence", "recent")
    assert first.probe_count == 3
    assert first.candidate_count == len(blocks)
    assert all(not block.block_id.startswith("background:") for block in blocks)
    assert SECRET not in repr(first)


def test_fixed_probes_cover_structured_optional_kinds_and_reduction() -> None:
    candidate = audited_candidate()
    membership = candidate.memberships[0]
    capsule = membership.capsule
    source_event_id = capsule.source_event_ids[0]

    def claim(claim_id: str, text: str) -> CapsuleClaim:
        return CapsuleClaim(
            claim_id=claim_id,
            text=text,
            status=SemanticStatus.ACTIVE,
            confidence=1.0,
            source_event_ids=(source_event_id,),
        )

    rich_capsule = capsule.model_copy(
        update={
            "level": CapsuleLevel.TASK,
            "constraints": (claim("constraint-rich", "must hold"),),
            "progress": (claim("progress-rich", "in progress"),),
            "decisions": (
                Decision(
                    decision_id="decision-rich",
                    text="choose rich",
                    status=SemanticStatus.ACTIVE,
                    confidence=1.0,
                    source_event_ids=(source_event_id,),
                    rationale="exact rationale",
                    alternatives=("other",),
                    supersedes=(),
                    rejected_because="constraint",
                ),
            ),
            "entities": (
                Entity(
                    entity_id="entity-rich",
                    kind="component",
                    canonical_name="Rich",
                    aliases=("R",),
                    source_event_ids=(source_event_id,),
                ),
            ),
            "dependencies": (
                Dependency(
                    dependency_id="dependency-rich",
                    kind="requires",
                    target_id="entity-rich",
                    source_event_ids=(source_event_id,),
                ),
            ),
        }
    )
    rich = replace(
        candidate,
        memberships=(
            replace(membership, capsule=rich_capsule),
            *candidate.memberships[1:],
        ),
    )

    report = verify_candidate(rich)
    kinds = {block.kind for block in build_candidate_blocks(rich)}

    assert report.passed is True
    assert {
        CandidateKind.CONSTRAINT,
        CandidateKind.TASK_CONTEXT,
        CandidateKind.DECISION,
        CandidateKind.DEPENDENCY,
        CandidateKind.ENTITY,
        CandidateKind.RAW_EVENT,
    }.issubset(kinds)


def test_candidate_verification_rejects_forged_provenance_with_one_stable_code() -> None:
    candidate = audited_candidate()
    membership = candidate.memberships[0]
    goal = membership.capsule.goals[0].model_copy(update={"source_event_ids": ("forged-event",)})
    capsule = membership.capsule.model_copy(update={"goals": (goal,)})
    forged = replace(
        candidate,
        memberships=(
            replace(membership, capsule=capsule),
            *candidate.memberships[1:],
        ),
    )

    with pytest.raises(CandidateVerificationError) as captured:
        verify_candidate(forged)

    assert_stable_rejection(captured.value)


def test_candidate_verification_rejects_required_capacity_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = build_candidate_blocks
    monkeypatch.setattr(
        verification_module,
        "build_candidate_blocks",
        lambda candidate: original(
            candidate,
            retrieval_config=RetrievalConfig(max_candidates=1),
        ),
    )

    with pytest.raises(CandidateVerificationError) as captured:
        verify_candidate(audited_candidate())

    assert_stable_rejection(captured.value)


@pytest.mark.parametrize("fault", ("graph", "closure", "equivalence"))
def test_candidate_verification_redacts_all_graph_gate_failures(
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    if fault == "graph":
        monkeypatch.setattr(
            verification_module,
            "build_context_graph",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(SECRET)),
        )
    elif fault == "closure":
        monkeypatch.setattr(
            verification_module,
            "validate_dependency_closure",
            lambda *_args, **_kwargs: False,
        )
    else:
        monkeypatch.setattr(
            verification_module,
            "_probe_equivalent",
            lambda *_args, **_kwargs: False,
        )

    with pytest.raises(CandidateVerificationError) as captured:
        verify_candidate(audited_candidate())

    assert_stable_rejection(captured.value)
