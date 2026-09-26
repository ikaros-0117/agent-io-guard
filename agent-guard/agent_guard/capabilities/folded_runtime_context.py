"""Detect application/runtime context folded into OpenAI Chat ``role=user``."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from ..models import LiteLLMGuardrailRequest
from .models import Authority, CapabilityMatch, MatchConfidence, Origin, Trust

FOLDED_RUNTIME_CONTEXT_ID = "folded_runtime_context"
FOLDED_RUNTIME_CONTEXT_VERSION = "2026-09-26.1"
KNOWN_RUNTIME_CONTEXT_PREFIXES = (
    "Current runtime context.",
    "<system-reminder>",
    "<runtime-context>",
    "<environment_context>",
)

RuntimeContextKind = Literal["human", "runtime_context", "unknown_context"]

_UNKNOWN_CONTEXT_TAG = re.compile(
    r"^</?[A-Za-z][A-Za-z0-9:_-]*(?:\s[^>]*)?(?:>|$)"
)


@dataclass(frozen=True, slots=True)
class RuntimeContextClassification:
    kind: RuntimeContextKind
    origin: Origin
    authority: Authority
    trust: Trust
    scope: str
    confidence: MatchConfidence
    rule_id: str


def is_known_runtime_context_text(value: str) -> bool:
    return value.lstrip().startswith(KNOWN_RUNTIME_CONTEXT_PREFIXES)


def classify_user_text(value: str) -> RuntimeContextClassification:
    stripped = value.lstrip()
    if stripped.startswith(KNOWN_RUNTIME_CONTEXT_PREFIXES):
        return RuntimeContextClassification(
            kind="runtime_context",
            origin="application",
            authority="context",
            trust="unknown",
            scope="context",
            confidence="high",
            rule_id="rule:known_runtime_context_prefix",
        )
    if _UNKNOWN_CONTEXT_TAG.match(stripped):
        return RuntimeContextClassification(
            kind="unknown_context",
            origin="unknown",
            authority="context",
            trust="unknown",
            scope="context",
            confidence="low",
            rule_id="rule:unknown_runtime_context_marker",
        )
    return RuntimeContextClassification(
        kind="human",
        origin="human",
        authority="user",
        trust="untrusted",
        scope="current_turn",
        confidence="high",
        rule_id="rule:user_text",
    )


class FoldedRuntimeContextCapability:
    capability_id = FOLDED_RUNTIME_CONTEXT_ID
    capability_version = FOLDED_RUNTIME_CONTEXT_VERSION

    def match(self, payload: LiteLLMGuardrailRequest) -> CapabilityMatch | None:
        if payload.input_type != "request":
            return None
        classifications = [
            classify_user_text(text)
            for text in _user_texts(payload.structured_messages)
        ]
        if any(item.kind == "runtime_context" for item in classifications):
            return CapabilityMatch(
                capability_id=self.capability_id,
                capability_version=self.capability_version,
                confidence="high",
                reasons=("rule:known_runtime_context_prefix", "field:role=user"),
            )
        if any(item.kind == "unknown_context" for item in classifications):
            return CapabilityMatch(
                capability_id=self.capability_id,
                capability_version=self.capability_version,
                confidence="low",
                reasons=("rule:unknown_runtime_context_marker", "field:role=user"),
            )
        return None

    def last_non_synthetic_user_message_index(
        self,
        messages: Sequence[Mapping[str, Any]] | None,
    ) -> int | None:
        if not messages:
            return None
        for message_index in range(len(messages) - 1, -1, -1):
            message = messages[message_index]
            if not isinstance(message, Mapping) or message.get("role") != "user":
                continue
            if any(
                classify_user_text(text).kind == "human"
                for text in _content_texts(message.get("content"))
            ):
                return message_index
        return None


def _user_texts(messages: list[dict[str, Any]] | None) -> tuple[str, ...]:
    values: list[str] = []
    for message in messages or []:
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        values.extend(_content_texts(message.get("content")))
    return tuple(values)


def _content_texts(content: Any) -> tuple[str, ...]:
    if content is None:
        return ()
    if isinstance(content, str):
        return (content,)
    if isinstance(content, list):
        values: list[str] = []
        for block in content:
            if isinstance(block, str):
                values.append(block)
            elif isinstance(block, Mapping) and isinstance(block.get("text"), str):
                values.append(block["text"])
        return tuple(values)
    if isinstance(content, Mapping) and isinstance(content.get("text"), str):
        return (content["text"],)
    return ()
