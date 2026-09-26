"""Capability adapter protocol and deterministic registry."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from ..models import LiteLLMGuardrailRequest
from .models import CapabilityMatch


@runtime_checkable
class CapabilityAdapter(Protocol):
    capability_id: str
    capability_version: str

    def match(self, payload: LiteLLMGuardrailRequest) -> CapabilityMatch | None: ...


class CapabilityRegistry:
    """Register adapters and collect their matches without resolving policy."""

    def __init__(self, capabilities: Iterable[CapabilityAdapter] = ()) -> None:
        self._capabilities: list[CapabilityAdapter] = []
        self._by_id: dict[str, CapabilityAdapter] = {}
        for capability in capabilities:
            self.register(capability)

    @property
    def capabilities(self) -> tuple[CapabilityAdapter, ...]:
        return tuple(self._capabilities)

    def register(self, capability: CapabilityAdapter) -> None:
        capability_id = getattr(capability, "capability_id", None)
        capability_version = getattr(capability, "capability_version", None)
        if not isinstance(capability_id, str) or not capability_id:
            raise ValueError("capability_id must be a non-empty string")
        if not isinstance(capability_version, str) or not capability_version:
            raise ValueError("capability_version must be a non-empty string")
        if capability_id in self._by_id:
            raise ValueError(f"capability already registered: {capability_id}")
        self._capabilities.append(capability)
        self._by_id[capability_id] = capability

    def match_all(self, payload: LiteLLMGuardrailRequest) -> tuple[CapabilityMatch, ...]:
        matches: list[CapabilityMatch] = []
        for capability in self._capabilities:
            match = capability.match(payload)
            if match is None:
                continue
            if match.capability_id != capability.capability_id:
                raise ValueError("capability match id does not match registered adapter")
            if match.capability_version != capability.capability_version:
                raise ValueError("capability match version does not match registered adapter")
            matches.append(match)
        return tuple(matches)
