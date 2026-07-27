"""Bounded sparse context engine."""

from .engine import SparseContextEngine
from .live import LiveContextTrace, ResidualBand, assemble_live_context
from .types import (
    ConstraintRow,
    ContextEngineMode,
    ContextGraph,
    ContextGraphConfig,
    EngineOutcome,
    EngineResult,
    ErrorCertificate,
    GraphCoordinate,
    QueryActivation,
    SparseRelation,
)

__all__ = [
    "ConstraintRow",
    "ContextEngineMode",
    "ContextGraph",
    "ContextGraphConfig",
    "EngineOutcome",
    "EngineResult",
    "ErrorCertificate",
    "GraphCoordinate",
    "LiveContextTrace",
    "QueryActivation",
    "ResidualBand",
    "SparseContextEngine",
    "SparseRelation",
    "assemble_live_context",
]
