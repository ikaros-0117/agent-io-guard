#!/usr/bin/env python3
"""Exercise LiteLLM's real Chat incremental-diff hook against agent-guard.

Run: liteLLM/.venv/bin/python tools/verify_stream_guard_inprocess.py
No external provider or listening port is needed.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "agent-guard"))
os.environ.setdefault("AGENT_GUARD_TOKEN", "verify-agent-guard-token")

import httpx  # noqa: E402
from litellm.proxy._types import UserAPIKeyAuth  # noqa: E402
from litellm.proxy.guardrails.guardrail_hooks.generic_guardrail_api.generic_guardrail_api import (  # noqa: E402
    GenericGuardrailAPI,
)
from litellm.proxy.guardrails.guardrail_hooks.unified_guardrail.unified_guardrail import (  # noqa: E402
    UnifiedLLMGuardrails,
)
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices  # noqa: E402

from agent_guard.api import create_app  # noqa: E402
from agent_guard.config import Settings  # noqa: E402

TOKEN = "verify-agent-guard-token"


class LocalGuardHandler:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(Settings(token=TOKEN))))
        self.responses: list[tuple[int, dict]] = []

    async def post(self, url: str, json: dict | None = None, headers: dict | None = None) -> httpx.Response:
        response = await self.client.post(url, json=json, headers=headers)
        self.responses.append((response.status_code, response.json()))
        return response


async def verify(chunks: list[str], expected: str, *, dynamic_holdback: bool = False) -> None:
    guard = GenericGuardrailAPI(
        api_base="http://127.0.0.1:8001", api_key=TOKEN, guardrail_name="agent-guard",
        event_hook=["post_call"], default_on=True,
        streaming_transform_mode="incremental_diff", streaming_sampling_rate=1,
    )
    handler = LocalGuardHandler()
    guard.async_handler = handler  # type: ignore[assignment]

    async def upstream():
        for content in chunks:
            yield ModelResponseStream(
                id="mock-stream", model="mock", choices=[
                    StreamingChoices(index=0, delta=Delta(content=content), finish_reason=None)
                ],
            )
        yield ModelResponseStream(
            id="mock-stream", model="mock", choices=[
                StreamingChoices(index=0, delta=Delta(content=""), finish_reason="stop")
            ],
        )

    emitted: list[str] = []
    async for chunk in UnifiedLLMGuardrails().async_post_call_streaming_iterator_hook(
        UserAPIKeyAuth(api_key="sk-test", request_route="/chat/completions"),
        upstream(), {"model": "mock", "guardrail_to_apply": guard}, guardrail_to_apply=guard,
    ):
        emitted.extend(choice.delta.content or "" for choice in chunk.choices)
    actual = "".join(emitted)
    assert actual == expected, (actual[:300], expected[:300])
    assert handler.responses and all(code == 200 for code, _ in handler.responses)
    assert all(body.get("stream_holdback_chars", [0])[0] >= 64 for _, body in handler.responses)
    if dynamic_holdback:
        assert any(body["stream_holdback_chars"][0] > 64 for _, body in handler.responses)
    await handler.client.aclose()


async def main() -> None:
    await verify(
        ["key sk-proj-", "abcdefghijklmnopqrstuvwxyz0123456789", " end"],
        "key [REDACTED_SECRET] end",
    )
    print("[PASS] split secret: client receives only redacted deltas")
    await verify(
        ["intro -----BEGIN PRIVATE KEY-----", "A" * 200, "-----END PRIVATE KEY----- outro"],
        "intro [REDACTED_PRIVATE_KEY] outro", dynamic_holdback=True,
    )
    print("[PASS] split private key: unfinished block held until redacted")
    await verify(["A" * 49_000, "B" * 52_000], "A" * 49_000 + "B" * 52_000)
    print("[PASS] 101000-character stream: no 413 and no interruption")
    await verify(
        ["A" * 49_000 + " sk-proj-", "abcdefghijklmnopqrstuvwxyz0123456789", " " + "B" * 52_000],
        "A" * 49_000 + " [REDACTED_SECRET] " + "B" * 52_000,
    )
    print("[PASS] long stream with split secret: both redaction and continuity preserved")


if __name__ == "__main__":
    asyncio.run(main())
