"""Pure bounded reachability for consecutive V21 evidenced reencodings.

The evaluator accepts only materialised V21 state, head, envelope, and policy
objects.  It delegates each reachable round to the V21-006 planner exactly
once, then independently validates the planner's returned verified transition
before allowing the next round to observe it.  The returned report retains
only roots and compact integer accounting -- never source bodies, envelopes,
candidates, releases, heads, or states.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from typing import Final, NoReturn

from crm_experiment.contracts_v21 import canonical_json_v21
from crm_experiment.evidenced_planner_v21 import (
    EvidencedPlannedCandidateV21,
    EvidencedPlanningRejectedV21,
    plan_evidenced_reencoding_v21,
)
from crm_experiment.evidenced_reencoder_v21 import (
    EvidencedReencodingCandidateV21,
    EvidencedReencodingResultV21,
    EvidencedReencodingStatusV21,
)
from crm_experiment.evidenced_state_v21 import (
    ContributionSupportV21,
    EvidencedCapsuleStateV21,
    evidenced_resident_layout_v21,
    evidenced_state_hash_v21,
    validate_evidenced_capsule_state_v21,
)
from crm_experiment.matrix_v21 import (
    EvidencedMatrixPlanV21,
    MatrixChildAxisV21,
    MatrixChildKindV21,
    MatrixDecisionAxisV21,
    MatrixDecisionXV21,
    MatrixDirectedEdgeV21,
    MatrixParentChildEdgeV21,
    MatrixSegmentAxisV21,
    MatrixSourceAxisV21,
    SparseBinaryCellV21,
)
from crm_experiment.reencoding_contracts_v21 import (
    ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
    EVIDENCED_POLICY_ROOT_DOMAIN_V21,
    EVIDENCED_STATE_HASH_DOMAIN_V21,
    EVIDENCED_TRANSITION_ROOT_DOMAIN_V21,
    MATRIX_ROOT_DOMAIN_V21,
    SOURCE_ENVELOPE_ROOT_DOMAIN_V21,
    AdvanceChainHeadV21,
    EvidencedReencodingPolicyV21,
    FoldContributionEvidenceV21,
    RecordDecisionV21,
    ReencodingOutcomeV21,
    SegmentDecisionV21,
    SegmentDispositionV21,
    SourceEnvelopeV21,
    advance_chain_head_root_v21,
    canonical_segment_hash_v21,
    evidenced_reencoding_policy_root_v21,
    evidenced_transition_root_v21,
    validate_advance_chain_head_v21,
    validate_barrier_advance_v21,
    validate_evidenced_reencoding_policy_v21,
    validate_first_fold_authorization_v21,
    validate_loss_ledger_advance_v21,
    validate_source_envelope_v21,
)

MAX_MULTIROUNDS_V21: Final = 12
MULTIROUND_RUN_ROOT_DOMAIN_V21: Final = "crm-v21-multiround-run-s3/v1"


class MultiroundStatusV21(StrEnum):
    """Closed terminal status for one bounded multi-round evaluation."""

    COMPLETED = "COMPLETED"
    STALE = "STALE"
    REJECTED = "REJECTED"
    POSTCONDITION_REJECTED = "POSTCONDITION_REJECTED"


class MultiroundExitReasonV21(StrEnum):
    """Closed, body-free reason codes used in reports and run-root binding."""

    COMPLETED = "COMPLETED"
    ADVANCED = "ADVANCED"
    STALE_INITIAL_ANCHOR = "STALE_INITIAL_ANCHOR"
    STALE_ROUND_ANCHOR = "STALE_ROUND_ANCHOR"
    PLANNER_REJECTED = "PLANNER_REJECTED"
    POSTCONDITION_REJECTED = "POSTCONDITION_REJECTED"


_EXIT_REASON_TEXT_V21: Final = {
    MultiroundExitReasonV21.STALE_INITIAL_ANCHOR: "initial anchor mismatch",
    MultiroundExitReasonV21.STALE_ROUND_ANCHOR: "round anchor mismatch",
    MultiroundExitReasonV21.PLANNER_REJECTED: "planner rejected",
    MultiroundExitReasonV21.POSTCONDITION_REJECTED: "postcondition rejected",
}
_STATUS_BY_EXIT_REASON_V21: Final = {
    MultiroundExitReasonV21.COMPLETED: MultiroundStatusV21.COMPLETED,
    MultiroundExitReasonV21.STALE_INITIAL_ANCHOR: MultiroundStatusV21.STALE,
    MultiroundExitReasonV21.STALE_ROUND_ANCHOR: MultiroundStatusV21.STALE,
    MultiroundExitReasonV21.PLANNER_REJECTED: MultiroundStatusV21.REJECTED,
    MultiroundExitReasonV21.POSTCONDITION_REJECTED: (
        MultiroundStatusV21.POSTCONDITION_REJECTED
    ),
}


class _StaleInputV21(ValueError):
    """A valid, materialised input no longer names the current predecessor."""


class _PostconditionRejectedV21(ValueError):
    """A returned planner value is not a trustworthy verified successor."""


def _reject_postcondition_v21(message: str) -> NoReturn:
    raise _PostconditionRejectedV21(message)


def _require_domain_digest_v21(value: object, domain: str, label: str) -> str:
    if (
        type(value) is not str
        or re.fullmatch(rf"{re.escape(domain)}:[0-9a-f]{{64}}", value) is None
    ):
        raise ValueError(f"{label} must be a {domain} domain digest")
    return value


def _require_nonnegative_int_v21(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative plain integer")
    return value


def _require_int_v21(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be a plain integer")
    return value


@dataclass(frozen=True, slots=True)
class RoundInputV21:
    """One fully concrete, externally anchored V21 round request."""

    round_index: int
    source_envelope: SourceEnvelopeV21
    policy: EvidencedReencodingPolicyV21
    expected_base_state_hash: str
    expected_source_envelope_root: str
    expected_policy_root: str
    expected_advance_head_root: str

    def __post_init__(self) -> None:
        validate_round_input_v21(self)


def validate_round_input_v21(round_input: RoundInputV21) -> None:
    """Revalidate a concrete request, including nested frozen objects."""
    if type(round_input) is not RoundInputV21:
        raise TypeError("multi-round inputs require nominal RoundInputV21")
    _require_nonnegative_int_v21(round_input.round_index, "round_index")
    if type(round_input.source_envelope) is not SourceEnvelopeV21:
        raise TypeError("round source_envelope must be nominal SourceEnvelopeV21")
    validate_source_envelope_v21(round_input.source_envelope)
    if type(round_input.policy) is not EvidencedReencodingPolicyV21:
        raise TypeError("round policy must be nominal EvidencedReencodingPolicyV21")
    validate_evidenced_reencoding_policy_v21(round_input.policy)
    _require_domain_digest_v21(
        round_input.expected_base_state_hash,
        EVIDENCED_STATE_HASH_DOMAIN_V21,
        "round expected_base_state_hash",
    )
    expected_envelope_root = _require_domain_digest_v21(
        round_input.expected_source_envelope_root,
        SOURCE_ENVELOPE_ROOT_DOMAIN_V21,
        "round expected_source_envelope_root",
    )
    expected_policy_root = _require_domain_digest_v21(
        round_input.expected_policy_root,
        EVIDENCED_POLICY_ROOT_DOMAIN_V21,
        "round expected_policy_root",
    )
    _require_domain_digest_v21(
        round_input.expected_advance_head_root,
        ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
        "round expected_advance_head_root",
    )
    if not hmac.compare_digest(
        round_input.source_envelope.envelope_root, expected_envelope_root
    ):
        raise ValueError("round source envelope does not match expected root")
    actual_policy_root = evidenced_reencoding_policy_root_v21(round_input.policy)
    if not hmac.compare_digest(actual_policy_root, expected_policy_root):
        raise ValueError("round policy does not match expected policy root")


@dataclass(frozen=True, slots=True)
class RoundAnchorV21:
    """A body-free copy of one input's external evidence anchors."""

    round_index: int
    expected_base_state_hash: str
    source_envelope_root: str
    policy_root: str
    expected_advance_head_root: str

    def __post_init__(self) -> None:
        validate_round_anchor_v21(self)


