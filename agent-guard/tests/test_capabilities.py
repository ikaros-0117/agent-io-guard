from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from agent_guard.capabilities import (
    CapabilityAdapter,
    CapabilityMatch,
    CapabilityRegistry,
    CapabilitySet,
    MatchConfidence,
    TurnBoundary,
)
from agent_guard.models import LiteLLMGuardrailRequest


def _match(
    capability_id: str = "folded_runtime_context",
    *,
    confidence: MatchConfidence = "high",
) -> CapabilityMatch:
    return CapabilityMatch(
        capability_id=capability_id,
        capability_version="2026-09-26.1",
        confidence=confidence,
        reasons=("rule:runtime_prefix",),
    )


def test_capability_match_round_trips_without_changing_reason_order() -> None:
    match = CapabilityMatch(
        capability_id="folded_runtime_context",
        capability_version="2026-09-26.1",
        confidence="high",
        reasons=("rule:runtime_prefix", "field:structured_messages"),
    )

    serialized = match.to_dict()
    restored = CapabilityMatch.from_dict(serialized)

    assert restored == match
    assert serialized["reasons"] == [
        "rule:runtime_prefix",
        "field:structured_messages",
    ]


def test_capability_match_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError, match="confidence"):
        CapabilityMatch(
            capability_id="folded_runtime_context",
            capability_version="2026-09-26.1",
            confidence="maybe",  # type: ignore[arg-type]
            reasons=("rule:runtime_prefix",),
        )


def test_capability_set_has_stable_capability_ids_and_round_trips() -> None:
    capability_set = CapabilitySet(
        adapter_id="generic_chat",
        adapter_version="2026-09-26.1",
        matches=(_match(), _match()),
        fallback=False,
        confidence="high",
    )

    assert capability_set.has("folded_runtime_context") is True
    assert capability_set.has("tool_chain") is False
    assert capability_set.capability_ids == ("folded_runtime_context",)
    assert CapabilitySet.from_dict(capability_set.to_dict()) == capability_set
    json.dumps(capability_set.to_dict(), ensure_ascii=False)


def test_turn_boundary_serialization_is_deterministic() -> None:
    boundary = TurnBoundary(
        last_assistant_index=2,
        current_message_indices=frozenset({5, 3, 4}),
        confidence="high",
        reason="last_assistant_boundary",
    )

    serialized = boundary.to_dict()

    assert serialized["current_message_indices"] == [3, 4, 5]
    assert TurnBoundary.from_dict(serialized) == boundary
    json.dumps(serialized)


def test_turn_boundary_rejects_negative_indices() -> None:
    with pytest.raises(ValueError, match="current_message_indices"):
        TurnBoundary(
            last_assistant_index=None,
            current_message_indices=frozenset({-1}),
            confidence="low",
            reason="unknown",
        )


@dataclass(frozen=True, slots=True)
class _FakeCapability:
    capability_id: str
    capability_version: str
    match_result: CapabilityMatch | None

    def match(self, payload: LiteLLMGuardrailRequest) -> CapabilityMatch | None:
        assert payload.input_type == "request"
        return self.match_result


def test_capability_registry_preserves_registration_order() -> None:
    first = _FakeCapability(
        "folded_runtime_context",
        "2026-09-26.1",
        _match(),
    )
    second = _FakeCapability("tool_chain", "2026-09-26.1", None)
    registry = CapabilityRegistry((first, second))
    payload = LiteLLMGuardrailRequest(input_type="request")

    assert registry.capabilities == (first, second)
    assert registry.match_all(payload) == (first.match_result,)
    assert isinstance(first, CapabilityAdapter)


def test_capability_registry_rejects_duplicate_or_mismatched_adapters() -> None:
    first = _FakeCapability("folded_runtime_context", "2026-09-26.1", _match())
    duplicate = _FakeCapability("folded_runtime_context", "2026-09-26.2", None)
    registry = CapabilityRegistry((first,))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(duplicate)

    mismatched = _FakeCapability("folded_runtime_context", "2026-09-26.1", _match("tool_chain"))
    mismatch_registry = CapabilityRegistry((mismatched,))
    with pytest.raises(ValueError, match="match id"):
        mismatch_registry.match_all(LiteLLMGuardrailRequest(input_type="request"))
