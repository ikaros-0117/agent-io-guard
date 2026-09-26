"""Canonicalize Chat tool calls, tool results, and their pairing metadata."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from ..models import LiteLLMGuardrailRequest
from .models import CapabilityMatch

TOOL_CHAIN_ID = "tool_chain"
TOOL_CHAIN_VERSION = "2026-09-26.1"


@dataclass(frozen=True, slots=True)
class ToolChainRecord:
    origin_ref: str
    kind: Literal["tool_call", "tool_result"]
    call_id: str | None
    text: str


@dataclass(frozen=True, slots=True)
class ToolChainAnalysis:
    records: tuple[ToolChainRecord, ...]
    call_ids: frozenset[str]
    result_ids: frozenset[str]
    unpaired_result_refs: tuple[str, ...]
    duplicate_call_refs: tuple[str, ...]
    conflicting_call_ids: tuple[str, ...]

    @property
    def has_tool_semantics(self) -> bool:
        return bool(self.records)

    def record_for_ref(self, origin_ref: str) -> ToolChainRecord | None:
        return next(
            (record for record in self.records if record.origin_ref == origin_ref),
            None,
        )


def content_blocks(content: Any) -> list[str]:
    """Return text-shaped blocks from a message content field."""
    if content is None:
        return []
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        result: list[str] = []
        for block in content:
            if isinstance(block, str):
                result.append(block)
            elif isinstance(block, Mapping) and isinstance(block.get("text"), str):
                result.append(block["text"])
            elif isinstance(block, Mapping) and block.get("type") == "tool_result":
                result.extend(content_blocks(block.get("content")))
        return result
    if isinstance(content, Mapping) and isinstance(content.get("text"), str):
        return [content["text"]]
    if isinstance(content, (int, float, bool)):
        return [str(content)]
    return []


def structured_tool_items(
    message: Mapping[str, Any],
    ref: str,
    role: str,
) -> list[tuple[str, str, str]]:
    """Return ``(origin_ref, content_type, text)`` for structured tool data."""
    result: list[tuple[str, str, str]] = []
    calls = list(message.get("tool_calls") or [])
    if isinstance(message.get("function_call"), dict):
        calls.append({"function": message["function_call"]})
    content = message.get("content")
    if isinstance(content, list):
        for block_index, block in enumerate(content):
            if not isinstance(block, Mapping):
                continue
            block_type = block.get("type")
            if block_type in {"tool_use", "function_call"}:
                calls.append(block)
            elif block_type in {"tool_result", "function_call_output"}:
                value = block.get("content", block.get("output"))
                values = content_blocks(value)
                for value_index, text in enumerate(values):
                    result.append(
                        (
                            f"{ref}.content[{block_index}].content[{value_index}]",
                            "tool_result",
                            text,
                        )
                    )
    if message.get("type") == "function_call_output":
        for value_index, text in enumerate(content_blocks(message.get("output"))):
            result.append((f"{ref}.output[{value_index}]", "tool_result", text))
    if message.get("type") == "function_call":
        calls.append(message)
    for call_index, call in enumerate(calls):
        if not isinstance(call, Mapping):
            continue
        function = call.get("function") or call
        arguments = (
            function.get("arguments", function.get("input"))
            if isinstance(function, Mapping)
            else None
        )
        if arguments is None:
            continue
        value = (
            arguments
            if isinstance(arguments, str)
            else json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        )
        result.append(
            (
                f"{ref}.tool_calls[{call_index}].function.arguments",
                "tool_call",
                value,
            )
        )
    return result


class ToolChainCapability:
    capability_id = TOOL_CHAIN_ID
    capability_version = TOOL_CHAIN_VERSION

    def analyze(self, payload: LiteLLMGuardrailRequest) -> ToolChainAnalysis:
        records: list[ToolChainRecord] = []
        call_arguments: dict[str, set[str]] = {}
        call_id_to_first_ref: dict[str, str] = {}
        duplicate_call_refs: list[str] = []

        for message_index, message in enumerate(payload.structured_messages or []):
            if not isinstance(message, Mapping):
                continue
            role = str(message.get("role") or "unknown").lower()
            prefix = f"structured_messages[{message_index}]"
            for origin_ref, content_type, text in structured_tool_items(
                message,
                prefix,
                role,
            ):
                if content_type == "tool_call":
                    call_id = _call_id(message, origin_ref)
                    records.append(
                        ToolChainRecord(origin_ref, "tool_call", call_id, text)
                    )
                    if call_id is not None:
                        call_arguments.setdefault(call_id, set()).add(text)
                        if call_id in call_id_to_first_ref:
                            duplicate_call_refs.append(origin_ref)
                        else:
                            call_id_to_first_ref[call_id] = origin_ref
                elif content_type == "tool_result":
                    records.append(
                        ToolChainRecord(
                            origin_ref,
                            "tool_result",
                            _result_call_id(message),
                            text,
                        )
                    )

            if role == "tool":
                for block_index, text in enumerate(_direct_text_blocks(message.get("content"))):
                    records.append(
                        ToolChainRecord(
                            f"{prefix}.content[{block_index}]",
                            "tool_result",
                            _result_call_id(message),
                            text,
                        )
                    )

        for call_index, call in enumerate(payload.tool_calls or []):
            if not isinstance(call, Mapping):
                continue
            function = call.get("function") or call
            arguments = (
                function.get("arguments", function.get("input"))
                if isinstance(function, Mapping)
                else None
            )
            if arguments is None:
                continue
            text = (
                arguments
                if isinstance(arguments, str)
                else json.dumps(arguments, ensure_ascii=False, sort_keys=True)
            )
            call_id = call.get("id")
            call_id = call_id if isinstance(call_id, str) and call_id else None
            origin_ref = f"tool_calls[{call_index}].function.arguments"
            records.append(ToolChainRecord(origin_ref, "tool_call", call_id, text))
            if call_id is not None:
                call_arguments.setdefault(call_id, set()).add(text)
                if call_id in call_id_to_first_ref:
                    duplicate_call_refs.append(origin_ref)
                else:
                    call_id_to_first_ref[call_id] = origin_ref

        call_ids = frozenset(call_arguments)
        result_records = [record for record in records if record.kind == "tool_result"]
        result_ids = frozenset(
            record.call_id
            for record in result_records
            if record.call_id is not None
        )
        unpaired_result_refs = tuple(
            record.origin_ref
            for record in result_records
            if record.call_id is None or record.call_id not in call_ids
        )
        conflicting_call_ids = tuple(
            sorted(
                call_id
                for call_id, arguments in call_arguments.items()
                if len(arguments) > 1
            )
        )
        return ToolChainAnalysis(
            records=tuple(records),
            call_ids=call_ids,
            result_ids=result_ids,
            unpaired_result_refs=unpaired_result_refs,
            duplicate_call_refs=tuple(duplicate_call_refs),
            conflicting_call_ids=conflicting_call_ids,
        )

    def match(self, payload: LiteLLMGuardrailRequest) -> CapabilityMatch | None:
        if payload.input_type != "request":
            return None
        analysis = self.analyze(payload)
        if not analysis.has_tool_semantics:
            return None
        reasons = ["rule:tool_chain_structure"]
        if analysis.duplicate_call_refs:
            reasons.append("rule:duplicate_tool_projection")
        if analysis.unpaired_result_refs:
            reasons.append("rule:unpaired_tool_result")
        if analysis.conflicting_call_ids:
            reasons.append("rule:conflicting_tool_call")
        return CapabilityMatch(
            capability_id=self.capability_id,
            capability_version=self.capability_version,
            confidence="high",
            reasons=tuple(reasons),
        )


def _call_id(message: Mapping[str, Any], origin_ref: str) -> str | None:
    match = re.search(r"\.tool_calls\[(\d+)\]\.function\.arguments$", origin_ref)
    message_calls = message.get("tool_calls")
    if match and isinstance(message_calls, list):
        call_index = int(match.group(1))
        if call_index < len(message_calls):
            call = message_calls[call_index]
            if isinstance(call, Mapping):
                call_id = call.get("id")
                if isinstance(call_id, str) and call_id:
                    return call_id
    call_id = message.get("id")
    return call_id if isinstance(call_id, str) and call_id else None


def _result_call_id(message: Mapping[str, Any]) -> str | None:
    call_id = message.get("tool_call_id") or message.get("call_id")
    return call_id if isinstance(call_id, str) and call_id else None


def _direct_text_blocks(content: Any) -> list[str]:
    if content is None:
        return []
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        values: list[str] = []
        for block in content:
            if isinstance(block, str):
                values.append(block)
            elif isinstance(block, Mapping) and isinstance(block.get("text"), str):
                values.append(block["text"])
        return values
    if isinstance(content, Mapping) and isinstance(content.get("text"), str):
        return [content["text"]]
    return []