def validate_round_anchor_v21(anchor: RoundAnchorV21) -> None:
    if type(anchor) is not RoundAnchorV21:
        raise TypeError("round anchors require nominal RoundAnchorV21")
    _require_nonnegative_int_v21(anchor.round_index, "round anchor index")
    _require_domain_digest_v21(
        anchor.expected_base_state_hash,
        EVIDENCED_STATE_HASH_DOMAIN_V21,
        "round anchor expected_base_state_hash",
    )
    _require_domain_digest_v21(
        anchor.source_envelope_root,
        SOURCE_ENVELOPE_ROOT_DOMAIN_V21,
        "round anchor source_envelope_root",
    )
    _require_domain_digest_v21(
        anchor.policy_root,
        EVIDENCED_POLICY_ROOT_DOMAIN_V21,
        "round anchor policy_root",
    )
    _require_domain_digest_v21(
        anchor.expected_advance_head_root,
        ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
        "round anchor expected_advance_head_root",
    )


def _round_anchor_v21(round_input: RoundInputV21) -> RoundAnchorV21:
    validate_round_input_v21(round_input)
    return RoundAnchorV21(
        round_index=round_input.round_index,
        expected_base_state_hash=round_input.expected_base_state_hash,
        source_envelope_root=round_input.expected_source_envelope_root,
        policy_root=round_input.expected_policy_root,
        expected_advance_head_root=round_input.expected_advance_head_root,
    )


@dataclass(frozen=True, slots=True)
class MultiroundTraceV21:
    """One accepted transition's compact roots and integer accounting only."""

    round_index: int
    before_state_hash: str
    after_state_hash: str
    source_envelope_root: str
    policy_root: str
    before_advance_head_root: str
    after_advance_head_root: str
    before_control_bytes: int
    before_source_body_bytes: int
    before_resident_bytes: int
    after_control_bytes: int
    after_source_body_bytes: int
    after_resident_bytes: int
    resident_delta: int
    loss_delta: int
    before_generation: int
    after_generation: int
    before_high_water: int
    after_high_water: int
    release_count: int
    matrix_root: str
    transition_root: str
    exit_reason: MultiroundExitReasonV21

    def __post_init__(self) -> None:
        validate_multiround_trace_v21(self)


def validate_multiround_trace_v21(trace: MultiroundTraceV21) -> None:
    """Require a self-consistent compact record of an accepted advance."""
    if type(trace) is not MultiroundTraceV21:
        raise TypeError("multi-round traces require nominal MultiroundTraceV21")
    _require_nonnegative_int_v21(trace.round_index, "trace round_index")
    for value, domain, label in (
        (trace.before_state_hash, EVIDENCED_STATE_HASH_DOMAIN_V21, "before state"),
        (trace.after_state_hash, EVIDENCED_STATE_HASH_DOMAIN_V21, "after state"),
        (
            trace.source_envelope_root,
            SOURCE_ENVELOPE_ROOT_DOMAIN_V21,
            "trace source envelope",
        ),
        (trace.policy_root, EVIDENCED_POLICY_ROOT_DOMAIN_V21, "trace policy"),
        (
            trace.before_advance_head_root,
            ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
            "before advance head",
        ),
        (
            trace.after_advance_head_root,
            ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
            "after advance head",
        ),
        (trace.matrix_root, MATRIX_ROOT_DOMAIN_V21, "trace matrix root"),
        (
            trace.transition_root,
            EVIDENCED_TRANSITION_ROOT_DOMAIN_V21,
            "trace transition root",
        ),
    ):
        _require_domain_digest_v21(value, domain, label)
    for value, label in (
        (trace.before_control_bytes, "trace before_control_bytes"),
        (trace.before_source_body_bytes, "trace before_source_body_bytes"),
        (trace.before_resident_bytes, "trace before_resident_bytes"),
        (trace.after_control_bytes, "trace after_control_bytes"),
        (trace.after_source_body_bytes, "trace after_source_body_bytes"),
        (trace.after_resident_bytes, "trace after_resident_bytes"),
        (trace.loss_delta, "trace loss_delta"),
        (trace.before_generation, "trace before_generation"),
        (trace.after_generation, "trace after_generation"),
        (trace.before_high_water, "trace before_high_water"),
        (trace.after_high_water, "trace after_high_water"),
        (trace.release_count, "trace release_count"),
    ):
        _require_nonnegative_int_v21(value, label)
    _require_int_v21(trace.resident_delta, "trace resident_delta")
    if trace.before_resident_bytes != (
        trace.before_control_bytes + trace.before_source_body_bytes
    ):
        raise ValueError("trace before resident bytes must equal C plus B")
    if trace.after_resident_bytes != (
        trace.after_control_bytes + trace.after_source_body_bytes
    ):
        raise ValueError("trace after resident bytes must equal C plus B")
    if trace.resident_delta != (
        trace.after_resident_bytes - trace.before_resident_bytes
    ):
        raise ValueError("trace resident_delta does not bind C/B/R accounting")
    if trace.after_generation != trace.before_generation + 1:
        raise ValueError("trace generation must advance exactly once")
    if trace.after_high_water < trace.before_high_water:
        raise ValueError("trace high_water cannot decrease")
    if trace.exit_reason is not MultiroundExitReasonV21.ADVANCED:
        raise ValueError("trace may contain only accepted ADVANCED transitions")


@dataclass(frozen=True, slots=True)
class EvidencedMultiroundResultV21:
    """Terminal compact report with no state, candidate, or source retention."""

    initial_state_hash: str
    initial_advance_head_root: str
    round_anchors: tuple[RoundAnchorV21, ...]
    traces: tuple[MultiroundTraceV21, ...]
    status: MultiroundStatusV21
    exit_reason: MultiroundExitReasonV21
    failed_round_index: int | None
    reason: str | None
    final_state_hash: str
    final_advance_head_root: str
    run_root: str

    def __post_init__(self) -> None:
        validate_evidenced_multiround_result_v21(self)


