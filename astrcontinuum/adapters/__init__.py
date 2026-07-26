"""Host adapters for AstrContinuum."""

from .astrbot import (
    AdapterErrorCode,
    AdapterFault,
    AdapterStage,
    AstrBotAdapterError,
    AstrBotHookBridge,
    DeterministicEventIdentity,
    HostTurnIdentity,
    PreparedRequest,
    ProjectionBuild,
    ProjectionCapability,
    build_projection_objects,
    canonical_tool_metadata,
    deterministic_event_identity,
    estimate_opaque_token_cost,
    extract_host_turn_identity,
    probe_projection_capability,
    select_projection_boundaries,
)
from .sylanne import SylanneAdapter

__all__ = [
    "AdapterErrorCode",
    "AdapterFault",
    "AdapterStage",
    "AstrBotAdapterError",
    "AstrBotHookBridge",
    "DeterministicEventIdentity",
    "HostTurnIdentity",
    "PreparedRequest",
    "ProjectionBuild",
    "ProjectionCapability",
    "SylanneAdapter",
    "build_projection_objects",
    "canonical_tool_metadata",
    "deterministic_event_identity",
    "estimate_opaque_token_cost",
    "extract_host_turn_identity",
    "probe_projection_capability",
    "select_projection_boundaries",
]
