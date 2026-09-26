FROM ghcr.io/astral-sh/uv:0.12.19-python3.11-trixie-slim AS builder

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM builder AS runtime

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

COPY config.yaml ./config.yaml
COPY chat_only_gateway.py serve_chat_only.py ./

EXPOSE 4000
CMD ["python", "/app/serve_chat_only.py", "--config", "/app/config.yaml", "--host", "0.0.0.0", "--port", "4000"]
