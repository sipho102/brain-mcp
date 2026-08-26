# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm AS base

# ripgrep for content search (see src/brain_mcp/search.py); curl for the healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends ripgrep curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# UID/GID the container runs as. Must be able to write /vault/00-inbox on
# the Unraid share; 99:100 is Unraid's nobody:users. Passed as raw numeric
# IDs to USER below (no /etc/passwd entry required) so this works
# regardless of what the base image's existing users/groups look like.
ARG APP_UID=99
ARG APP_GID=100

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

USER ${APP_UID}:${APP_GID}

EXPOSE 3100

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-3100}/health" || exit 1

ENTRYPOINT ["brain-mcp"]
