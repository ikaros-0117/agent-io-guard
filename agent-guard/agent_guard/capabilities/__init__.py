"""Versioned adapters that explain Chat message semantics."""

from .base import CapabilityAdapter, CapabilityMatcher, CapabilityRegistry
from .models import (
    Authority,
    CapabilityMatch,
    CapabilitySet,
    MatchConfidence,
    Origin,
    Trust,
    TurnBoundary,
)

__all__ = [
    "Authority",
    "CapabilityAdapter",
    "CapabilityMatch",
    "CapabilityMatcher",
    "CapabilityRegistry",
    "CapabilitySet",
    "MatchConfidence",
    "Origin",
    "Trust",
    "TurnBoundary",
]
