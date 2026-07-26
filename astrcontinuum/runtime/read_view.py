from __future__ import annotations

from typing import NoReturn

from ..domain import SessionKey, SnapshotState
from ..storage import RequestView
from .types import RequestViewErrorCode, RequestViewSource


class RequestViewInvariantError(ValueError):
    """Report one stable request-view invariant without content."""

    def __init__(self, code: RequestViewErrorCode) -> None:
        self.code = code.value
        super().__init__(self.code)


def _invalid(code: RequestViewErrorCode) -> NoReturn:
    raise RequestViewInvariantError(code)


def _same_session(left: SessionKey, right: SessionKey) -> bool:
    return left == right


def _validate_snapshot(view: RequestView, session_key: SessionKey) -> None:
    snapshot = view.snapshot
    if snapshot is None:
        if view.pointer_version != 0 or view.covered_event_end != 0 or view.memberships:
            _invalid(RequestViewErrorCode.BOOTSTRAP_INVALID)
        return

    membership_ids = tuple(item.capsule_id for item in view.memberships)
    membership_ordinals = tuple(item.ordinal for item in view.memberships)
    if (
        view.pointer_version < 1
        or snapshot.state != SnapshotState.COMMITTED
        or not _same_session(snapshot.session_key, session_key)
        or snapshot.covered_event_end != view.covered_event_end
        or snapshot.source_high_water_mark != view.covered_event_end
        or membership_ids != snapshot.capsule_ids
        or membership_ordinals != tuple(range(len(view.memberships)))
        or any(
            not _same_session(item.capsule.session_key, session_key) for item in view.memberships
        )
    ):
        _invalid(RequestViewErrorCode.SNAPSHOT_INVALID)


def _validate_delta(view: RequestView, session_key: SessionKey) -> None:
    if view.covered_event_end < 0 or view.high_water_mark < view.covered_event_end:
        _invalid(RequestViewErrorCode.COVERAGE_INVALID)
    if any(not _same_session(item.session_key, session_key) for item in view.delta):
        _invalid(RequestViewErrorCode.DELTA_IDENTITY_MISMATCH)
    expected = tuple(range(view.covered_event_end + 1, view.high_water_mark + 1))
    if tuple(item.sequence for item in view.delta) != expected:
        _invalid(RequestViewErrorCode.DELTA_GAP)


def read_request_view(
    source: RequestViewSource,
    session_key: SessionKey,
) -> RequestView:
    """Read and validate exactly one immutable durable request view."""

    view = source.read_request_view(session_key)
    if not isinstance(view, RequestView):
        _invalid(RequestViewErrorCode.TYPE_INVALID)
    if not _same_session(view.session_key, session_key):
        _invalid(RequestViewErrorCode.SESSION_MISMATCH)
    _validate_snapshot(view, session_key)
    _validate_delta(view, session_key)
    return view
