"""Versioned capability and turn-boundary domain models."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

MatchConfidence = Literal["high", "medium", "low"]
Origin = Literal["human", "application", "model", "tool", "unknown"]
Authority = Literal[
    "user",
    "system",
    "developer",
    "assistant",
    "tool",
    "context",
    "unknown",
]
Trust = Literal["trusted", "untrusted", "unknown"]

_CONFIDENCE_VALUES = frozenset({"high", "medium", "low"})


def _require_non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _validate_confidence(value: Any, field: str = "confidence") -> MatchConfidence:
    if value not in _CONFIDENCE_VALUES:
        raise ValueError(f"{field} must be one of: high, medium, low")
    return value


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _require_string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be an array of strings")
    result = tuple(value)
    if any(not isinstance(item, str) or not item for item in result):
        raise ValueError(f"{field} must be an array of non-empty strings")
    return result


def _is_non_negative_index(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True, slots=True)
class CapabilityMatch:
    capability_id: str
    capability_version: str
    confidence: MatchConfidence
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_non_empty_string(self.capability_id, "capability_id")
        _require_non_empty_string(self.capability_version, "capability_version")
        _validate_confidence(self.confidence)
        _require_string_tuple(self.reasons, "reasons")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "capability_version": self.capability_version,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CapabilityMatch:
        data = _require_mapping(value, "capability_match")
        return cls(
            capability_id=_require_non_empty_string(data.get("capability_id"), "capability_id"),
            capability_version=_require_non_empty_string(
                data.get("capability_version"), "capability_version"
            ),
            confidence=_validate_confidence(data.get("confidence")),
            reasons=_require_string_tuple(data.get("reasons"), "reasons"),
        )


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    adapter_id: str
    adapter_version: str
    matches: tuple[CapabilityMatch, ...]
    fallback: bool
    confidence: MatchConfidence

    def __post_init__(self) -> None:
        _require_non_empty_string(self.adapter_id, "adapter_id")
        _require_non_empty_string(self.adapter_version, "adapter_version")
        if not isinstance(self.matches, tuple) or any(
            not isinstance(match, CapabilityMatch) for match in self.matches
        ):
            raise ValueError("matches must be a tuple of CapabilityMatch values")
        if not isinstance(self.fallback, bool):
            raise ValueError("fallback must be a boolean")
        _validate_confidence(self.confidence)

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(match.capability_id for match in self.matches))

    def has(self, capability_id: str) -> bool:
        return any(match.capability_id == capability_id for match in self.matches)

    @property
    def allows_history_exemption(self) -> bool:
        """Only stable, non-fallback resolutions may neutralize historical hits."""
        return not self.fallback and self.confidence != "low"

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "matches": [match.to_dict() for match in self.matches],
            "fallback": self.fallback,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CapabilitySet:
        data = _require_mapping(value, "capability_set")
        matches_value = data.get("matches")
        if not isinstance(matches_value, list):
            raise ValueError("matches must be an array")
        return cls(
            adapter_id=_require_non_empty_string(data.get("adapter_id"), "adapter_id"),
            adapter_version=_require_non_empty_string(
                data.get("adapter_version"), "adapter_version"
            ),
            matches=tuple(CapabilityMatch.from_dict(match) for match in matches_value),
            fallback=data.get("fallback"),
            confidence=_validate_confidence(data.get("confidence")),
        )


@dataclass(frozen=True, slots=True)
class TurnBoundary:
    last_assistant_index: int | None
    current_message_indices: frozenset[int]
    confidence: MatchConfidence
    reason: str

    def __post_init__(self) -> None:
        if self.last_assistant_index is not None and not _is_non_negative_index(
            self.last_assistant_index
        ):
            raise ValueError("last_assistant_index must be non-negative or null")
        if not isinstance(self.current_message_indices, frozenset) or any(
            not _is_non_negative_index(index) for index in self.current_message_indices
        ):
            raise ValueError("current_message_indices must contain non-negative integers")
        _validate_confidence(self.confidence)
        _require_non_empty_string(self.reason, "reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_assistant_index": self.last_assistant_index,
            "current_message_indices": sorted(self.current_message_indices),
            "confidence": self.confidence,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TurnBoundary:
        data = _require_mapping(value, "turn_boundary")
        indices = data.get("current_message_indices")
        if not isinstance(indices, list) or any(
            not _is_non_negative_index(index) for index in indices
        ):
            raise ValueError("current_message_indices must be an array of integers")
        last_assistant_index = data.get("last_assistant_index")
        if last_assistant_index is not None and not _is_non_negative_index(last_assistant_index):
            raise ValueError("last_assistant_index must be an integer or null")
        return cls(
            last_assistant_index=last_assistant_index,
            current_message_indices=frozenset(indices),
            confidence=_validate_confidence(data.get("confidence")),
            reason=_require_non_empty_string(data.get("reason"), "reason"),
        )
