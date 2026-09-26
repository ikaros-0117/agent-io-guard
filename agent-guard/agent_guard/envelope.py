"""Input-side semantic alignment for LiteLLM's flat and structured views.

The flat ``texts`` array is the only text rewrite surface exposed by the generic
hook. Structured-only content is still security relevant, but never writable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from .capabilities.folded_runtime_context import is_known_runtime_context_text
from .capabilities.models import Authority, MatchConfidence, Origin, Trust
from .detector import TextItem

ADAPTER_VERSION = "2026-09-26.1"
TRUSTED_ROLES = frozenset({"system", "developer"})


def is_synthetic_user_text(value: str) -> bool:
    """Compatibility wrapper while the capability owns the versioned rules."""
    return is_known_runtime_context_text(value)


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
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                result.extend(content_blocks(block.get("content")))
        return result
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        return [content["text"]]
    if isinstance(content, (int, float, bool)):
        return [str(content)]
    return []


def structured_tool_items(message: dict[str, Any], ref: str, role: str) -> list[tuple[str, str, str]]:
    """Return (origin_ref, content_type, text) for structured tool data."""
    result: list[tuple[str, str, str]] = []
    calls = list(message.get("tool_calls") or [])
    if isinstance(message.get("function_call"), dict):
        calls.append({"function": message["function_call"]})
    content = message.get("content")
    if isinstance(content, list):
        for block_index, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type in {"tool_use", "function_call"}:
                calls.append(block)
            elif block_type in {"tool_result", "function_call_output"}:
                value = block.get("content", block.get("output"))
                values = content_blocks(value)
                for value_index, text in enumerate(values):
                    result.append((f"{ref}.content[{block_index}].content[{value_index}]", "tool_result", text))
    if message.get("type") == "function_call_output":
        for value_index, text in enumerate(content_blocks(message.get("output"))):
            result.append((f"{ref}.output[{value_index}]", "tool_result", text))
    if message.get("type") == "function_call":
        calls.append(message)
    for call_index, call in enumerate(calls):
        if not isinstance(call, dict):
            continue
        function = call.get("function") or call
        arguments = function.get("arguments", function.get("input")) if isinstance(function, dict) else None
        if arguments is None:
            continue
        value = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        result.append((f"{ref}.tool_calls[{call_index}].function.arguments", "tool_call", value))
    return result


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
    text_index: int | None,
    scan: bool,
) -> tuple[Origin, Authority, Trust, bool, MatchConfidence]:
    if role == "system":
        return "application", "system", "trusted", False, "high"
    if role == "developer":
        return "application", "developer", "trusted", False, "high"
    if role == "user":
        return "human", "user", "untrusted", text_index is not None, "high"
    if role == "assistant":
        return "model", "assistant", "unknown", text_index is not None, "high"
    if role == "tool":
        return "tool", "tool", "untrusted", text_index is not None, "high"
    mutable = scan and text_index is not None and content_type != "tool_call"
    return "unknown", "unknown", "unknown", mutable, "low"


@dataclass(frozen=True, slots=True)
class SecurityEnvelope:
    items: tuple[SecurityItem, ...]
    alignment: str
    reason_codes: tuple[str, ...]
    adapter_version: str = ADAPTER_VERSION

    @property
    def scan_items(self) -> list[TextItem]:
        return [item.detection_item() for item in self.items if item.scan and item.text]

    def by_source(self) -> dict[str, SecurityItem]:
        return {item.detection_item().source: item for item in self.items if item.scan}


def build_input_envelope(
    texts: list[str] | None,
    messages: list[dict[str, Any]] | None,
    tool_calls: list[dict[str, Any]] | None,
) -> SecurityEnvelope:
    flat = texts or []
    items: list[SecurityItem] = []
    reasons: list[str] = []
    cursor = 0
    messages = messages or []
    if not messages:
        reasons.append("no_structured_messages")

    def add(ref: str, role: str, scope: str, content_type: str, text: str,
            text_index: int | None = None, scan: bool = True) -> None:
        origin, authority, trust, mutable, confidence = _security_fields(
            role, content_type, text_index, scan
        )
        items.append(SecurityItem(
            item_id=f"litellm:{ref}:{len(items)}", origin_ref=ref,
            text_index=text_index, role=role, scope=scope,
            content_type=content_type, text=text, scan=scan,
            origin=origin, authority=authority, trust=trust,
            mutable=mutable, confidence=confidence,
        ))

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
            add(ref, role, "context" if role in TRUSTED_ROLES else "unresolved",
                "tool_result" if role == "tool" else "text", value,
                text_index, scan=role not in TRUSTED_ROLES)

        for ref, content_type, value in structured_tool_items(message, f"structured_messages[{msg_index}]", role):
            add(ref, "tool" if content_type == "tool_result" else role,
                "current_turn", content_type, value)

    # Unmatched flat entries are scanned as untrusted, but can never receive a
    # historical exemption because their provenance is unknown.
    for index in range(cursor, len(flat)):
        reasons.append("unmapped_text")
        add(f"texts[{index}]", "unknown", "current_turn", "text", flat[index], index)

    for index, call in enumerate(tool_calls or []):
        if not isinstance(call, dict):
            reasons.append("unknown_tool_call")
            continue
        function = call.get("function") or {}
        arguments = function.get("arguments") if isinstance(function, dict) else None
        if arguments is None:
            arguments = call
        value = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        # Top-level calls are an alternate projection of structured calls.
        if any(item.content_type == "tool_call" and item.text == value for item in items):
            continue
        add(f"tool_calls[{index}].function.arguments", "assistant", "current_turn", "tool_call", value)

    # Only aligned user text (excluding known application-generated tail) may be
    # classified as history. The DSH prefix list remains temporary until A2.
    users = [item for item in items if item.role == "user" and item.text_index is not None
             and not is_synthetic_user_text(item.text)]
    latest_message_ref = (users[-1].origin_ref.split(".content[")[0] if users else None)
    aligned = not reasons
    resolved: list[SecurityItem] = []
    for item in items:
        scope = item.scope
        if scope == "unresolved":
            scope = ("current_turn" if latest_message_ref is not None
                     and item.origin_ref.startswith(latest_message_ref + ".content[")
                     else "history")
        resolved.append(replace(item, scope=scope))
    return SecurityEnvelope(tuple(resolved), "aligned" if aligned else "degraded",
                            tuple(dict.fromkeys(reasons)))
