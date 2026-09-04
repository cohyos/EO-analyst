"""PostgreSQL connection pool (psycopg3) shared by all modules."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from eoa.config import settings


@lru_cache(maxsize=1)
def get_pool() -> ConnectionPool:
    """Create (once) and return the process-wide connection pool."""
    pool: ConnectionPool = ConnectionPool(
        conninfo=settings().database_url,
        min_size=1,
        max_size=8,
        kwargs={"row_factory": dict_row, "autocommit": False},
        open=True,
    )
    return pool


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Yield a pooled connection; commits on success, rolls back on error."""
    pool = get_pool()
    with pool.connection() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def ping() -> bool:
    """Return True if the database answers."""
    try:
        with connection() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def close_pool() -> None:
    """Close the pool (used on shutdown and in tests)."""
    if get_pool.cache_info().currsize:
        get_pool().close()
        get_pool.cache_clear()
