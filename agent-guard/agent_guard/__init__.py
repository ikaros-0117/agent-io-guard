"""agent-guard L1 static rule detection service."""

from .config import Settings
from .detector import StaticRuleDetector

__all__ = ["Settings", "StaticRuleDetector"]
