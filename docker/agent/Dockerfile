# docker/agent/Dockerfile
#
# Orchestrator/agent image: runs `python -m eoa.orchestrator.main`.
# No browser automation, no dev tooling — kept as small as possible since it
# is the long-running, internet-isolated core process (see docker-compose.yml
# and scripts/verify_isolation.*).
#
# Build context MUST be the repo root (not this directory), because the
# package build needs pyproject.toml, agent/ and README.md:
#   docker compose build agent   # context: ., dockerfile: docker/agent/Dockerfile

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Jerusalem

WORKDIR /app

# libpq-dev: psycopg[binary] ships its own libpq, but keep build tooling
# available for any source-built wheels (e.g. on platforms without prebuilt
# binary wheels).
RUN apt-get update \
    && apt-get install -y --no-install-recommends --no-install-suggests \
       build-essential \
       libpq-dev \
       tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Copy only what the package build needs first, for layer caching.
COPY pyproject.toml README.md ./
COPY agent/ ./agent/

RUN uv pip install --system -e .

# Runtime mount points (see docker-compose.yml): config is read-only,
# output is where reports/exports land.
RUN mkdir -p /app/config /app/output

CMD ["python", "-m", "eoa.orchestrator.main"]
