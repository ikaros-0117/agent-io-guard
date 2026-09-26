"""Fallback adapter for the standard OpenAI Chat Completions shape."""
from __future__ import annotations

from ..models import LiteLLMGuardrailRequest
from .base import ProfileMatch

GENERIC_CHAT_ADAPTER_VERSION = "2026-09-26.1"
CHAT_ROLES = frozenset({"system", "developer", "user", "assistant", "tool"})


class GenericChatProfile:
    adapter_id = "generic_chat"
    adapter_version = GENERIC_CHAT_ADAPTER_VERSION

    def match(self, payload: LiteLLMGuardrailRequest) -> ProfileMatch | None:
        if payload.input_type != "request":
            return None
        messages = payload.structured_messages
        if not isinstance(messages, list) or not messages:
            return None
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in CHAT_ROLES:
                return None
        return ProfileMatch(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            confidence="high",
            reasons=("field:structured_messages", "rule:openai_chat_roles"),
        )
