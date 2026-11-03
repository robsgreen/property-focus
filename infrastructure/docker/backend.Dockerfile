# syntax=docker/dockerfile:1
#
# Backend images, one build stage per runtime role, all from the same locked environment:
#
#   task       `property-focus <command>`: migrations and one-off `run-task <kind>` runs
#   api        the REST API on port 8000
#   mcp        the MCP adapter on port 8001
#   scheduler  the scheduler daemon over infrastructure/dogfood/scheduler.crontab
#
# Build from the repository root, for example:
#   docker build -f infrastructure/docker/backend.Dockerfile --target api .
#
# Behind a TLS-intercepting proxy, pass its CA bundle as a build secret so dependency
# downloads verify: --secret id=build_ca,src=<bundle.pem>. The bundle never enters an image.

FROM ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c AS uv

FROM python:3.13.18-slim-trixie@sha256:76baad6037690d6e8c4397fbcaef17bca6f19eecef822b708fe8fa3417d30bb5 AS python

# Installs the locked runtime dependencies (no development group), then the backend as an
# editable workspace member, so the package keeps its source-tree layout: the migration
# code finds backend/alembic.ini relative to the package.
FROM python AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export SSL_CERT_FILE=/run/secrets/build_ca; fi; \
    uv sync --frozen --no-dev --package property-focus --no-install-workspace
COPY backend/alembic.ini backend/
COPY backend/migrations backend/migrations
COPY backend/src backend/src
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export SSL_CERT_FILE=/run/secrets/build_ca; fi; \
    uv sync --frozen --no-dev --package property-focus \
    && python -m compileall -q backend/src backend/migrations

FROM python AS runtime
RUN useradd --system --uid 10001 --home-dir /app --no-create-home property-focus
COPY --from=build /app /app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER property-focus

FROM runtime AS task
ENTRYPOINT ["python", "-m", "property_focus.cli"]
CMD ["--help"]

FROM runtime AS api
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]
CMD ["python", "-m", "property_focus.cli", "serve", "api", "--host", "0.0.0.0"]

FROM runtime AS mcp
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/.well-known/oauth-protected-resource', timeout=2)"]
CMD ["python", "-m", "property_focus.cli", "serve", "mcp", "--host", "0.0.0.0"]

FROM runtime AS scheduler
COPY infrastructure/dogfood/scheduler.crontab /app/scheduler.crontab
CMD ["python", "-m", "property_focus.cli", "schedule", "--crontab", "/app/scheduler.crontab"]
