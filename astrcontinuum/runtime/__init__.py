"""Canonical request-side AstrContinuum runtime."""

from .budget import BudgetInvariantError, Utf8ByteTokenCounter, assemble
from .read_view import RequestViewInvariantError, read_request_view
from .retrieval import select_candidates
from .types import (
    RUNTIME_SLOT_PRIORITY,
    AssemblyMode,
    AssemblyResult,
    AssemblyTrace,
    BlockRejection,
    BlockSelection,
    BudgetConfig,
    CandidateBlock,
    CandidateKind,
    RequestViewErrorCode,
    RequestViewSource,
    RetrievalConfig,
    RuntimeSlot,
    TokenCounter,
)

__all__ = [
    "RUNTIME_SLOT_PRIORITY",
    "AssemblyMode",
    "AssemblyResult",
    "AssemblyTrace",
    "BlockRejection",
    "BlockSelection",
    "BudgetConfig",
    "BudgetInvariantError",
    "CandidateBlock",
    "CandidateKind",
    "RequestViewErrorCode",
    "RequestViewInvariantError",
    "RequestViewSource",
    "RetrievalConfig",
    "RuntimeSlot",
    "TokenCounter",
    "Utf8ByteTokenCounter",
    "assemble",
    "read_request_view",
    "select_candidates",
]
