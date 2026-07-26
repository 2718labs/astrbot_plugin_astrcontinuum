from __future__ import annotations

from enum import Enum
from typing import Protocol

from ..domain import SessionKey
from ..storage import RequestView


class RequestViewErrorCode(str, Enum):
    """Stable, content-free request-view validation failures."""

    TYPE_INVALID = "REQUEST_VIEW_TYPE_INVALID"
    SESSION_MISMATCH = "REQUEST_VIEW_SESSION_MISMATCH"
    BOOTSTRAP_INVALID = "REQUEST_VIEW_BOOTSTRAP_INVALID"
    SNAPSHOT_INVALID = "REQUEST_VIEW_SNAPSHOT_INVALID"
    COVERAGE_INVALID = "REQUEST_VIEW_COVERAGE_INVALID"
    DELTA_GAP = "REQUEST_VIEW_DELTA_GAP"
    DELTA_IDENTITY_MISMATCH = "REQUEST_VIEW_DELTA_IDENTITY_MISMATCH"


class RequestViewSource(Protocol):
    """Narrow durable read port used by the request-side runtime."""

    def read_request_view(self, session_key: SessionKey) -> RequestView: ...
