from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import unquote

ZERO_WIDTH_RE = re.compile(r"[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
WHITESPACE_RE = re.compile(r"\s+")


def _decode_limited(value: str) -> str:
    """Decode one or two layers of HTML/URL escaping without unbounded expansion."""
    decoded = value
    for _ in range(2):
        next_value = html.unescape(unquote(decoded))
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


def canonicalize(value: str) -> str:
    """Return the normalized view used for deterministic matching."""
    value = _decode_limited(value)
    value = unicodedata.normalize("NFKC", value)
    value = ZERO_WIDTH_RE.sub("", value)
    return WHITESPACE_RE.sub(" ", value).strip()


def match_view(value: str) -> str:
    """Case-fold and collapse whitespace for high-signal rule matching."""
    return canonicalize(value).casefold()


def redaction_view(value: str) -> str:
    """Normalize without case folding or whitespace collapse.

    Redaction uses this view so unchanged text is not rewritten just because it
    contained tabs, line breaks, or unusual casing.
    """
    value = _decode_limited(value)
    value = unicodedata.normalize("NFKC", value)
    return ZERO_WIDTH_RE.sub("", value)
