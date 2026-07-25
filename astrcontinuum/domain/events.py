from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import ClassVar

from pydantic import AwareDatetime, model_validator

from ._base import FrozenEnvelope, NonEmptyStr, NonNegativeInt, PositiveInt
from .identity import SessionKey


class EventType(str, Enum):
    USER_MESSAGE = "USER_MESSAGE"
    ASSISTANT_MESSAGE = "ASSISTANT_MESSAGE"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"


class EventRole(str, Enum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    TOOL = "TOOL"


class SourceHook(str, Enum):
    ON_LLM_REQUEST = "ON_LLM_REQUEST"
    ON_AGENT_DONE = "ON_AGENT_DONE"
    ON_USING_LLM_TOOL = "ON_USING_LLM_TOOL"
    ON_LLM_TOOL_RESPOND = "ON_LLM_TOOL_RESPOND"


class EventEnvelope(FrozenEnvelope):
    event_id: NonEmptyStr
    session_key: SessionKey
    sequence: PositiveInt
    event_type: EventType
    role: EventRole
    content: str
    source_hook: SourceHook
    idempotency_key: NonEmptyStr
    token_count: NonNegativeInt
    created_at: AwareDatetime

    _MAPPINGS: ClassVar[dict[EventType, tuple[EventRole, SourceHook]]] = {
        EventType.USER_MESSAGE: (EventRole.USER, SourceHook.ON_LLM_REQUEST),
        EventType.ASSISTANT_MESSAGE: (EventRole.ASSISTANT, SourceHook.ON_AGENT_DONE),
        EventType.TOOL_CALL: (EventRole.TOOL, SourceHook.ON_USING_LLM_TOOL),
        EventType.TOOL_RESULT: (EventRole.TOOL, SourceHook.ON_LLM_TOOL_RESPOND),
    }

    @model_validator(mode="after")
    def validate_authoritative_mapping(self) -> EventEnvelope:
        expected_role, expected_hook = self._MAPPINGS[self.event_type]
        if self.role != expected_role or self.source_hook != expected_hook:
            raise ValueError(
                "event_type, role, and source_hook must use an authoritative mapping"
            )
        return self

    @classmethod
    def create(
        cls,
        *,
        event_id: str,
        session_key: SessionKey,
        sequence: int,
        event_type: EventType,
        content: str,
        idempotency_key: str,
        token_count: int,
        created_at: datetime,
    ) -> EventEnvelope:
        role, source_hook = cls._MAPPINGS[event_type]
        return cls(
            event_id=event_id,
            session_key=session_key,
            sequence=sequence,
            event_type=event_type,
            role=role,
            content=content,
            source_hook=source_hook,
            idempotency_key=idempotency_key,
            token_count=token_count,
            created_at=created_at,
        )
