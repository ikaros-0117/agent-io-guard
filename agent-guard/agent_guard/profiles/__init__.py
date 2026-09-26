"""Chat protocol adapters and capability resolution."""

from .base import ChatProtocolAdapter, ProfileMatch
from .generic_chat import GenericChatProfile
from .resolver import CapabilityResolver

__all__ = [
    "CapabilityResolver",
    "ChatProtocolAdapter",
    "GenericChatProfile",
    "ProfileMatch",
]
