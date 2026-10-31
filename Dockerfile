# syntax=docker/dockerfile:1
#
# Cloud Run image for the BSPHO Virtual Assistant Streamlit UI.
#
# Builds the whole `uv` workspace (the `ui` package depends on the `agent`
# package), so a single image serves both the clinician chat and the admin
# document-sync flow.
#
# Build and run locally:
#   docker build -t bspho-ui .
#   docker run --rm -p 8080:8080 \
#     -e UI_AGENT_BACKEND=mock -e UI_MAPPING_SOURCE=mock bspho-ui
#
# !! KNOWN ISSUE — not currently used for deployment !!
# This image runs as the unprivileged `appuser`, but `WORKDIR /app` below
# creates /app owned by root (the --chown on COPY applies to the copied
# contents, not to the directory itself). The agent's GCS mapping fallback in
# packages/agent/src/agent/config.py does an unguarded
#     LOCAL_MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
# which therefore dies with "PermissionError: [Errno 13] ... '/app/data'".
# The UI then silently degrades to the bundled 13-document sample mapping and
# chat requests fail. Verified on Cloud Run revision va-ui-00002-jfr.
#
# To use this image, do ONE of:
#   a) wrap that write in try/except OSError in config.py (it is only a cache), or
#   b) add `RUN mkdir -p /app/data && chown -R appuser:appuser /app` before USER.
# Until then, deploy with deploy/Dockerfile.ui.

FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.4 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies first, in their own layer, so editing application code
# does not invalidate the (slow) dependency resolution and download step.
#
# NOTE: no `--mount=type=cache` here. It requires BuildKit, and Cloud Build's
# `gcr.io/cloud-builders/docker` runs the legacy engine, so a cache mount fails
# with "the --mount option requires BuildKit". Layer caching above still works.
COPY pyproject.toml uv.lock ./
COPY packages/agent/pyproject.toml packages/agent/
COPY packages/ui/pyproject.toml packages/ui/
RUN uv sync --locked --no-dev --package ui --no-install-workspace

COPY packages/ packages/
RUN uv sync --locked --no-dev --package ui


FROM python:3.13-slim AS runtime

# Streamlit needs no build toolchain at runtime; keep the final image small
# and run as an unprivileged user.
RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

COPY --from=builder --chown=appuser:appuser /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Cloud Run terminates TLS and forwards plain HTTP, so disable
    # Streamlit's XSRF/CORS checks that assume a direct origin.
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ENABLE_CORS=false \
    STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=false \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

USER appuser

# Cloud Run injects $PORT (default 8080) and expects the container to listen
# on 0.0.0.0. `data/processed/hospital_mapping.json` is intentionally NOT
# baked into the image: the agent falls back to fetching it from GCS using
# the service account's credentials.
ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/_stcore/health').read()"

CMD exec streamlit run packages/ui/src/ui/app.py \
    --server.port="${PORT}" \
    --server.address=0.0.0.0
