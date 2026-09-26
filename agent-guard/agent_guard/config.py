from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    token: str
    policy_version: str = "2026-09-21.1"
    max_texts: int = 100
    max_text_chars: int = 50_000
    max_total_chars: int = 200_000
    stream_holdback_chars: int = 64
    max_output_text_chars: int = 1_000_000
    max_output_total_chars: int = 2_000_000
    alignment_mode: str = "strict"

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("AGENT_GUARD_TOKEN", "").strip()
        if not token:
            raise ValueError(
                "AGENT_GUARD_TOKEN is required; copy .env.example to .env and set a token"
            )

        alignment_mode = os.getenv("AGENT_GUARD_ALIGNMENT", "strict").strip()
        if alignment_mode not in {"strict", "degraded_allowed"}:
            raise ValueError("AGENT_GUARD_ALIGNMENT must be strict or degraded_allowed")

        return cls(
            token=token,
            policy_version=os.getenv(
                "AGENT_GUARD_POLICY_VERSION", "2026-09-21.1"
            ).strip(),
            max_texts=_positive_int("AGENT_GUARD_MAX_TEXTS", 100),
            max_text_chars=_positive_int("AGENT_GUARD_MAX_TEXT_CHARS", 50_000),
            max_total_chars=_positive_int("AGENT_GUARD_MAX_TOTAL_CHARS", 200_000),
            stream_holdback_chars=_positive_int("AGENT_GUARD_STREAM_HOLDBACK_CHARS", 64),
            max_output_text_chars=_positive_int("AGENT_GUARD_MAX_OUTPUT_TEXT_CHARS", 1_000_000),
            max_output_total_chars=_positive_int("AGENT_GUARD_MAX_OUTPUT_TOTAL_CHARS", 2_000_000),
            alignment_mode=alignment_mode,
        )
