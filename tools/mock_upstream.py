"""OpenAI-compatible mock upstream that records every request it receives.

The E2E verification uses it to answer the only question that matters for the
input-side guard: did the request reach the model, and what content did it carry?

Run standalone for debugging:

    python3 tools/mock_upstream.py --port 9100 --state /tmp/mock_requests.jsonl
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class MockUpstream:
    """Threaded HTTP server exposing POST /v1/chat/completions.

    The assistant message echoes the messages the upstream actually received, so
    a caller can assert on what the model would have seen.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0, state_file: str | None = None,
                 stream_chunks: list[str] | None = None) -> None:
        self._lock = threading.Lock()
        self._requests: list[dict[str, Any]] = []
        self._state_file = state_file
        self.stream_chunks = stream_chunks
        self.port = port
        self._server = ThreadingHTTPServer((host, port), self._handler())
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._requests)

    def count(self) -> int:
        with self._lock:
            return len(self._requests)

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()

    def start(self) -> "MockUpstream":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _record(self, body: dict[str, Any]) -> None:
        with self._lock:
            self._requests.append(body)
        if self._state_file:
            with open(self._state_file, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(body, ensure_ascii=False) + "\n")

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _read_body(self) -> dict[str, Any]:
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    return json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    return {"_raw": raw.decode("utf-8", "replace")}

            def _json(self, payload: dict[str, Any], status: int = 200) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                if self.path.startswith("/v1/models"):
                    self._json(
                        {
                            "object": "list",
                            "data": [{"id": "mock-model", "object": "model", "owned_by": "mock"}],
                        }
                    )
                    return
                self._json({"error": "not found"}, status=404)

            def do_POST(self) -> None:  # noqa: N802
                body = self._read_body()
                upstream._record(body)
                if body.get("stream") and upstream.stream_chunks is not None:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    for index, content in enumerate(upstream.stream_chunks):
                        frame = {
                            "id": "chatcmpl-mock", "object": "chat.completion.chunk",
                            "created": 0, "model": body.get("model", "mock-model"),
                            "choices": [{"index": 0, "delta": {
                                **({"role": "assistant"} if index == 0 else {}), "content": content,
                            }, "finish_reason": None}],
                        }
                        self.wfile.write(("data: " + json.dumps(frame) + "\n\n").encode())
                        self.wfile.flush()
                    end = {
                        "id": "chatcmpl-mock", "object": "chat.completion.chunk",
                        "created": 0, "model": body.get("model", "mock-model"),
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    }
                    self.wfile.write(("data: " + json.dumps(end) + "\n\ndata: [DONE]\n\n").encode())
                    self.wfile.flush()
                    return
                messages = body.get("messages", [])
                echo = "UPSTREAM_SAW:" + json.dumps(messages, ensure_ascii=False)
                self._json(
                    {
                        "id": "chatcmpl-mock",
                        "object": "chat.completion",
                        "created": 0,
                        "model": body.get("model", "mock-model"),
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": echo},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    }
                )

            def log_message(self, *args: Any) -> None:
                return None

        return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--state", default=None)
    args = parser.parse_args()

    upstream = MockUpstream(host=args.host, port=args.port, state_file=args.state).start()
    print(f"mock upstream listening on {upstream.url} (state={args.state})", flush=True)
    try:
        upstream._thread.join()
    except KeyboardInterrupt:
        upstream.stop()


if __name__ == "__main__":
    main()