def _validate_anchor_sequence_v21(anchors: object) -> tuple[RoundAnchorV21, ...]:
    if type(anchors) is not tuple:
        raise ValueError("result round_anchors must be a tuple")
    if len(anchors) > MAX_MULTIROUNDS_V21:
        raise ValueError("result round_anchors exceeds 12-round cap")
    items: list[RoundAnchorV21] = []
    for expected_index, anchor in enumerate(anchors):
        if type(anchor) is not RoundAnchorV21:
            raise ValueError("result round_anchors require nominal rows")
        validate_round_anchor_v21(anchor)
        if anchor.round_index != expected_index:
            raise ValueError("result round_anchors must be contiguous from zero")
        items.append(anchor)
    return tuple(items)


def _validate_trace_sequence_v21(
    traces: object,
    *,
    initial_state_hash: str,
    initial_head_root: str,
    anchors: tuple[RoundAnchorV21, ...],
) -> tuple[MultiroundTraceV21, ...]:
    if type(traces) is not tuple:
        raise ValueError("result traces must be a tuple")
    if len(traces) > len(anchors):
        raise ValueError("result traces cannot exceed planned round anchors")
    items: list[MultiroundTraceV21] = []
    prior_state_hash = initial_state_hash
    prior_head_root = initial_head_root
    for expected_index, trace in enumerate(traces):
        if type(trace) is not MultiroundTraceV21:
            raise ValueError("result traces require nominal rows")
        validate_multiround_trace_v21(trace)
        anchor = anchors[expected_index]
        if (
            trace.round_index != expected_index
            or trace.before_state_hash != prior_state_hash
            or trace.before_advance_head_root != prior_head_root
            or trace.before_state_hash != anchor.expected_base_state_hash
            or trace.before_advance_head_root != anchor.expected_advance_head_root
            or trace.source_envelope_root != anchor.source_envelope_root
            or trace.policy_root != anchor.policy_root
        ):
            raise ValueError("trace does not bind its prior compact anchors")
        prior_state_hash = trace.after_state_hash
        prior_head_root = trace.after_advance_head_root
        items.append(trace)
    return tuple(items)


