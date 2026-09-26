#!/usr/bin/env python3
"""Real HTTP Proxy + local streamed mock upstream + agent-guard verification.

Run: agent-guard/.venv/bin/python tools/verify_stream_guard_e2e.py
Requires permission to bind 127.0.0.1 ports; no provider key or Internet needed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from verify_input_guard_e2e import Harness, MASTER_KEY

REPO = Path(__file__).resolve().parent.parent
TOKEN = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
CHUNKS = ["Here is a key: sk-proj-", "abcdefghijklmnopqrstuvwxyz0123456789", " and done."]
EXPECTED = "Here is a key: [REDACTED_SECRET] and done."


def read_stream(harness: Harness) -> str:
    streamed: list[str] = []
    errors: list[dict] = []
    with httpx.stream(
        "POST", f"http://127.0.0.1:{harness.proxy_port}/v1/chat/completions",
        headers={"Authorization": f"Bearer {MASTER_KEY}"},
        json={"model": "mock-guard-target", "stream": True,
              "messages": [{"role": "user", "content": "Say hello"}]},
        timeout=90,
    ) as response:
        assert response.status_code == 200, (response.status_code, response.read())
        for line in response.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            frame = json.loads(line.removeprefix("data: "))
            if "error" in frame:
                errors.append(frame)
            for choice in frame.get("choices", []):
                streamed.append(choice.get("delta", {}).get("content") or "")
    assert not errors, errors
    return "".join(streamed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-guard-python", default=str(REPO / "agent-guard/.venv/bin/python"))
    parser.add_argument("--litellm-bin", default=str(REPO / "liteLLM/.venv/bin/litellm"))
    parser.add_argument("--keep-logs", action="store_true")
    args = parser.parse_args()
    harness = Harness(args, stream_chunks=CHUNKS)
    print("logs:", harness.log_dir)
    try:
        harness.start_agent_guard()
        harness.start_proxy()
        content = read_stream(harness)
        assert content == EXPECTED, content
        assert TOKEN not in content
        print("[PASS] HTTP Proxy streamed split secret; client saw only redacted text")
        harness.upstream.stream_chunks = [
            "intro -----BEGIN PRIVATE KEY-----", "A" * 200,
            "-----END PRIVATE KEY----- outro",
        ]
        assert read_stream(harness) == "intro [REDACTED_PRIVATE_KEY] outro"
        print("[PASS] HTTP Proxy streamed split PEM; unfinished key not emitted")
        harness.upstream.stream_chunks = [
            "A" * 49_000 + " sk-proj-", "abcdefghijklmnopqrstuvwxyz0123456789",
            " " + "B" * 52_000,
        ]
        assert read_stream(harness) == (
            "A" * 49_000 + " [REDACTED_SECRET] " + "B" * 52_000
        )
        assert harness.upstream.count() == 3
        print("[PASS] HTTP Proxy streamed 101000+ chars with split secret; no 413")
        return 0
    finally:
        harness.shutdown()


if __name__ == "__main__":
    sys.exit(main())
