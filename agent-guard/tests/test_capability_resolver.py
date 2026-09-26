from __future__ import annotations

import json
import logging

from agent_guard.capabilities import CapabilityMatch, MatchConfidence
from agent_guard.models import LiteLLMGuardrailRequest
from agent_guard.profiles import CapabilityResolver, ProfileMatch


class _StaticRegistry:
    def __init__(self, matches: tuple[CapabilityMatch, ...] = ()) -> None:
        self.matches = matches

    def match_all(self, payload: LiteLLMGuardrailRequest) -> tuple[CapabilityMatch, ...]:
        return self.matches


class _LowConfidenceProfile:
    adapter_id = "generic_chat"
    adapter_version = "2026-09-26.1"

    def match(self, payload: LiteLLMGuardrailRequest) -> ProfileMatch:
        return ProfileMatch(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            confidence="low",
            reasons=("rule:uncertain_chat_shape",),
        )


class _ExplodingRegistry:
    def match_all(self, payload: LiteLLMGuardrailRequest) -> tuple[CapabilityMatch, ...]:
        raise AssertionError("capability matching must not run without a request profile")


def _chat_payload(
    *,
    text: str = "Summarize the fixture status.",
    headers: dict[str, str] | None = None,
) -> LiteLLMGuardrailRequest:
    return LiteLLMGuardrailRequest(
        input_type="request",
        texts=[text],
        structured_messages=[{"role": "user", "content": text}],
        request_headers=headers,
    )


def _match(
    capability_id: str,
    *,
    confidence: MatchConfidence = "high",
) -> CapabilityMatch:
    return CapabilityMatch(
        capability_id=capability_id,
        capability_version="2026-09-26.1",
        confidence=confidence,
        reasons=("rule:structural_signal",),
    )


def test_valid_generic_chat_resolves_without_fallback() -> None:
    resolution = CapabilityResolver().resolve(_chat_payload())

    assert resolution.adapter_id == "generic_chat"
    assert resolution.adapter_version == "2026-09-26.1"
    assert resolution.matches == ()
    assert resolution.fallback is False
    assert resolution.confidence == "high"
    assert resolution.allows_history_exemption is True


def test_unrecognized_chat_shape_falls_back_to_generic_strict() -> None:
    payload = LiteLLMGuardrailRequest(
        input_type="request",
        texts=["plain text"],
        structured_messages=None,
    )

    resolution = CapabilityResolver().resolve(payload)

    assert resolution.adapter_id == "generic_chat"
    assert resolution.matches == ()
    assert resolution.fallback is True
    assert resolution.confidence == "low"
    assert resolution.allows_history_exemption is False


def test_response_phase_does_not_reidentify_or_reward_history_exemption() -> None:
    payload = LiteLLMGuardrailRequest(
        input_type="response",
        texts=["assistant output"],
    )

    resolution = CapabilityResolver(registry=_ExplodingRegistry()).resolve(payload)

    assert resolution.fallback is True
    assert resolution.confidence == "low"
    assert resolution.allows_history_exemption is False


def test_high_confidence_capabilities_are_preserved() -> None:
    match = _match("folded_runtime_context")
    resolver = CapabilityResolver(registry=_StaticRegistry((match,)))

    resolution = resolver.resolve(_chat_payload())

    assert resolution.matches == (match,)
    assert resolution.capability_ids == ("folded_runtime_context",)
    assert resolution.fallback is False
    assert resolution.confidence == "high"


def test_default_registry_detects_known_folded_runtime_context() -> None:
    payload = LiteLLMGuardrailRequest(
        input_type="request",
        structured_messages=[
            {"role": "user", "content": "Summarize the status."},
            {
                "role": "user",
                "content": "Current runtime context. Sanitized runtime marker.",
            },
        ],
    )

    resolution = CapabilityResolver().resolve(payload)

    assert resolution.capability_ids == ("folded_runtime_context",)
    assert resolution.fallback is False
    assert resolution.confidence == "high"


def test_unknown_runtime_context_marker_falls_back_to_generic_strict() -> None:
    payload = LiteLLMGuardrailRequest(
        input_type="request",
        structured_messages=[
            {"role": "user", "content": "<unknown-context>Sanitized marker.</unknown-context>"}
        ],
    )

    resolution = CapabilityResolver().resolve(payload)

    assert resolution.matches == ()
    assert resolution.fallback is True
    assert resolution.confidence == "low"
    assert resolution.allows_history_exemption is False


def test_low_confidence_resolution_disables_history_exemption() -> None:
    resolver = CapabilityResolver(generic_profile=_LowConfidenceProfile())

    resolution = resolver.resolve(_chat_payload())

    assert resolution.fallback is True
    assert resolution.confidence == "low"
    assert resolution.matches == ()
    assert resolution.allows_history_exemption is False


def test_conflicting_capability_ids_fall_back_conservatively() -> None:
    conflict = _match("folded_runtime_context")
    resolver = CapabilityResolver(registry=_StaticRegistry((conflict, conflict)))

    resolution = resolver.resolve(_chat_payload())

    assert resolution.fallback is True
    assert resolution.confidence == "low"
    assert resolution.matches == ()


def test_header_identity_does_not_change_resolution() -> None:
    without_header = CapabilityResolver().resolve(_chat_payload())
    with_header = CapabilityResolver().resolve(
        _chat_payload(headers={"x-client-name": "dsh_chat"})
    )

    assert with_header == without_header


def test_resolver_does_not_log_or_serialize_input_text(caplog) -> None:
    sentinel = "sensitive-resolver-sentinel"
    payload = _chat_payload(text=sentinel)

    with caplog.at_level(logging.DEBUG):
        resolution = CapabilityResolver().resolve(payload)

    serialized = json.dumps(resolution.to_dict(), ensure_ascii=False)
    assert sentinel not in caplog.text
    assert sentinel not in serialized
