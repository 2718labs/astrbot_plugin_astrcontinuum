from __future__ import annotations

import hashlib
import json

from ._base import FrozenEnvelope, NonEmptyStr


class SessionKey(FrozenEnvelope):
    """Canonical seven-field host identity."""

    platform_instance_id: NonEmptyStr
    message_type: NonEmptyStr
    session_id: NonEmptyStr
    group_id: NonEmptyStr | None
    user_id: NonEmptyStr
    conversation_id: NonEmptyStr
    persona_id: NonEmptyStr | None

    def canonical_json(self) -> str:
        ordered = {
            "platform_instance_id": self.platform_instance_id,
            "message_type": self.message_type,
            "session_id": self.session_id,
            "group_id": self.group_id,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "persona_id": self.persona_id,
        }
        return json.dumps(
            ordered,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @property
    def session_key_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
