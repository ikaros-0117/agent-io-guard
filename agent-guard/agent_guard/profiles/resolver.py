"""Resolve protocol adaptation and capability matches without applying policy."""
from __future__ import annotations

from ..capabilities.base import CapabilityMatcher, CapabilityRegistry
from ..capabilities.folded_runtime_context import FoldedRuntimeContextCapability
from ..capabilities.models import CapabilityMatch, CapabilitySet, MatchConfidence
from ..capabilities.tool_chain import ToolChainCapability
from ..models import LiteLLMGuardrailRequest
from .base import ChatProtocolAdapter, ProfileMatch
from .generic_chat import GenericChatProfile

_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


class CapabilityResolver:
    def __init__(
        self,
        registry: CapabilityMatcher | None = None,
        generic_profile: ChatProtocolAdapter | None = None,
    ) -> None:
        self.registry = (
            registry
            if registry is not None
            else CapabilityRegistry(
                (FoldedRuntimeContextCapability(), ToolChainCapability())
            )
        )
        self.generic_profile = (
            generic_profile if generic_profile is not None else GenericChatProfile()
        )

    def resolve(self, payload: LiteLLMGuardrailRequest) -> CapabilitySet:
        profile_match = self.generic_profile.match(payload)
        if profile_match is None:
            return self._fallback()
        matches = self.registry.match_all(payload)
        if _has_conflict(matches):
            return self._fallback()
        if profile_match.confidence == "low" or any(
            match.confidence == "low" for match in matches
        ):
            return self._fallback()
        return CapabilitySet(
            adapter_id=profile_match.adapter_id,
            adapter_version=profile_match.adapter_version,
            matches=matches,
            fallback=False,
            confidence=_minimum_confidence(profile_match, matches),
        )

    def _fallback(self) -> CapabilitySet:
        return CapabilitySet(
            adapter_id=self.generic_profile.adapter_id,
            adapter_version=self.generic_profile.adapter_version,
            matches=(),
            fallback=True,
            confidence="low",
        )


def _has_conflict(matches: tuple[CapabilityMatch, ...]) -> bool:
    capability_ids = [match.capability_id for match in matches]
    return len(capability_ids) != len(set(capability_ids))


def _minimum_confidence(
    profile_match: ProfileMatch,
    matches: tuple[CapabilityMatch, ...],
) -> MatchConfidence:
    confidence = profile_match.confidence
    for match in matches:
        if _CONFIDENCE_ORDER[match.confidence] < _CONFIDENCE_ORDER[confidence]:
            confidence = match.confidence
    return confidence
