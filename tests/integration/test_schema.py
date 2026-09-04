"""Integration tests for the DB schema, memory layer, and jobs queue.

Needs Docker. Prefers an image built from `docker/postgres-age` (pgvector +
Apache AGE) if that Dockerfile exists yet -- another agent owns it and may
still be building it. Falls back to the stock `pgvector/pgvector:pg17` image,
in which case AGE/graph-dependent tests are skipped with a clear reason.

Run with: pytest tests/integration -m integration
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("testcontainers")

from testcontainers.postgres import PostgresContainer

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
POSTGRES_AGE_DOCKERFILE = REPO_ROOT / "docker" / "postgres-age" / "Dockerfile"


def _has_buildable_age_image() -> bool:
    return POSTGRES_AGE_DOCKERFILE.exists()


@pytest.fixture(scope="module")
def has_age() -> bool:
    """Whether this run's Postgres image actually has Apache AGE available."""
    return _has_buildable_age_image()


@pytest.fixture(scope="module")
def pg_container(has_age: bool):
    """Start a Postgres container: docker/postgres-age if buildable, else pgvector/pgvector:pg17."""
    if has_age:
        image_tag = "eoa-postgres-age-test:latest"
        subprocess.run(
            [
                "docker",
                "build",
                "-t",
                image_tag,
                "-f",
                str(POSTGRES_AGE_DOCKERFILE),
                str(REPO_ROOT / "docker" / "postgres-age"),
            ],
            check=True,
        )
        container = PostgresContainer(image_tag, driver="psycopg")
    else:
        container = PostgresContainer("pgvector/pgvector:pg17", driver="psycopg")

    with container as pg:
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
    subprocess.run(["alembic", "upgrade", "head"], cwd=str(REPO_ROOT), env=env, check=True)
    yield


@pytest.fixture()
def conn(database_url: str):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(
        database_url.replace("postgresql+psycopg://", "postgresql://"), row_factory=dict_row
    ) as c:
        yield c


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
    }
    assert expected <= tables


def test_insert_item_with_embedding_and_nearest(conn, database_url: str) -> None:
    import sys

    sys.path.insert(0, str(REPO_ROOT / "agent"))
    os.environ["DATABASE_URL"] = database_url

    from eoa.memory import vector as vector_mod
    from eoa.memory.relational import insert_item, upsert_source

    source_id = upsert_source(name="test-source", url="https://example.com/feed", kind="rss")
    item_id = insert_item(source_id=source_id, url="https://example.com/a", title="A")
    vec = [0.1] * 1024
    vector_mod.upsert_embedding(item_id, vec)

    results = vector_mod.nearest(vec, limit=5)
    assert any(r[0] == item_id for r in results)


def test_entity_insert_creates_vertex(conn, has_age: bool, database_url: str) -> None:
    if not has_age:
        pytest.skip("Apache AGE not available in this Postgres image (docker/postgres-age not built yet)")

    with conn.cursor() as cur:
        cur.execute(open(REPO_ROOT / "db" / "graph_init.sql", encoding="utf-8").read())
    conn.commit()

    import sys

    sys.path.insert(0, str(REPO_ROOT / "agent"))
    os.environ["DATABASE_URL"] = database_url

    from eoa.memory.relational import upsert_entity

    entity_id = upsert_entity(name="Test Entity Co", kind="company", country="US")

    with conn.cursor() as cur:
        cur.execute("LOAD 'age'")
        cur.execute('SET search_path = ag_catalog, "$user", public')
        cur.execute(
            f"SELECT * FROM cypher('eo_graph', $$ MATCH (e:Entity {{entity_id: {entity_id}}}) "
            "RETURN e $$) AS (e agtype)"
        )
        rows = cur.fetchall()
    assert len(rows) == 1


def test_jobs_claim_and_finish_flow(conn, database_url: str) -> None:
    import sys

    sys.path.insert(0, str(REPO_ROOT / "agent"))
    os.environ["DATABASE_URL"] = database_url

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
    assert row["state"] == "done"
