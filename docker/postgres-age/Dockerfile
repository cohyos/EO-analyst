# docker/postgres-age/Dockerfile
#
# PostgreSQL 17 + Apache AGE (graph) + pgvector (embeddings) for EO-Analyst.
#
# Base: apache/age:release_PG17_1.7.0 — the official Apache AGE image confirmed
# (Docker Hub, checked live) to carry a PostgreSQL-17 build of AGE 1.7.0
# (the release line that supersedes 1.6.0 for PG17; both exist as tags:
# release_PG17_1.6.0 and release_PG17_1.7.0 — 1.7.0 is used here as it is the
# more recent PG17-compatible release). That image already:
#   - is built FROM postgres:17
#   - bakes in age.so + age--1.7.0.sql + age.control
#   - runs `CREATE EXTENSION age;` via /docker-entrypoint-initdb.d/00-create-extension-age.sql
#   - sets CMD ["postgres", "-c", "shared_preload_libraries=age"]
#
# pgvector is NOT part of that image, so it is compiled from source in a
# throwaway builder stage (against the same postgres:17 toolchain AGE itself
# was built with) and the resulting artifacts are copied into the final image.
#
# To rebuild against a newer AGE/pgvector release, bump AGE_BASE_IMAGE and
# PGVECTOR_VERSION below and re-check both projects' PG17 compatibility.

ARG AGE_BASE_IMAGE=apache/age:release_PG17_1.7.0
ARG PGVECTOR_VERSION=v0.8.6

# ---------------------------------------------------------------------------
# Stage 1: build pgvector from source against postgresql-server-dev-17.
# Using plain postgres:17 (not the AGE image) keeps this stage cacheable
# independently of AGE version bumps.
# ---------------------------------------------------------------------------
FROM postgres:17 AS pgvector-build
ARG PGVECTOR_VERSION

RUN apt-get update \
    && apt-get install -y --no-install-recommends --no-install-suggests \
       ca-certificates \
       git \
       build-essential \
       postgresql-server-dev-17 \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --branch "${PGVECTOR_VERSION}" --depth 1 \
        https://github.com/pgvector/pgvector.git /usr/src/pgvector

WORKDIR /usr/src/pgvector
RUN make clean \
    && make OPTFLAGS="" \
    && make install

# ---------------------------------------------------------------------------
# Stage 2: final image — apache/age's PG17 build plus the pgvector extension
# files copied on top. AGE is already fully installed/configured in the base
# image; we only add vector.so + its SQL/control files and our own idempotent
# bootstrap script.
# ---------------------------------------------------------------------------
FROM ${AGE_BASE_IMAGE}

COPY --from=pgvector-build /usr/lib/postgresql/17/lib/vector.so \
     /usr/lib/postgresql/17/lib/vector.so
COPY --from=pgvector-build /usr/share/postgresql/17/extension/vector.control \
     /usr/share/postgresql/17/extension/vector.control
COPY --from=pgvector-build /usr/share/postgresql/17/extension/vector--*.sql \
     /usr/share/postgresql/17/extension/

# Our own idempotent bootstrap (CREATE EXTENSION IF NOT EXISTS age/vector,
# create_graph guarded). Runs after the base image's own
# 00-create-extension-age.sql (alphabetical order in docker-entrypoint-initdb.d),
# and is self-sufficient (re-issues `CREATE EXTENSION IF NOT EXISTS age;`) so it
# also works standalone if this Dockerfile is ever based directly on postgres:17.
COPY init/01-extensions.sql /docker-entrypoint-initdb.d/01-extensions.sql

# shared_preload_libraries=age is required for AGE's Cypher planner hooks and
# must be set at server start (it cannot be changed with ALTER SYSTEM at
# runtime). The base apache/age image already sets this via CMD; we restate it
# explicitly here so this Dockerfile is self-documenting and the setting
# survives even if the base image's own default ever changes. pgvector needs
# no preload entry.
CMD ["postgres", "-c", "shared_preload_libraries=age"]
