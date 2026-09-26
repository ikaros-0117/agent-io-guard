"""Minimal Chat protocol adapter interface used by the capability resolver."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..capabilities.models import MatchConfidence
from ..models import LiteLLMGuardrailRequest


@dataclass(frozen=True, slots=True)
class ProfileMatch:
    adapter_id: str
    adapter_version: str
    confidence: MatchConfidence
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.adapter_id, str) or not self.adapter_id:
            raise ValueError("adapter_id must be a non-empty string")
        if not isinstance(self.adapter_version, str) or not self.adapter_version:
            raise ValueError("adapter_version must be a non-empty string")
        if self.confidence not in {"high", "medium", "low"}:
            raise ValueError("confidence must be one of: high, medium, low")
        if not isinstance(self.reasons, tuple) or any(
            not isinstance(reason, str) or not reason for reason in self.reasons
        ):
            raise ValueError("reasons must be a tuple of non-empty strings")


@runtime_checkable
class ChatProtocolAdapter(Protocol):
    adapter_id: str
    adapter_version: str

    def match(self, payload: LiteLLMGuardrailRequest) -> ProfileMatch | None: ...
