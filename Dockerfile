FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV HOST=0.0.0.0
ENV PORT=10000
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --no-install-project

COPY server/ server/
COPY config/ config/
RUN uv sync --no-dev --frozen

VOLUME /app/data

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD .venv/bin/python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\", \"10000\")}/health', timeout=3).read()"

CMD ["sh", "-c", "mkdir -p /app/data && exec .venv/bin/start"]
