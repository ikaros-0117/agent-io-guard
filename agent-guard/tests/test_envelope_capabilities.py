from __future__ import annotations

import json

from agent_guard.envelope import build_input_envelope


def test_security_item_exposes_capability_and_provenance_metadata() -> None:
    envelope = build_input_envelope(
        ["Follow the policy.", "Summarize the status."],
        [
            {"role": "system", "content": "Follow the policy."},
            {"role": "user", "content": "Summarize the status."},
        ],
        None,
    )

    system_item, user_item = envelope.items
    assert (
        system_item.origin,
        system_item.authority,
        system_item.trust,
        system_item.mutable,
        system_item.confidence,
        system_item.capability_ids,
    ) == ("application", "system", "trusted", False, "high", ())
    assert (
        user_item.origin,
        user_item.authority,
        user_item.trust,
        user_item.mutable,
        user_item.confidence,
        user_item.capability_ids,
    ) == ("human", "user", "untrusted", True, "high", ())


def test_tool_call_item_is_non_mutable_and_has_audit_metadata() -> None:
    envelope = build_input_envelope(
        ["Continue after the tool call."],
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-fixture-tool-1",
                        "type": "function",
                        "function": {
                            "name": "shell",
                            "arguments": '{"command":"rm -rf /"}',
                        },
                    }
                ],
            },
            {"role": "user", "content": "Continue after the tool call."},
        ],
        [
            {
                "id": "call-fixture-tool-1",
                "type": "function",
                "function": {
                    "name": "shell",
                    "arguments": '{"command":"rm -rf /"}',
                },
            }
        ],
    )
    tool_call = next(item for item in envelope.items if item.content_type == "tool_call")

    assert tool_call.origin == "model"
    assert tool_call.authority == "assistant"
    assert tool_call.trust == "unknown"
    assert tool_call.mutable is False
    assert tool_call.confidence == "high"
    audit = tool_call.to_audit_dict()
    assert "text" not in audit
    assert audit["item_id"] == tool_call.item_id
    assert audit["capability_ids"] == []
    json.dumps(audit, ensure_ascii=False)


def test_unmapped_text_remains_low_confidence_and_strictly_scannable() -> None:
    envelope = build_input_envelope(["unmapped text"], [], None)
    item = envelope.items[0]

    assert item.origin == "unknown"
    assert item.authority == "unknown"
    assert item.trust == "unknown"
    assert item.mutable is True
    assert item.confidence == "low"
    assert item.scope == "current_turn"
    assert item.scan is True
