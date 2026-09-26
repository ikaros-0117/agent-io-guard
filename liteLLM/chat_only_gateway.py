"""Public Chat-only ingress for the loopback-bound LiteLLM Proxy.

Only explicitly allowed client routes are forwarded. Never expose the backend
port publicly: a direct backend connection bypasses this route boundary.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

ALLOWED_ROUTES = frozenset({("POST", "/v1/chat/completions"), ("GET", "/v1/models")})
REQUEST_HEADERS = frozenset({
    "authorization", "content-type", "accept", "x-request-id", "x-trace-id", "x-tenant-id",
})
RESPONSE_HEADERS = frozenset({
    "content-type", "content-encoding", "cache-control", "x-request-id", "x-litellm-call-id",
})
MAX_BODY_BYTES = 2_000_000


def create_app(backend_url: str | None = None) -> FastAPI:
    backend = (backend_url or os.getenv("LITELLM_BACKEND_URL", "http://127.0.0.1:4001")).rstrip("/")
    parsed = urlsplit(backend)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("LITELLM_BACKEND_URL must be a loopback HTTP origin")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10), trust_env=False) as client:
            app.state.backend_client = client
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    async def forward(path: str, request: Request):
        if (request.method, request.url.path) not in ALLOWED_ROUTES:
            return JSONResponse(status_code=404, content={"error": "route_not_supported"})
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY_BYTES:
                return JSONResponse(status_code=413, content={"error": "request_too_large"})
        headers = {key: value for key, value in request.headers.items() if key.lower() in REQUEST_HEADERS}
        # The ingress forwards raw SSE bytes; do not request compressed output
        # from the loopback backend unless it explicitly chooses to send it.
        headers["accept-encoding"] = "identity"
        client: httpx.AsyncClient = request.app.state.backend_client
        try:
            upstream_request = client.build_request(
                request.method, backend + request.url.path,
                params=request.query_params, headers=headers, content=bytes(body),
            )
            upstream = await client.send(upstream_request, stream=True)
        except httpx.RequestError:
            return JSONResponse(status_code=502, content={"error": "backend_unavailable"})

        async def stream():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()

        response_headers = {key: value for key, value in upstream.headers.items() if key.lower() in RESPONSE_HEADERS}
        return StreamingResponse(stream(), status_code=upstream.status_code, headers=response_headers)

    return app


app = create_app()
