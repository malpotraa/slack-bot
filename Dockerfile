# Multi-stage build for the Slack Assistant production image.
# Stage 1 resolves and installs deps with uv. Stage 2 is a slim runtime.

FROM python:3.12-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1

# Install uv from the official static binary
COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /uvx /usr/local/bin/

WORKDIR /app

# 1) install deps using only the lock + manifest (better cache hits)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) copy source and install the project itself
COPY . .
RUN uv sync --frozen --no-dev


# ── Runtime stage ────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# libpq isn't required for asyncpg, but keep CA roots fresh for outbound HTTPS
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Run as a non-root user
RUN useradd --create-home --uid 1001 app
WORKDIR /app

COPY --from=builder --chown=app:app /app /app

USER app

# Cloud Run injects $PORT (defaults to 8080); our config reads it.
EXPOSE 8080

CMD ["python", "-m", "app.main"]
