"""Input-side semantic alignment for LiteLLM's flat and structured views.

The flat ``texts`` array is the only text rewrite surface exposed by the generic
hook. Structured-only content is still security relevant, but never writable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from .capabilities.folded_runtime_context import (
    FOLDED_RUNTIME_CONTEXT_ID,
    classify_user_text,
)
from .capabilities.models import (
    Authority,
    CapabilitySet,
    MatchConfidence,
    Origin,
    Trust,
)
from .capabilities.tool_chain import (
    TOOL_CHAIN_ID,
    ToolChainAnalysis,
    ToolChainCapability,
    structured_tool_items,
)
from .detector import TextItem
from .models import LiteLLMGuardrailRequest

ADAPTER_VERSION = "2026-09-26.1"
GENERIC_CHAT_ADAPTER_ID = "generic_chat"
TRUSTED_ROLES = frozenset({"system", "developer"})

def content_blocks(content: Any) -> list[str]:
    if content is None:
        return []
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        result: list[str] = []
        for block in content:
            if isinstance(block, str):
                result.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                result.append(block["text"])
        return result
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        return [content["text"]]
    if isinstance(content, (int, float, bool)):
        return [str(content)]
    return []


@dataclass(frozen=True, slots=True)
class SecurityItem:
    item_id: str
    origin_ref: str
    text_index: int | None
    role: str
    scope: str
    content_type: str
    text: str
    scan: bool = True
    origin: Origin = "unknown"
    authority: Authority = "unknown"
    trust: Trust = "unknown"
    mutable: bool = False
    confidence: MatchConfidence = "low"
    capability_ids: tuple[str, ...] = ()

    def detection_item(self) -> TextItem:
        return TextItem(
            source=f"texts[{self.text_index}]" if self.text_index is not None else self.origin_ref,
            text=self.text,
            phase="input",
            source_type="tool_call" if self.content_type == "tool_call" else "message",
            rewritable=self.text_index is not None and self.mutable,
        )

    def to_audit_dict(self) -> dict[str, Any]:
        """Serialize audit metadata without returning the source text."""
        return {
            "item_id": self.item_id,
            "origin_ref": self.origin_ref,
            "text_index": self.text_index,
            "role": self.role,
            "origin": self.origin,
            "authority": self.authority,
            "trust": self.trust,
            "scope": self.scope,
            "content_type": self.content_type,
            "mutable": self.mutable,
            "confidence": self.confidence,
            "capability_ids": list(self.capability_ids),
            "scan": self.scan,
        }


def _security_fields(
    role: str,
    content_type: str,
    text: str,
    text_index: int | None,
    scan: bool,
    scope: str,
    capabilities: CapabilitySet,
) -> tuple[Origin, Authority, Trust, str, bool, MatchConfidence, tuple[str, ...]]:
    if role == "user":
        classification = classify_user_text(text)
        if (
            classification.kind == "runtime_context"
            and capabilities.has(FOLDED_RUNTIME_CONTEXT_ID)
        ):
            return (
                "application",
                "context",
                "unknown",
                "context",
                text_index is not None,
                classification.confidence,
                (FOLDED_RUNTIME_CONTEXT_ID,),
            )
        if classification.kind == "unknown_context":
            return (
                "unknown",
                "context",
                "unknown",
                "context",
                text_index is not None,
                classification.confidence,
                (),
            )
    if role == "system":
        return "application", "system", "trusted", scope, False, "high", ()
    if role == "developer":
        return "application", "developer", "trusted", scope, False, "high", ()
    if role == "user":
        return "human", "user", "untrusted", scope, text_index is not None, "high", ()
    if role == "assistant":
        return "model", "assistant", "unknown", scope, text_index is not None, "high", ()
    if role == "tool":
        return "tool", "tool", "untrusted", scope, text_index is not None, "high", ()
    mutable = scan and text_index is not None and content_type != "tool_call"
    return "unknown", "unknown", "unknown", scope, mutable, "low", ()


def _apply_tool_chain(
    items: list[SecurityItem],
    analysis: ToolChainAnalysis,
    capabilities: CapabilitySet,
) -> tuple[list[SecurityItem], tuple[str, ...]]:
    tool_chain_enabled = capabilities.has(TOOL_CHAIN_ID)
    unpaired_result_refs = set(analysis.unpaired_result_refs)
    resolved: list[SecurityItem] = []
    for item in items:
        record = analysis.record_for_ref(item.origin_ref)
        if record is None:
            resolved.append(item)
            continue

        capability_ids = item.capability_ids
        if tool_chain_enabled:
            capability_ids = tuple(
                dict.fromkeys((*capability_ids, TOOL_CHAIN_ID))
            )

        if item.origin_ref in unpaired_result_refs:
            resolved.append(
                replace(
                    item,
                    origin="unknown",
                    authority="unknown",
                    trust="unknown",
                    confidence="low",
                    capability_ids=capability_ids,
                )
            )
            continue
        resolved.append(replace(item, capability_ids=capability_ids))

    reasons: list[str] = []
    if analysis.conflicting_call_ids:
        reasons.append("tool_chain_conflicting_call")
    if analysis.unpaired_result_refs:
        reasons.append("tool_chain_unpaired_result")
    return resolved, tuple(reasons)


@dataclass(frozen=True, slots=True)
class SecurityEnvelope:
    items: tuple[SecurityItem, ...]
    alignment: str
    reason_codes: tuple[str, ...]
    adapter_version: str = ADAPTER_VERSION
    adapter_id: str = GENERIC_CHAT_ADAPTER_ID
    fallback: bool = False
    confidence: MatchConfidence = "high"
    capability_ids: tuple[str, ...] = ()

    @property
    def allows_history_exemption(self) -> bool:
        return (
            self.alignment == "aligned"
            and not self.fallback
            and self.confidence != "low"
        )

    @property
    def scan_items(self) -> list[TextItem]:
        return [item.detection_item() for item in self.items if item.scan and item.text]

    def by_source(self) -> dict[str, SecurityItem]:
        return {item.detection_item().source: item for item in self.items if item.scan}


class ChatEnvelopeBuilder:
    def build(
        self,
        payload: LiteLLMGuardrailRequest,
        capabilities: CapabilitySet,
    ) -> SecurityEnvelope:
        if payload.input_type != "request":
            raise ValueError("ChatEnvelopeBuilder only supports request payloads")

        flat = list(payload.texts or [])
        messages = payload.structured_messages or []
        tool_calls = payload.tool_calls or []
        tool_chain_analysis = ToolChainCapability().analyze(payload)
        items: list[SecurityItem] = []
        reasons: list[str] = []
        cursor = 0
        if not messages:
            reasons.append("no_structured_messages")
        if capabilities.fallback or capabilities.confidence == "low":
            reasons.append("capability_fallback")

        def add(
            ref: str,
            role: str,
            scope: str,
            content_type: str,
            text: str,
            text_index: int | None = None,
            scan: bool = True,
        ) -> None:
            origin, authority, trust, resolved_scope, mutable, confidence, capability_ids = (
                _security_fields(
                    role,
                    content_type,
                    text,
                    text_index,
                    scan,
                    scope,
                    capabilities,
                )
            )
            items.append(
                SecurityItem(
                    item_id=f"litellm:{ref}:{len(items)}",
                    origin_ref=ref,
                    text_index=text_index,
                    role=role,
                    scope=resolved_scope,
                    content_type=content_type,
                    text=text,
                    scan=scan,
                    origin=origin,
                    authority=authority,
                    trust=trust,
                    mutable=mutable,
                    confidence=confidence,
                    capability_ids=capability_ids,
                )
            )

        # Match by value AND order, never by an assumed shared array index. A trusted
        # instruction can appear in structured_messages but be absent from texts.
        for msg_index, message in enumerate(messages):
            role = str(message.get("role") or "unknown").lower()
            if role == "unknown":
                reasons.append("unknown_role")
            for block_index, value in enumerate(content_blocks(message.get("content"))):
                ref = f"structured_messages[{msg_index}].content[{block_index}]"
                matched = cursor < len(flat) and flat[cursor] == value
                text_index = cursor if matched else None
                if matched:
                    cursor += 1
                elif role not in TRUSTED_ROLES and role != "tool":
                    reasons.append("texts_content_mismatch")
                add(
                    ref,
                    role,
                    "context" if role in TRUSTED_ROLES else "unresolved",
                    "tool_result" if role == "tool" else "text",
                    value,
                    text_index,
                    scan=role not in TRUSTED_ROLES,
                )

            for ref, content_type, value in structured_tool_items(
                message,
                f"structured_messages[{msg_index}]",
                role,
            ):
                add(
                    ref,
                    "tool" if content_type == "tool_result" else role,
                    "current_turn",
                    content_type,
                    value,
                )

        # Unmatched flat entries are scanned as untrusted, but can never receive a
        # historical exemption because their provenance is unknown.
        for index in range(cursor, len(flat)):
            reasons.append("unmapped_text")
            add(
                f"texts[{index}]",
                "unknown",
                "current_turn",
                "text",
                flat[index],
                index,
            )

        for index, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                reasons.append("unknown_tool_call")
                continue
            function = call.get("function") or {}
            arguments = function.get("arguments") if isinstance(function, dict) else None
            if arguments is None:
                arguments = call
            value = (
                arguments
                if isinstance(arguments, str)
                else json.dumps(arguments, ensure_ascii=False, sort_keys=True)
            )
            origin_ref = f"tool_calls[{index}].function.arguments"
            # Top-level calls are an alternate projection of structured calls.
            record = tool_chain_analysis.record_for_ref(origin_ref)
            if (
                record is not None
                and record.call_id is not None
                and origin_ref in tool_chain_analysis.duplicate_call_refs
                and record.call_id not in tool_chain_analysis.conflicting_call_ids
            ):
                continue
            add(
                origin_ref,
                "assistant",
                "current_turn",
                "tool_call",
                value,
            )

        items, tool_chain_reasons = _apply_tool_chain(
            items,
            tool_chain_analysis,
            capabilities,
        )
        reasons.extend(tool_chain_reasons)

        if any(item.origin == "unknown" and item.authority == "context" for item in items):
            reasons.append("unknown_runtime_context_marker")
        declared_folded = capabilities.has(FOLDED_RUNTIME_CONTEXT_ID)
        applied_folded = any(
            FOLDED_RUNTIME_CONTEXT_ID in item.capability_ids for item in items
        )
        if declared_folded and not applied_folded:
            reasons.append("capability_not_applied")
        declared_tool_chain = capabilities.has(TOOL_CHAIN_ID)
        applied_tool_chain = any(
            TOOL_CHAIN_ID in item.capability_ids for item in items
        )
        if (
            declared_tool_chain
            and tool_chain_analysis.has_tool_semantics
            and not applied_tool_chain
        ):
            reasons.append("capability_not_applied")

        # Only confirmed human text may define the current-turn boundary. Known
        # synthetic tails do not shift the last real user.
        users = [
            item
            for item in items
            if item.role == "user"
            and item.text_index is not None
            and classify_user_text(item.text).kind != "runtime_context"
        ]
        latest_message_ref = (
            users[-1].origin_ref.split(".content[")[0] if users else None
        )
        resolved: list[SecurityItem] = []
        for item in items:
            scope = item.scope
            if scope == "unresolved":
                scope = (
                    "current_turn"
                    if latest_message_ref is not None
                    and item.origin_ref.startswith(latest_message_ref + ".content[")
                    else "history"
                )
            resolved.append(replace(item, scope=scope))

        return SecurityEnvelope(
            items=tuple(resolved),
            alignment="aligned" if not reasons else "degraded",
            reason_codes=tuple(dict.fromkeys(reasons)),
            adapter_version=capabilities.adapter_version,
            adapter_id=capabilities.adapter_id,
            fallback=capabilities.fallback,
            confidence=capabilities.confidence,
            capability_ids=capabilities.capability_ids,
        )


def build_input_envelope(
    texts: list[str] | None,
    messages: list[dict[str, Any]] | None,
    tool_calls: list[dict[str, Any]] | None,
) -> SecurityEnvelope:
    """Compatibility wrapper for callers that already hold the Chat projection."""
    payload = LiteLLMGuardrailRequest(
        input_type="request",
        texts=texts,
        structured_messages=messages,
        tool_calls=tool_calls,
    )
    capabilities = CapabilitySet(
        adapter_id=GENERIC_CHAT_ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        matches=(),
        fallback=False,
        confidence="high",
    )
    return ChatEnvelopeBuilder().build(payload, capabilities)
