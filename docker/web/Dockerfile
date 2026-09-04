# docker/web/Dockerfile
#
# Web/API image: serves the FastAPI app (`uvicorn eoa.api.app:app`) and, when
# available, the built React 19 + Vite static bundle from web/.
#
# The frontend build is optional and gated by both file presence AND the
# BUILD_FRONTEND build ARG, so this image builds cleanly even before web/
# has a package.json (current state of this repo) — it just serves the API
# with an empty static dir in that case.
#
# Build context MUST be the repo root (not this directory):
#   docker compose build web   # context: ., dockerfile: docker/web/Dockerfile

# ---------------------------------------------------------------------------
# Stage 1: optional frontend build (Node 24, multi-stage so it never affects
# the final Python image size when skipped).
# ---------------------------------------------------------------------------
FROM node:24-alpine AS frontend-build

# auto   -> build if web/package.json exists, otherwise skip gracefully
# skip   -> never build, even if package.json exists (fast rebuilds/dev)
# force  -> build; fail loudly if package.json is missing
ARG BUILD_FRONTEND=auto

WORKDIR /frontend
COPY web/ ./

RUN set -eu; \
    mkdir -p dist; \
    if [ "$BUILD_FRONTEND" = "skip" ]; then \
        echo "[web] BUILD_FRONTEND=skip — not building the React app"; \
    elif [ -f package.json ]; then \
        echo "[web] package.json found — building React app"; \
        npm ci; \
        npm run build; \
    elif [ "$BUILD_FRONTEND" = "force" ]; then \
        echo "[web] BUILD_FRONTEND=force but web/package.json is missing" >&2; \
        exit 1; \
    else \
        echo "[web] no web/package.json yet — skipping frontend build (dist/ left empty)"; \
    fi

# ---------------------------------------------------------------------------
# Stage 2: Python API image.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Jerusalem

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends --no-install-suggests \
       build-essential \
       libpq-dev \
       tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./
COPY agent/ ./agent/

RUN uv pip install --system -e .

# Built static assets (may be empty — see frontend-build stage above).
# eoa.api.app is expected to serve this dir (e.g. StaticFiles) when non-empty.
COPY --from=frontend-build /frontend/dist ./web/dist

EXPOSE 8765

CMD ["uvicorn", "eoa.api.app:app", "--host", "0.0.0.0", "--port", "8765"]
