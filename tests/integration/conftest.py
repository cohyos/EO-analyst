"""Integration test fixtures for the live EO-Analyst stack.

Provides fixtures for:
- DATABASE_URL: PostgreSQL connection string (env var or default local)
- db_conn: psycopg connection to the live database
- http_client: httpx AsyncClient for the web API
- Base utilities for integration testing against live services

Run with: pytest tests/integration -m integration
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def database_url() -> str:
    """PostgreSQL connection URL for the live stack or env override."""
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://eoa@127.0.0.1:5432/eoanalyst",  # no default password; set DATABASE_URL (runtime/eoa.env)
    )


@pytest.fixture(scope="session")
def api_base_url() -> str:
    """Web API base URL."""
    return os.environ.get("API_BASE_URL", "http://127.0.0.1:8765")


@pytest.fixture(scope="session")
def ntfy_base_url() -> str:
    """Ntfy self-hosted URL."""
    return os.environ.get("NTFY_BASE_URL", "http://127.0.0.1:8090")


@pytest.fixture()
def db_conn(database_url: str):
    """Yield a psycopg connection to the live database."""
    pytest.importorskip("psycopg")
    import psycopg
    from psycopg.rows import dict_row

    # Normalize the connection string if it uses the sqlalchemy URL scheme.
    conn_str = database_url.replace("postgresql+psycopg://", "postgresql://")

    try:
        conn = psycopg.connect(conn_str, row_factory=dict_row)
    except Exception as exc:
        pytest.skip(f"Database unreachable: {exc}")

    yield conn

    try:
        conn.close()
    except Exception:
        pass


@pytest.fixture()
def http_client():
    """Yield an httpx.Client for the live API."""
    pytest.importorskip("httpx")
    import httpx

    client = httpx.Client(timeout=30.0)
    yield client
    client.close()


@pytest.fixture()
async def async_http_client():
    """Yield an httpx.AsyncClient for the live API."""
    pytest.importorskip("httpx")
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client
