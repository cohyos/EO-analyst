# docker/fetcher/Dockerfile
#
# Fetch service image: runs `python -m eoa.fetch.service` (RSS/HTML fetching
# + sanitization). This is the one image that also needs a real browser
# (Playwright + Chromium) for JS-rendered sources, which is heavy — kept in
# its own final stage (`runtime`, built FROM `deps`) so the plain-Python
# dependency layer stays independently cacheable/reusable and the extra
# ~300-400MB of browser + OS deps never leaks into the agent image (a
# separate Dockerfile/image entirely).
#
# Build context MUST be the repo root (not this directory):
#   docker compose build fetcher   # context: ., dockerfile: docker/fetcher/Dockerfile

# ---------------------------------------------------------------------------
# Stage 1: Python package + its declared dependencies only (no Playwright).
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

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

# ---------------------------------------------------------------------------
# Stage 2: add Playwright + Chromium on top of `deps`.
# NOTE: playwright is not in pyproject.toml's dependency list (fetch/ today
# uses feedparser/trafilatura/httpx per docs/CONVENTIONS.md); it is installed
# directly at the image layer here per the fetcher's infra spec, ahead of the
# Python code depending on it. If/when eoa.fetch grows a hard runtime import
# of `playwright`, add it to pyproject.toml's [project.dependencies] too.
# ---------------------------------------------------------------------------
FROM deps AS runtime

ENV TZ=Asia/Jerusalem

RUN pip install --no-cache-dir playwright \
    && playwright install --with-deps chromium

CMD ["python", "-m", "eoa.fetch.service"]