def evidenced_multiround_run_root_v21(
    *,
    initial_state_hash: str,
    initial_advance_head_root: str,
    round_anchors: tuple[RoundAnchorV21, ...],
    traces: tuple[MultiroundTraceV21, ...],
    status: MultiroundStatusV21,
    exit_reason: MultiroundExitReasonV21,
    failed_round_index: int | None,
    reason: str | None,
    final_state_hash: str,
    final_advance_head_root: str,
) -> str:
    """Bind only compact inputs, accepted traces, and terminal identities."""
    checked_initial_state = _require_domain_digest_v21(
        initial_state_hash, EVIDENCED_STATE_HASH_DOMAIN_V21, "run initial_state_hash"
    )
    checked_initial_head = _require_domain_digest_v21(
        initial_advance_head_root,
        ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
        "run initial_advance_head_root",
    )
    anchors = _validate_anchor_sequence_v21(round_anchors)
    checked_traces = _validate_trace_sequence_v21(
        traces,
        initial_state_hash=checked_initial_state,
        initial_head_root=checked_initial_head,
        anchors=anchors,
    )
    if type(status) is not MultiroundStatusV21:
        raise ValueError("run status must be a closed MultiroundStatusV21")
    if type(exit_reason) is not MultiroundExitReasonV21:
        raise ValueError("run exit_reason must be closed")
    if exit_reason is MultiroundExitReasonV21.ADVANCED:
        raise ValueError("run cannot terminate with ADVANCED exit_reason")
    if failed_round_index is not None:
        _require_nonnegative_int_v21(failed_round_index, "run failed_round_index")
    if reason is not None and type(reason) is not str:
        raise ValueError("run reason must be a string or None")
    checked_final_state = _require_domain_digest_v21(
        final_state_hash, EVIDENCED_STATE_HASH_DOMAIN_V21, "run final_state_hash"
    )
    checked_final_head = _require_domain_digest_v21(
        final_advance_head_root,
        ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
        "run final_advance_head_root",
    )
    digest = sha256(
        canonical_json_v21(
            {
                "domain": MULTIROUND_RUN_ROOT_DOMAIN_V21,
                "value": {
                    "initial_state_hash": checked_initial_state,
                    "initial_advance_head_root": checked_initial_head,
                    "round_anchors": anchors,
                    "traces": checked_traces,
                    "status": status,
                    "exit_reason": exit_reason,
                    "failed_round_index": failed_round_index,
                    "reason": reason,
                    "final_state_hash": checked_final_state,
                    "final_advance_head_root": checked_final_head,
                },
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"{MULTIROUND_RUN_ROOT_DOMAIN_V21}:{digest}"


def validate_evidenced_multiround_result_v21(
    result: EvidencedMultiroundResultV21,
) -> None:
    """Revalidate the report after any attempted nested frozen-object mutation."""
    if type(result) is not EvidencedMultiroundResultV21:
        raise TypeError(
            "multi-round results require nominal EvidencedMultiroundResultV21"
        )
    initial_state_hash = _require_domain_digest_v21(
        result.initial_state_hash,
        EVIDENCED_STATE_HASH_DOMAIN_V21,
        "result initial_state_hash",
    )
    initial_head_root = _require_domain_digest_v21(
        result.initial_advance_head_root,
        ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
        "result initial_advance_head_root",
    )
    anchors = _validate_anchor_sequence_v21(result.round_anchors)
    traces = _validate_trace_sequence_v21(
        result.traces,
        initial_state_hash=initial_state_hash,
        initial_head_root=initial_head_root,
        anchors=anchors,
    )
    if type(result.status) is not MultiroundStatusV21:
        raise ValueError("result status must be a closed MultiroundStatusV21")
    if type(result.exit_reason) is not MultiroundExitReasonV21:
        raise ValueError("result exit_reason must be closed")
    if result.exit_reason is MultiroundExitReasonV21.ADVANCED:
        raise ValueError("result cannot terminate with ADVANCED exit_reason")
    expected_status = _STATUS_BY_EXIT_REASON_V21.get(result.exit_reason)
    if expected_status is None or result.status is not expected_status:
        raise ValueError("result status does not match closed exit_reason")
    if result.status is MultiroundStatusV21.COMPLETED:
        if (
            result.failed_round_index is not None
            or result.reason is not None
            or len(traces) != len(anchors)
        ):
            raise ValueError(
                "completed result must consume every round without failure"
            )
    else:
        expected_reason = _EXIT_REASON_TEXT_V21[result.exit_reason]
        if result.reason != expected_reason:
            raise ValueError("failed result reason must be its closed body-free text")
        if result.exit_reason is MultiroundExitReasonV21.STALE_INITIAL_ANCHOR:
            expected_index = 0 if anchors else None
            if result.failed_round_index != expected_index or traces:
                raise ValueError(
                    "initial stale result cannot contain an attempted trace"
                )
        else:
            if (
                isinstance(result.failed_round_index, bool)
                or not isinstance(result.failed_round_index, int)
                or result.failed_round_index < 0
                or result.failed_round_index >= len(anchors)
                or len(traces) != result.failed_round_index
            ):
                raise ValueError("failed result must retain only its verified prefix")
    final_state_hash = _require_domain_digest_v21(
        result.final_state_hash,
        EVIDENCED_STATE_HASH_DOMAIN_V21,
        "result final_state_hash",
    )
    final_head_root = _require_domain_digest_v21(
        result.final_advance_head_root,
        ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
        "result final_advance_head_root",
    )
    expected_final_state = traces[-1].after_state_hash if traces else initial_state_hash
    expected_final_head = (
        traces[-1].after_advance_head_root if traces else initial_head_root
    )
    if (
        final_state_hash != expected_final_state
        or final_head_root != expected_final_head
    ):
        raise ValueError("result final identities do not name its verified prefix")
    expected_root = evidenced_multiround_run_root_v21(
        initial_state_hash=initial_state_hash,
        initial_advance_head_root=initial_head_root,
        round_anchors=anchors,
        traces=traces,
        status=result.status,
        exit_reason=result.exit_reason,
        failed_round_index=result.failed_round_index,
        reason=result.reason,
        final_state_hash=final_state_hash,
        final_advance_head_root=final_head_root,
    )
    claimed_root = _require_domain_digest_v21(
        result.run_root, MULTIROUND_RUN_ROOT_DOMAIN_V21, "result run root"
    )
    if not hmac.compare_digest(claimed_root, expected_root):
        raise ValueError("result run root does not bind compact multi-round report")


def _validate_head_for_state_v21(
    state: EvidencedCapsuleStateV21,
    head: AdvanceChainHeadV21,
    state_hash: str,
) -> None:
    """Use public head validation/rebuild, then bind cursors to this state."""
    if type(head) is not AdvanceChainHeadV21:
        raise TypeError("advance head must be nominal AdvanceChainHeadV21")
    validate_advance_chain_head_v21(head)
    rebuilt = advance_chain_head_root_v21(
        state_hash=head.state_hash,
        generation=head.generation,
        barrier_cursors=head.barrier_cursors,
        ledger_cursor=head.ledger_cursor,
        previous_head_root=head.previous_head_root,
    )
    if not hmac.compare_digest(head.head_root, rebuilt):
        raise ValueError("advance head root does not rebuild from public fields")
    if not hmac.compare_digest(head.state_hash, state_hash):
        raise _StaleInputV21("advance head does not name current state")
    if head.generation != state.state.frame.generation:
        raise _StaleInputV21("advance head generation does not name current state")
    barriers = {
        (barrier.namespace, barrier.incarnation): barrier
        for barrier in state.state.fold_barriers
    }
    cursors = {
        (cursor.namespace, cursor.incarnation): cursor
        for cursor in head.barrier_cursors
    }
    if set(cursors) != set(barriers):
        raise _StaleInputV21("advance head barrier cursors do not name current state")
    for identity, barrier in barriers.items():
        cursor = cursors[identity]
        if (
            cursor.durable_root != barrier.cumulative_root
            or cursor.high_water != barrier.folded_through_high_water
        ):
            raise _StaleInputV21("advance head barrier cursor does not name state")
    ledger = state.state.loss_ledger
    ledger_cursor = head.ledger_cursor
    if (
        ledger_cursor.durable_root != ledger.cumulative_loss_root
        or ledger_cursor.role_counts != ledger.role_counts
        or ledger_cursor.generation != ledger.last_fold_generation
    ):
        raise _StaleInputV21("advance head ledger cursor does not name state")


def _runtime_state_hash_v21(state: EvidencedCapsuleStateV21) -> str:
    if type(state) is not EvidencedCapsuleStateV21:
        raise TypeError("state must be nominal EvidencedCapsuleStateV21")
    validate_evidenced_capsule_state_v21(state)
    return evidenced_state_hash_v21(state)


def _preflight_rounds_v21(rounds: object) -> tuple[RoundAnchorV21, ...]:
    if type(rounds) is not tuple:
        raise TypeError("rounds must be one fully concrete tuple")
    if len(rounds) > MAX_MULTIROUNDS_V21:
        raise ValueError("multi-round evaluation accepts at most 12 rounds")
    anchors: list[RoundAnchorV21] = []
    for expected_index, round_input in enumerate(rounds):
        if type(round_input) is not RoundInputV21:
            raise TypeError("rounds require nominal RoundInputV21 values")
        validate_round_input_v21(round_input)
        if round_input.round_index != expected_index:
            raise ValueError("round_index values must be contiguous from zero")
        anchors.append(_round_anchor_v21(round_input))
    return tuple(anchors)


def _matches_current_round_v21(
    *,
    state_hash: str,
    head: AdvanceChainHeadV21,
    round_input: RoundInputV21,
    anchor: RoundAnchorV21,
) -> bool:
    return (
        hmac.compare_digest(anchor.expected_base_state_hash, state_hash)
        and hmac.compare_digest(anchor.expected_advance_head_root, head.head_root)
        and hmac.compare_digest(round_input.source_envelope.base_state_hash, state_hash)
    )


@dataclass(frozen=True, slots=True)
class _SuccessfulAdvanceV21:
    state: EvidencedCapsuleStateV21
    head: AdvanceChainHeadV21
    state_hash: str
    matrix_root: str
    transition_root: str
    loss_delta: int
    release_count: int


@dataclass(frozen=True, slots=True)
class _ExpectedMatrixProjectionV21:
    """The complete public matrix projection for one returned candidate."""

    source_axes: tuple[MatrixSourceAxisV21, ...]
    segment_axes: tuple[MatrixSegmentAxisV21, ...]
    child_axes: tuple[MatrixChildAxisV21, ...]
    h_edges: tuple[MatrixDirectedEdgeV21, ...]
    mandatory_rows: tuple[int, ...]
    f_rows: tuple[SparseBinaryCellV21, ...]
    g_rows: tuple[SparseBinaryCellV21, ...]
    p_edges: tuple[MatrixParentChildEdgeV21, ...]
    x_rows: tuple[MatrixDecisionXV21, ...]
    h_width: int
    m_width: int
    f_width: int
    g_width: int
    p_width: int
    x_width: int


def _expected_matrix_projection_v21(
    *,
    base: EvidencedCapsuleStateV21,
    envelope: SourceEnvelopeV21,
    candidate: EvidencedReencodingCandidateV21,
) -> _ExpectedMatrixProjectionV21:
    """Derive the planner's public sparse-matrix projection without planning."""
    evidence_by_id: dict[str, FoldContributionEvidenceV21] = {}
    for evidence in envelope.contribution_evidence:
        if evidence.record_id in evidence_by_id:
            raise ValueError("duplicate source contribution evidence id")
        evidence_by_id[evidence.record_id] = evidence

    source_axes_list: list[MatrixSourceAxisV21] = []
    for source in envelope.source_records:
        evidence = evidence_by_id.get(source.record_id)
        if evidence is None:
            raise ValueError("source contribution evidence is missing")
        if evidence.source_commitment != source.commitment:
            raise ValueError("source contribution evidence commitment differs")
        source_axes_list.append(
            MatrixSourceAxisV21(
                record_id=source.record_id,
                commitment=source.commitment,
                role=evidence.role,
                core_required=source.core_required,
                contribution_keys=evidence.contribution_keys,
            )
        )
    source_axes = tuple(source_axes_list)
    source_rows = {
        axis.record_id: row_index for row_index, axis in enumerate(source_axes)
    }
    if len(source_rows) != len(source_axes):
        raise ValueError("duplicate source axis id")
    if set(evidence_by_id) != set(source_rows):
        raise ValueError("source contribution evidence does not exactly cover axes")

    base_segment_keys: dict[str, tuple[str, ...]] = {}
    for segment in base.state.capsule_segments:
        if segment.segment_id in base_segment_keys:
            raise ValueError("duplicate base segment id")
        base_segment_keys[segment.segment_id] = tuple(
            locator.contribution_key
            for locator in base.contribution_index
            if locator.support is ContributionSupportV21.SEGMENT
            and locator.resident_id == segment.segment_id
        )
    segment_axes = tuple(
        MatrixSegmentAxisV21(
            segment_id=segment.segment_id,
            canonical_hash=canonical_segment_hash_v21(base.state, segment.segment_id),
            role_counts=segment.role_counts,
            contribution_keys=base_segment_keys[segment.segment_id],
        )
        for segment in sorted(
            base.state.capsule_segments, key=lambda row: row.segment_id
        )
    )
    segment_rows = {
        axis.segment_id: row_index for row_index, axis in enumerate(segment_axes)
    }
    if len(segment_rows) != len(segment_axes):
        raise ValueError("duplicate segment axis id")

    records_by_id: dict[str, RecordDecisionV21] = {}
    for decision in candidate.record_decisions:
        if decision.record_id in records_by_id:
            raise ValueError("duplicate record decision id")
        records_by_id[decision.record_id] = decision
    if set(records_by_id) != set(source_rows):
        raise ValueError("record decisions do not exactly cover source axes")

    segments_by_id: dict[str, SegmentDecisionV21] = {}
    for decision in candidate.segment_decisions:
        if decision.segment_id in segments_by_id:
            raise ValueError("duplicate segment decision id")
        segments_by_id[decision.segment_id] = decision
    if set(segments_by_id) != set(segment_rows):
        raise ValueError("segment decisions do not exactly cover segment axes")

    rootless_sources: dict[str, list[int]] = {}
    for row_index, axis in enumerate(source_axes):
        decision = records_by_id.get(axis.record_id)
        if decision is None:
            raise ValueError("source axis has no record decision")
        if decision.source_commitment != axis.commitment:
            raise ValueError("record decision commitment differs from source axis")
        if decision.contribution_keys != axis.contribution_keys:
            raise ValueError(
                "record decision contribution keys differ from source axis"
            )
        if decision.outcome is ReencodingOutcomeV21.SEGMENT:
            child_id = decision.child_segment_id
            if child_id is None:
                raise ValueError("SEGMENT record decision omits its child")
            rootless_sources.setdefault(child_id, []).append(row_index)
        elif decision.child_segment_id is not None:
            raise ValueError("non-SEGMENT record decision names a child")

    compact_parents: dict[str, int] = {}
    for row_index, axis in enumerate(segment_axes):
        decision = segments_by_id.get(axis.segment_id)
        if decision is None:
            raise ValueError("segment axis has no segment decision")
        if decision.canonical_segment_hash != axis.canonical_hash:
            raise ValueError("segment decision canonical hash differs from axis")
        if decision.contribution_keys != axis.contribution_keys:
            raise ValueError("segment decision contribution keys differ from axis")
        if decision.disposition is SegmentDispositionV21.COMPACT:
            child_id = decision.child_segment_id
            if child_id is None:
                raise ValueError("COMPACT segment decision omits its child")
            if child_id in compact_parents:
                raise ValueError("a compact child cannot have multiple parents")
            compact_parents[child_id] = row_index
        elif decision.child_segment_id is not None:
            raise ValueError("non-COMPACT segment decision names a child")

    if set(rootless_sources).intersection(compact_parents):
        raise ValueError("a matrix child cannot be both rootless and compact")
    child_axes = tuple(
        MatrixChildAxisV21(
            child_id=child_id,
            kind=(
                MatrixChildKindV21.ROOTLESS_SEGMENT
                if child_id in rootless_sources
                else MatrixChildKindV21.PARENT_COMPACT
            ),
            source_rows=tuple(rootless_sources.get(child_id, ())),
            parent_rows=(compact_parents[child_id],)
            if child_id in compact_parents
            else (),
        )
        for child_id in sorted(set(rootless_sources) | set(compact_parents))
    )
    child_rows = {axis.child_id: row_index for row_index, axis in enumerate(child_axes)}
    if len(child_rows) != len(child_axes):
        raise ValueError("duplicate child axis id")

    h_edges_list: list[MatrixDirectedEdgeV21] = []
    for edge in envelope.hard_edges:
        source_row = source_rows.get(edge.source_id)
        target_row = source_rows.get(edge.target_id)
        if source_row is None or target_row is None:
            raise ValueError("hard edge does not map to the source axis")
        h_edges_list.append(
            MatrixDirectedEdgeV21(
                source_row=source_row,
                target_row=target_row,
            )
        )
    h_edges = tuple(sorted(h_edges_list))
    mandatory_rows = tuple(
        sorted(
            {
                row_index
                for row_index, axis in enumerate(source_axes)
                if axis.core_required
            }
            | {edge.source_row for edge in h_edges}
            | {edge.target_row for edge in h_edges}
        )
    )

    f_width = max(
        (len(evidence.coverage_vector) for evidence in envelope.contribution_evidence),
        default=0,
    )
    g_width = max(
        (len(evidence.bridge_vector) for evidence in envelope.contribution_evidence),
        default=0,
    )
    f_rows = tuple(
        SparseBinaryCellV21(row_index=row_index, column_index=column_index)
        for row_index, axis in enumerate(source_axes)
        for column_index, bit in enumerate(
            evidence_by_id[axis.record_id].coverage_vector
        )
        if bit == 1
    )
    g_rows = tuple(
        SparseBinaryCellV21(row_index=row_index, column_index=column_index)
        for row_index, axis in enumerate(source_axes)
        for column_index, bit in enumerate(evidence_by_id[axis.record_id].bridge_vector)
        if bit == 1
    )
    p_edges = tuple(
        sorted(
            MatrixParentChildEdgeV21(parent_row=parent_row, child_row=child_row)
            for child_row, child in enumerate(child_axes)
            for parent_row in child.parent_rows
        )
    )

    x_rows_list: list[MatrixDecisionXV21] = []
    for row_index, axis in enumerate(source_axes):
        decision = records_by_id.get(axis.record_id)
        if decision is None:
            raise ValueError("source axis has no X decision")
        child_row = None
        if decision.outcome is ReencodingOutcomeV21.SEGMENT:
            if decision.child_segment_id is None:
                raise ValueError("SEGMENT record X decision omits its child")
            child_row = child_rows.get(decision.child_segment_id)
            if child_row is None:
                raise ValueError("record X decision child is not materialised")
        x_rows_list.append(
            MatrixDecisionXV21(
                axis=MatrixDecisionAxisV21.SOURCE,
                row_index=row_index,
                record_outcome=decision.outcome,
                segment_disposition=None,
                child_row=child_row,
            )
        )
    for row_index, axis in enumerate(segment_axes):
        decision = segments_by_id.get(axis.segment_id)
        if decision is None:
            raise ValueError("segment axis has no X decision")
        child_row = None
        if decision.disposition is SegmentDispositionV21.COMPACT:
            if decision.child_segment_id is None:
                raise ValueError("COMPACT segment X decision omits its child")
            child_row = child_rows.get(decision.child_segment_id)
            if child_row is None:
                raise ValueError("segment X decision child is not materialised")
        x_rows_list.append(
            MatrixDecisionXV21(
                axis=MatrixDecisionAxisV21.SEGMENT,
                row_index=row_index,
                record_outcome=None,
                segment_disposition=decision.disposition,
                child_row=child_row,
            )
        )
    x_rows = tuple(x_rows_list)
    return _ExpectedMatrixProjectionV21(
        source_axes=source_axes,
        segment_axes=segment_axes,
        child_axes=child_axes,
        h_edges=h_edges,
        mandatory_rows=mandatory_rows,
        f_rows=f_rows,
        g_rows=g_rows,
        p_edges=p_edges,
        x_rows=x_rows,
        h_width=len(source_axes),
        m_width=len(source_axes),
        f_width=f_width,
        g_width=g_width,
        p_width=len(child_axes),
        x_width=len(source_axes) + len(segment_axes),
    )


def _validate_matrix_projection_v21(
    *,
    matrix: EvidencedMatrixPlanV21,
    projection: _ExpectedMatrixProjectionV21,
    policy: EvidencedReencodingPolicyV21,
) -> None:
    """Reject a locally valid matrix that is not its candidate's projection."""
    if matrix.solver_mode is not policy.solver_mode:
        raise ValueError("matrix solver mode differs from the round policy")
    if not 1 <= matrix.evaluation_count <= policy.max_plan_evaluations:
        raise ValueError("matrix evaluation count is outside the round policy")
    for label, actual, expected in (
        ("source axes", matrix.source_axes, projection.source_axes),
        ("segment axes", matrix.segment_axes, projection.segment_axes),
        ("child axes", matrix.child_axes, projection.child_axes),
        ("H", matrix.h_edges, projection.h_edges),
        ("M", matrix.mandatory_rows, projection.mandatory_rows),
        ("F", matrix.f_rows, projection.f_rows),
        ("G", matrix.g_rows, projection.g_rows),
        ("P", matrix.p_edges, projection.p_edges),
        ("X", matrix.x_rows, projection.x_rows),
    ):
        if actual != expected:
            raise ValueError(f"matrix {label} differs from its canonical projection")
    if (
        matrix.h_width,
        matrix.m_width,
        matrix.f_width,
        matrix.g_width,
        matrix.p_width,
        matrix.x_width,
    ) != (
        projection.h_width,
        projection.m_width,
        projection.f_width,
        projection.g_width,
        projection.p_width,
        projection.x_width,
    ):
        raise ValueError("matrix widths differ from its canonical projection")


def _revalidate_candidate_witness_v21(
    candidate: EvidencedReencodingCandidateV21,
    *,
    base_hash: str,
    envelope_root: str,
    policy_root: str,
) -> None:
    """Revalidate all public nested candidate rows without invoking V21-005."""
    if type(candidate.source_envelope) is not SourceEnvelopeV21:
        _reject_postcondition_v21("candidate source envelope type is not nominal")
    validate_source_envelope_v21(candidate.source_envelope)
    if type(candidate.policy) is not EvidencedReencodingPolicyV21:
        _reject_postcondition_v21("candidate policy type is not nominal")
    validate_evidenced_reencoding_policy_v21(candidate.policy)
    if type(candidate.proposed_state) is not EvidencedCapsuleStateV21:
        _reject_postcondition_v21("candidate proposed state type is not nominal")
    validate_evidenced_capsule_state_v21(candidate.proposed_state)
    if type(candidate.first_fold_authorizations) is not tuple:
        _reject_postcondition_v21("candidate authorizations must be a tuple")
    for authorization in candidate.first_fold_authorizations:
        validate_first_fold_authorization_v21(authorization)
    expected_authorizations = tuple(
        sorted(
            authorization.authorization_root
            for authorization in candidate.first_fold_authorizations
        )
    )
    if candidate.authorization_roots != expected_authorizations:
        _reject_postcondition_v21("candidate authorization roots do not bind rows")
    if type(candidate.barrier_advances) is not tuple:
        _reject_postcondition_v21("candidate barrier advances must be a tuple")
    for advance in candidate.barrier_advances:
        validate_barrier_advance_v21(advance)
    expected_barriers = tuple(
        sorted(advance.new_root for advance in candidate.barrier_advances)
    )
    if candidate.barrier_roots != expected_barriers:
        _reject_postcondition_v21("candidate barrier roots do not bind advances")
    validate_loss_ledger_advance_v21(candidate.ledger_advance)
    if candidate.ledger_root != candidate.ledger_advance.new_root:
        _reject_postcondition_v21("candidate ledger root does not bind advance")
    actual_record_root = _record_root_v21(
        base_hash=base_hash,
        envelope_root=envelope_root,
        policy_root=policy_root,
        rows=candidate.record_decisions,
    )
    if candidate.record_decision_root != actual_record_root:
        _reject_postcondition_v21("candidate record root does not bind rows")
    actual_segment_root = _segment_root_v21(
        base_hash=base_hash,
        envelope_root=envelope_root,
        policy_root=policy_root,
        rows=candidate.segment_decisions,
    )
    if candidate.segment_decision_root != actual_segment_root:
        _reject_postcondition_v21("candidate segment root does not bind rows")


def _record_root_v21(
    *,
    base_hash: str,
    envelope_root: str,
    policy_root: str,
    rows: object,
) -> str:
    from crm_experiment.reencoding_contracts_v21 import record_decision_root_v21

    if type(rows) is not tuple:
        _reject_postcondition_v21("candidate record decisions must be a tuple")
    return record_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope_root,
        policy_root=policy_root,
        rows=rows,
    )


def _segment_root_v21(
    *,
    base_hash: str,
    envelope_root: str,
    policy_root: str,
    rows: object,
) -> str:
    from crm_experiment.reencoding_contracts_v21 import segment_decision_root_v21

    if type(rows) is not tuple:
        _reject_postcondition_v21("candidate segment decisions must be a tuple")
    return segment_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope_root,
        policy_root=policy_root,
        rows=rows,
    )


def _validate_returned_plan_v21(
    *,
    planned: object,
    base: EvidencedCapsuleStateV21,
    current_head: AdvanceChainHeadV21,
    round_input: RoundInputV21,
    anchor: RoundAnchorV21,
    base_hash: str,
) -> _SuccessfulAdvanceV21:
    """Validate a planner result without reinvoking the V21-005 verifier."""
    try:
        if type(planned) is not EvidencedPlannedCandidateV21:
            _reject_postcondition_v21(
                "planner return must be nominal V21 planned value"
            )
        candidate = planned.candidate
        matrix = planned.matrix_plan
        verification = planned.verification
        if type(candidate) is not EvidencedReencodingCandidateV21:
            _reject_postcondition_v21("planned candidate type is not nominal")
        if type(matrix) is not EvidencedMatrixPlanV21:
            _reject_postcondition_v21("planned matrix type is not nominal")
        if type(verification) is not EvidencedReencodingResultV21:
            _reject_postcondition_v21("planned verification type is not nominal")
        if candidate.source_envelope != round_input.source_envelope:
            _reject_postcondition_v21("candidate source envelope differs from round")
        if candidate.policy != round_input.policy:
            _reject_postcondition_v21("candidate policy differs from round")
        if (
            candidate.source_envelope.base_state_hash != base_hash
            or candidate.source_envelope.envelope_root != anchor.source_envelope_root
        ):
            _reject_postcondition_v21("candidate envelope does not bind current round")
        actual_policy_root = evidenced_reencoding_policy_root_v21(candidate.policy)
        if actual_policy_root != anchor.policy_root:
            _reject_postcondition_v21("candidate policy root does not bind round")
        _revalidate_candidate_witness_v21(
            candidate,
            base_hash=base_hash,
            envelope_root=anchor.source_envelope_root,
            policy_root=anchor.policy_root,
        )
        replace(matrix)
        projection = _expected_matrix_projection_v21(
            base=base,
            envelope=round_input.source_envelope,
            candidate=candidate,
        )
        _validate_matrix_projection_v21(
            matrix=matrix,
            projection=projection,
            policy=round_input.policy,
        )
        proposed_hash = evidenced_state_hash_v21(candidate.proposed_state)
        if candidate.proposed_hash != proposed_hash:
            _reject_postcondition_v21("candidate proposed hash does not bind state")
        if (
            matrix.base_state_hash != base_hash
            or matrix.source_envelope_root != anchor.source_envelope_root
            or matrix.policy_root != anchor.policy_root
            or matrix.matrix_root != candidate.matrix_root
            or matrix.record_decision_root != candidate.record_decision_root
            or matrix.segment_decision_root != candidate.segment_decision_root
            or matrix.proposed_hash != proposed_hash
            or matrix.target_generation != candidate.target_generation
            or matrix.target_high_water != candidate.target_high_water
        ):
            _reject_postcondition_v21("matrix roots do not bind candidate fields")
        if (
            type(candidate.target_generation) is not int
            or type(candidate.target_high_water) is not int
            or candidate.target_generation != base.state.frame.generation + 1
            or candidate.target_high_water
            != max(
                base.state.frame.high_water,
                *(
                    source.as_of
                    for source in round_input.source_envelope.source_records
                ),
            )
        ):
            _reject_postcondition_v21("candidate generation or high_water is invalid")
        if (
            candidate.proposed_state.state.frame.generation
            != candidate.target_generation
            or candidate.proposed_state.state.frame.high_water
            != candidate.target_high_water
        ):
            _reject_postcondition_v21("proposed frame does not match candidate target")
        before_layout = evidenced_resident_layout_v21(base)
        after_layout = evidenced_resident_layout_v21(candidate.proposed_state)
        objective = matrix.objective
        if (
            objective.control_bytes != after_layout.control_bytes
            or objective.source_body_bytes != after_layout.source_body_bytes
            or objective.resident_bytes != after_layout.resident_bytes
            or objective.saved_bytes
            != max(0, before_layout.resident_bytes - after_layout.resident_bytes)
            or objective.loss_units != candidate.ledger_advance.loss_units_delta
        ):
            _reject_postcondition_v21("matrix objective does not bind real C/B/R")
        expected_transition = evidenced_transition_root_v21(
            base_state_hash=base_hash,
            source_envelope_root=anchor.source_envelope_root,
            policy_root=anchor.policy_root,
            matrix_root=candidate.matrix_root,
            record_decision_root=candidate.record_decision_root,
            segment_decision_root=candidate.segment_decision_root,
            authorization_roots=candidate.authorization_roots,
            barrier_roots=candidate.barrier_roots,
            ledger_root=candidate.ledger_root,
            proposed_hash=proposed_hash,
            target_generation=candidate.target_generation,
            target_high_water=candidate.target_high_water,
        )
        if candidate.transition_root != expected_transition:
            _reject_postcondition_v21("candidate transition root does not rebuild")
        if (
            verification.status is not EvidencedReencodingStatusV21.VERIFIED
            or verification.consumed_delta is not True
            or verification.state != candidate.proposed_state
            or verification.transition_root != candidate.transition_root
        ):
            _reject_postcondition_v21(
                "planner result is not a consumed VERIFIED candidate"
            )
        verified_hash = _runtime_state_hash_v21(verification.state)
        if verified_hash != proposed_hash:
            _reject_postcondition_v21("verified state differs from candidate hash")
        _validate_head_for_state_v21(
            verification.state, verification.advance_head, verified_hash
        )
        if (
            verification.advance_head.previous_head_root != current_head.head_root
            or verification.advance_head.generation != candidate.target_generation
            or verification.advance_head.state_hash != verified_hash
        ):
            _reject_postcondition_v21("verified head does not advance current head")
        release_count = sum(
            len(advance.released_sources) for advance in candidate.barrier_advances
        )
        return _SuccessfulAdvanceV21(
            state=verification.state,
            head=verification.advance_head,
            state_hash=verified_hash,
            matrix_root=candidate.matrix_root,
            transition_root=candidate.transition_root,
            loss_delta=objective.loss_units,
            release_count=release_count,
        )
    except _PostconditionRejectedV21:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise _PostconditionRejectedV21(str(error)) from error


def _success_trace_v21(
    *,
    anchor: RoundAnchorV21,
    before_state: EvidencedCapsuleStateV21,
    before_head: AdvanceChainHeadV21,
    before_hash: str,
    advance: _SuccessfulAdvanceV21,
) -> MultiroundTraceV21:
    before_layout = evidenced_resident_layout_v21(before_state)
    after_layout = evidenced_resident_layout_v21(advance.state)
    return MultiroundTraceV21(
        round_index=anchor.round_index,
        before_state_hash=before_hash,
        after_state_hash=advance.state_hash,
        source_envelope_root=anchor.source_envelope_root,
        policy_root=anchor.policy_root,
        before_advance_head_root=before_head.head_root,
        after_advance_head_root=advance.head.head_root,
        before_control_bytes=before_layout.control_bytes,
        before_source_body_bytes=before_layout.source_body_bytes,
        before_resident_bytes=before_layout.resident_bytes,
        after_control_bytes=after_layout.control_bytes,
        after_source_body_bytes=after_layout.source_body_bytes,
        after_resident_bytes=after_layout.resident_bytes,
        resident_delta=after_layout.resident_bytes - before_layout.resident_bytes,
        loss_delta=advance.loss_delta,
        before_generation=before_state.state.frame.generation,
        after_generation=advance.state.state.frame.generation,
        before_high_water=before_state.state.frame.high_water,
        after_high_water=advance.state.state.frame.high_water,
        release_count=advance.release_count,
        matrix_root=advance.matrix_root,
        transition_root=advance.transition_root,
        exit_reason=MultiroundExitReasonV21.ADVANCED,
    )


def _result_v21(
    *,
    initial_state_hash: str,
    initial_head_root: str,
    anchors: tuple[RoundAnchorV21, ...],
    traces: tuple[MultiroundTraceV21, ...],
    status: MultiroundStatusV21,
    exit_reason: MultiroundExitReasonV21,
    failed_round_index: int | None,
) -> EvidencedMultiroundResultV21:
    reason = (
        None
        if status is MultiroundStatusV21.COMPLETED
        else _EXIT_REASON_TEXT_V21[exit_reason]
    )
    final_state_hash = traces[-1].after_state_hash if traces else initial_state_hash
    final_head_root = (
        traces[-1].after_advance_head_root if traces else initial_head_root
    )
    run_root = evidenced_multiround_run_root_v21(
        initial_state_hash=initial_state_hash,
        initial_advance_head_root=initial_head_root,
        round_anchors=anchors,
        traces=traces,
        status=status,
        exit_reason=exit_reason,
        failed_round_index=failed_round_index,
        reason=reason,
        final_state_hash=final_state_hash,
        final_advance_head_root=final_head_root,
    )
    return EvidencedMultiroundResultV21(
        initial_state_hash=initial_state_hash,
        initial_advance_head_root=initial_head_root,
        round_anchors=anchors,
        traces=traces,
        status=status,
        exit_reason=exit_reason,
        failed_round_index=failed_round_index,
        reason=reason,
        final_state_hash=final_state_hash,
        final_advance_head_root=final_head_root,
        run_root=run_root,
    )


def evaluate_evidenced_multiround_v21(
    initial_state: EvidencedCapsuleStateV21,
    initial_head: AdvanceChainHeadV21,
    rounds: tuple[RoundInputV21, ...],
    *,
    expected_initial_state_hash: str,
    expected_initial_advance_head_root: str,
) -> EvidencedMultiroundResultV21:
    """Evaluate at most twelve pre-materialised V21 transitions purely.

    A current anchor mismatch closes as ``STALE`` before that round's planner
    call.  V21-006's declared planning rejection closes as ``REJECTED``.  A
    malformed returned plan closes as ``POSTCONDITION_REJECTED``.  No failed
    attempt enters ``traces`` or advances the locally held predecessor.
    """
    anchors = _preflight_rounds_v21(rounds)
    initial_hash = _runtime_state_hash_v21(initial_state)
    if type(initial_head) is not AdvanceChainHeadV21:
        raise TypeError("initial_head must be nominal AdvanceChainHeadV21")
    validate_advance_chain_head_v21(initial_head)
    expected_state = _require_domain_digest_v21(
        expected_initial_state_hash,
        EVIDENCED_STATE_HASH_DOMAIN_V21,
        "expected_initial_state_hash",
    )
    expected_head = _require_domain_digest_v21(
        expected_initial_advance_head_root,
        ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
        "expected_initial_advance_head_root",
    )
    initial_head_root = initial_head.head_root
    try:
        _validate_head_for_state_v21(initial_state, initial_head, initial_hash)
    except _StaleInputV21:
        return _result_v21(
            initial_state_hash=initial_hash,
            initial_head_root=initial_head_root,
            anchors=anchors,
            traces=(),
            status=MultiroundStatusV21.STALE,
            exit_reason=MultiroundExitReasonV21.STALE_INITIAL_ANCHOR,
            failed_round_index=0 if anchors else None,
        )
    if not hmac.compare_digest(initial_hash, expected_state) or not hmac.compare_digest(
        initial_head_root, expected_head
    ):
        return _result_v21(
            initial_state_hash=initial_hash,
            initial_head_root=initial_head_root,
            anchors=anchors,
            traces=(),
            status=MultiroundStatusV21.STALE,
            exit_reason=MultiroundExitReasonV21.STALE_INITIAL_ANCHOR,
            failed_round_index=0 if anchors else None,
        )

    state = initial_state
    head = initial_head
    traces: list[MultiroundTraceV21] = []
    for anchor, round_input in zip(anchors, rounds, strict=True):
        validate_round_input_v21(round_input)
        if _round_anchor_v21(round_input) != anchor:
            raise ValueError("round input mutated after preflight")
        current_hash = _runtime_state_hash_v21(state)
        try:
            _validate_head_for_state_v21(state, head, current_hash)
        except _StaleInputV21:
            return _result_v21(
                initial_state_hash=initial_hash,
                initial_head_root=initial_head_root,
                anchors=anchors,
                traces=tuple(traces),
                status=MultiroundStatusV21.STALE,
                exit_reason=MultiroundExitReasonV21.STALE_ROUND_ANCHOR,
                failed_round_index=anchor.round_index,
            )
        if not _matches_current_round_v21(
            state_hash=current_hash,
            head=head,
            round_input=round_input,
            anchor=anchor,
        ):
            return _result_v21(
                initial_state_hash=initial_hash,
                initial_head_root=initial_head_root,
                anchors=anchors,
                traces=tuple(traces),
                status=MultiroundStatusV21.STALE,
                exit_reason=MultiroundExitReasonV21.STALE_ROUND_ANCHOR,
                failed_round_index=anchor.round_index,
            )
        try:
            planned = plan_evidenced_reencoding_v21(
                state,
                head,
                round_input.source_envelope,
                round_input.policy,
                expected_base_state_hash=anchor.expected_base_state_hash,
                expected_source_envelope_root=anchor.source_envelope_root,
                expected_policy_root=anchor.policy_root,
                expected_advance_head_root=anchor.expected_advance_head_root,
            )
        except EvidencedPlanningRejectedV21:
            return _result_v21(
                initial_state_hash=initial_hash,
                initial_head_root=initial_head_root,
                anchors=anchors,
                traces=tuple(traces),
                status=MultiroundStatusV21.REJECTED,
                exit_reason=MultiroundExitReasonV21.PLANNER_REJECTED,
                failed_round_index=anchor.round_index,
            )
        try:
            advance = _validate_returned_plan_v21(
                planned=planned,
                base=state,
                current_head=head,
                round_input=round_input,
                anchor=anchor,
                base_hash=current_hash,
            )
        except _PostconditionRejectedV21:
            return _result_v21(
                initial_state_hash=initial_hash,
                initial_head_root=initial_head_root,
                anchors=anchors,
                traces=tuple(traces),
                status=MultiroundStatusV21.POSTCONDITION_REJECTED,
                exit_reason=MultiroundExitReasonV21.POSTCONDITION_REJECTED,
                failed_round_index=anchor.round_index,
            )
        traces.append(
            _success_trace_v21(
                anchor=anchor,
                before_state=state,
                before_head=head,
                before_hash=current_hash,
                advance=advance,
            )
        )
        state = advance.state
        head = advance.head
    return _result_v21(
        initial_state_hash=initial_hash,
        initial_head_root=initial_head_root,
        anchors=anchors,
        traces=tuple(traces),
        status=MultiroundStatusV21.COMPLETED,
        exit_reason=MultiroundExitReasonV21.COMPLETED,
        failed_round_index=None,
    )
