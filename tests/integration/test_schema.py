"""Integration tests for the DB schema, memory layer, and jobs queue.

Needs Docker. ADR-004 (docs/adr/004-windows-native.md) dropped both pgvector and Apache AGE:
`items.embedding` is a plain `REAL[]` column (similarity computed in numpy by
`eoa.memory.vector`), and the graph is the plain-SQL `graph_edges` table (migration 0006;
`entities` rows are the vertices directly). So this suite runs against the stock
`postgres:17` image -- no custom AGE/pgvector build -- and exercises `graph_edges` instead of
AGE vertices.

Run with: pytest tests/integration -m integration
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("testcontainers")

from testcontainers.postgres import PostgresContainer

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pg_container():
    """Start a plain `postgres:17` container (no pgvector, no Apache AGE; see ADR-004)."""
    with PostgresContainer("postgres:17", driver="psycopg") as pg:
        yield pg


@pytest.fixture(scope="module")
def database_url(pg_container) -> str:
    url = pg_container.get_connection_url()
    # testcontainers renders postgresql+psycopg://..., which is exactly what
    # db/migrations/env.py expects on DATABASE_URL (and what it would coerce
    # a bare postgresql:// into anyway).
    return url


@pytest.fixture(scope="module", autouse=True)
def apply_migrations(database_url: str):
    """Run `alembic upgrade head` against the ephemeral test container."""
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    # Invoke alembic as `python -m alembic` with *this* interpreter (the venv running pytest)
    # rather than a bare `alembic` off PATH -- on this machine PATH resolves `alembic` to a
    # different, non-venv Python that lacks psycopg and fails with an opaque exit code.
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=str(REPO_ROOT), env=env, check=True
    )
    yield


@pytest.fixture()
def conn(database_url: str):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(
        database_url.replace("postgresql+psycopg://", "postgresql://"), row_factory=dict_row
    ) as c:
        yield c


@pytest.fixture(autouse=True)
def _eoa_db_pool_pointed_at_container(database_url: str):
    """Point `eoa.db`'s process-wide connection pool at *this* ephemeral container.

    `eoa.db.get_pool()` is `@lru_cache`d: the first time anything calls `eoa.db.connection()`
    in the pytest process, it builds one `ConnectionPool` from `settings().database_url` and
    keeps it forever -- later mutations of `os.environ["DATABASE_URL"]` by a test function do
    nothing on their own. If some other test module (e.g. test_live_stack.py, which talks to
    the real live stack) happens to run first in the same session and touches `eoa.db`
    first, every `eoa.memory.*` call in this file would silently keep hitting that *other*
    database instead of this module's testcontainers instance -- while the `conn` fixture
    above connects directly to the right one, making assertions that re-read through `conn`
    see no row at all (this was the "None row" in `test_jobs_claim_and_finish_flow`: the job
    was really enqueued/claimed/finished against a stale cached pool pointing elsewhere).
    Force a fresh pool for this module's tests, and drop it again afterwards so later modules
    are not stuck pointing at this container once it is torn down.

    Separately: `eoa.db.get_pool()` hands its conninfo straight to raw psycopg
    (`psycopg_pool.ConnectionPool`), which -- unlike SQLAlchemy/alembic -- does not
    understand the `postgresql+psycopg://` driver-qualified scheme testcontainers returns
    (`psycopg.ProgrammingError: missing "=" after "postgresql+psycopg://..." in connection
    info string`). `db/migrations/env.py` wants that `+psycopg` form for SQLAlchemy, but
    `eoa.config.settings().database_url` / `eoa.db` want a plain `postgresql://` URL (that is
    what the real `runtime/eoa.env` DATABASE_URL looks like too) -- so strip the driver
    qualifier before exporting it for the `eoa.*` imports below, same as the `conn` fixture
    already does for its direct psycopg.connect().
    """
    if str(REPO_ROOT / "agent") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "agent"))
    os.environ["DATABASE_URL"] = database_url.replace("postgresql+psycopg://", "postgresql://")

    from eoa.db import close_pool

    close_pool()
    yield
    close_pool()


def test_migration_applies_and_creates_core_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        tables = {row["table_name"] for row in cur.fetchall()}
    expected = {
        "sources",
        "items",
        "entities",
        "events",
        "contracts",
        "conferences",
        "reports",
        "jobs",
        "run_log",
        "resource_log",
        "model_registry",
        "security_log",
        "triage_feedback",
        "source_reliability",
        "search_playbook",
        "lessons",
        "investigation_log",
        "clarifications",
        "feedback_surveys",
        "graph_edges",
    }
    assert expected <= tables


def test_items_embedding_is_plain_real_array(conn) -> None:
    """ADR-004: no pgvector extension -- `items.embedding` is a bare `real[]` column."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, udt_name
            FROM information_schema.columns
            WHERE table_name = 'items' AND column_name = 'embedding'
            """
        )
        row = cur.fetchone()
    assert row is not None, "items.embedding column not found"
    assert row["data_type"] == "ARRAY"
    assert row["udt_name"] == "_float4"


def test_insert_item_with_embedding_and_nearest(conn, database_url: str) -> None:
    from eoa.memory import vector as vector_mod
    from eoa.memory.relational import insert_item, upsert_source

    source_id = upsert_source(name="test-source", url="https://example.com/feed", kind="rss")
    item_id = insert_item(source_id=source_id, url="https://example.com/a", title="A")
    vec = [0.1] * 1024
    vector_mod.upsert_embedding(item_id, vec)

    results = vector_mod.nearest(vec, limit=5)
    assert any(r[0] == item_id for r in results)


def test_entity_and_edge_creates_graph_edges_row(conn, database_url: str) -> None:
    """Replaces the old AGE-vertex test: the graph is now the plain `graph_edges` table."""
    from eoa.memory.graph import add_edge
    from eoa.memory.relational import upsert_entity

    src_id = upsert_entity(name="Test Entity Co A", kind="company", country="US")
    dst_id = upsert_entity(name="Test Entity Co B", kind="company", country="IL")

    edge = add_edge(src_id, dst_id, "PARTNER_OF", item_id=None)
    assert edge["src_entity_id"] == src_id
    assert edge["dst_entity_id"] == dst_id
    assert edge["label"] == "PARTNER_OF"

    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM graph_edges WHERE src_entity_id = %s AND dst_entity_id = %s AND label = %s",
            (src_id, dst_id, "PARTNER_OF"),
        )
        row = cur.fetchone()
    assert row is not None, "add_edge() did not persist a graph_edges row"
    assert row["item_id"] is None


def test_jobs_claim_and_finish_flow(conn, database_url: str) -> None:
    from eoa.memory.relational import claim_next_job, enqueue_job, finish_job

    job_id = enqueue_job("fetch", payload={"source": "test"})
    claimed = claim_next_job(kinds=["fetch"])
    assert claimed is not None
    assert claimed["id"] == job_id
    assert claimed["state"] == "running"

    finish_job(job_id, "done", result={"ok": True})

    with conn.cursor() as cur:
        cur.execute("SELECT state, result FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
    assert row is not None, "job row not found -- eoa.db pool may be pointed at the wrong database"
    assert row["state"] == "done"
