"""Versioned adapters that explain Chat message semantics."""

from .base import CapabilityAdapter, CapabilityMatcher, CapabilityRegistry
from .folded_runtime_context import (
    FOLDED_RUNTIME_CONTEXT_ID,
    FOLDED_RUNTIME_CONTEXT_VERSION,
    FoldedRuntimeContextCapability,
    RuntimeContextClassification,
    classify_user_text,
    is_known_runtime_context_text,
)
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
    "FOLDED_RUNTIME_CONTEXT_ID",
    "FOLDED_RUNTIME_CONTEXT_VERSION",
    "FoldedRuntimeContextCapability",
    "MatchConfidence",
    "Origin",
    "RuntimeContextClassification",
    "Trust",
    "TurnBoundary",
    "classify_user_text",
    "is_known_runtime_context_text",
]
