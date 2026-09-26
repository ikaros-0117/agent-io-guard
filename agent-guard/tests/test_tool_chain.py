from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.fixtures.chat_clients.loader import FIXTURE_ROOT, load_fixture  # noqa: E402

from agent_guard.capabilities import ToolChainCapability  # noqa: E402
from agent_guard.envelope import ChatEnvelopeBuilder  # noqa: E402
from agent_guard.models import LiteLLMGuardrailRequest  # noqa: E402
from agent_guard.profiles import CapabilityResolver  # noqa: E402


def _fixture_payload(client_id: str, scenario: str) -> LiteLLMGuardrailRequest:
    fixture = load_fixture(FIXTURE_ROOT / client_id / f"{scenario}.json")
    return LiteLLMGuardrailRequest.model_validate(fixture.request)


@pytest.mark.parametrize("client_id", ["generic_chat", "dsh_chat"])
def test_tool_call_fixture_is_recognized_as_tool_chain(client_id: str) -> None:
    payload = _fixture_payload(client_id, "tool_call")

    analysis = ToolChainCapability().analyze(payload)
    match = ToolChainCapability().match(payload)

    assert analysis.call_ids == frozenset({"call-fixture-tool-1"})
    assert analysis.result_ids == frozenset()
    assert analysis.unpaired_result_refs == ()
    assert analysis.conflicting_call_ids == ()
    assert "tool_calls[0].function.arguments" in analysis.duplicate_call_refs
    assert match is not None
    assert match.capability_id == "tool_chain"
    assert match.capability_version == "2026-09-26.1"
    assert match.confidence == "high"
    assert match.reasons == (
        "rule:tool_chain_structure",
        "rule:duplicate_tool_projection",
    )


@pytest.mark.parametrize("client_id", ["generic_chat", "dsh_chat"])
def test_tool_result_fixture_pairs_call_and_result(client_id: str) -> None:
    payload = _fixture_payload(client_id, "tool_result")

    analysis = ToolChainCapability().analyze(payload)

    assert analysis.call_ids == frozenset({"call-fixture-tool-result-1"})
    assert analysis.result_ids == frozenset({"call-fixture-tool-result-1"})
    assert analysis.unpaired_result_refs == ()
    assert analysis.conflicting_call_ids == ()


def test_multiple_tool_calls_keep_their_own_ids() -> None:
    payload = LiteLLMGuardrailRequest(
        input_type="request",
        structured_messages=[
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-a",
                        "type": "function",
                        "function": {"name": "shell", "arguments": '{"command":"a"}'},
                    },
                    {
                        "id": "call-b",
                        "type": "function",
                        "function": {"name": "shell", "arguments": '{"command":"b"}'},
                    },
                ],
            }
        ],
    )

    analysis = ToolChainCapability().analyze(payload)

    assert analysis.call_ids == frozenset({"call-a", "call-b"})
    assert analysis.unpaired_result_refs == ()


def test_unpaired_tool_result_is_low_confidence_and_degraded() -> None:
    payload = LiteLLMGuardrailRequest(
        input_type="request",
        texts=["Sanitized tool output."],
        structured_messages=[
            {
                "role": "tool",
                "tool_call_id": "missing-call",
                "content": "Sanitized tool output.",
            }
        ],
    )

    analysis = ToolChainCapability().analyze(payload)
    capabilities = CapabilityResolver().resolve(payload)
    envelope = ChatEnvelopeBuilder().build(payload, capabilities)
    item = envelope.items[0]

    assert analysis.unpaired_result_refs == (
        "structured_messages[0].content[0]",
    )
    assert capabilities.has("tool_chain")
    assert envelope.alignment == "degraded"
    assert "tool_chain_unpaired_result" in envelope.reason_codes
    assert envelope.allows_history_exemption is False
    assert item.origin == "unknown"
    assert item.authority == "unknown"
    assert item.trust == "unknown"
    assert item.confidence == "low"
    assert item.capability_ids == ("tool_chain",)


def test_conflicting_duplicate_projection_is_degraded() -> None:
    payload = LiteLLMGuardrailRequest(
        input_type="request",
        structured_messages=[
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-conflict",
                        "type": "function",
                        "function": {
                            "name": "shell",
                            "arguments": '{"command":"first"}',
                        },
                    }
                ],
            }
        ],
        tool_calls=[
            {
                "id": "call-conflict",
                "type": "function",
                "function": {
                    "name": "shell",
                    "arguments": '{"command":"second"}',
                },
            }
        ],
    )

    analysis = ToolChainCapability().analyze(payload)
    capabilities = CapabilityResolver().resolve(payload)
    envelope = ChatEnvelopeBuilder().build(payload, capabilities)

    assert analysis.conflicting_call_ids == ("call-conflict",)
    assert envelope.alignment == "degraded"
    assert "tool_chain_conflicting_call" in envelope.reason_codes
    assert envelope.allows_history_exemption is False


def test_builder_applies_tool_chain_capability_and_disables_rewrite() -> None:
    payload = _fixture_payload("generic_chat", "tool_call")
    capabilities = CapabilityResolver().resolve(payload)

    envelope = ChatEnvelopeBuilder().build(payload, capabilities)
    tool_item = next(
        item for item in envelope.items if item.content_type == "tool_call"
    )

    assert envelope.capability_ids == ("tool_chain",)
    assert envelope.fallback is False
    assert envelope.confidence == "high"
    assert envelope.alignment == "aligned"
    assert tool_item.origin == "model"
    assert tool_item.authority == "assistant"
    assert tool_item.trust == "unknown"
    assert tool_item.mutable is False
    assert tool_item.capability_ids == ("tool_chain",)
