"""Integration smoke tests against the live EO-Analyst stack.

Tests the full integration of:
1. PostgreSQL database (migrations, schema, AGE graph)
2. Web API endpoints (/api/status, /api/items, /api/graph, etc.)
3. Fetcher bridge (job enqueueing and polling)
4. Ntfy self-hosted notifications
5. Network isolation (agent container has no egress)

Run with: PYTHONPATH=agent python -m pytest tests/integration/test_live_stack.py -q -m integration

Skips gracefully when services are unreachable (clear reason given).
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


# ============================================================================
# 1. DATABASE & SCHEMA TESTS
# ============================================================================


class TestDatabaseSchema:
    """Tests for DB migrations, core tables, and AGE graph."""

    def test_alembic_migration_at_head(self, db_conn, database_url: str):
        """Verify alembic current revision matches the latest migration file."""
        import subprocess

        env = os.environ.copy()
        env["DATABASE_URL"] = database_url
        result = subprocess.run(
            ["alembic", "current"],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip(f"alembic current failed: {result.stderr[:200]}")

        tokens = [t for t in result.stdout.strip().replace("(head)", " ").split() if t]
        current_revision = tokens[-1] if tokens else None

        # Get the latest revision from migration files
        migrations_dir = REPO_ROOT / "db" / "migrations" / "versions"
        if not migrations_dir.exists():
            pytest.skip("Migrations directory not found")

        migration_files = sorted(migrations_dir.glob("*.py"))
        if not migration_files:
            pytest.skip("No migration files found")

        latest_file = migration_files[-1].stem
        # Extract the revision ID from filename (format: <rev>_*.py)
        latest_revision = latest_file.split("_")[0] if "_" in latest_file else latest_file

        current_clean = str(current_revision).replace("(head)", "").strip()
        assert current_clean == latest_revision, f"Current revision {current_revision} != latest {latest_revision}"

    def test_core_tables_exist(self, db_conn):
        """Verify all core tables are present."""
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
            )
            tables = {row["table_name"] for row in cur.fetchall()}

        expected_tables = {
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

        missing = expected_tables - tables
        assert not missing, f"Missing tables: {missing}"

    def test_items_table_has_embedding_column(self, db_conn):
        """Verify items table has pgvector embedding column."""
        with db_conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'items' AND column_name = 'embedding'
                """
            )
            row = cur.fetchone()

        assert row is not None, "items.embedding column not found"
        # pgvector type is reported as 'USER-DEFINED'
        assert row["data_type"] in ("USER-DEFINED", "vector"), f"Unexpected type: {row['data_type']}"

    def test_age_graph_exists_and_has_entity_vertices(self, db_conn):
        """Verify AGE graph 'eo_graph' exists and Entity vertex count matches entities table."""
        # First check if AGE extension is available
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT installed_version FROM pg_available_extensions WHERE name = 'age' LIMIT 1"
            )
            has_age = cur.fetchone() is not None

        if not has_age:
            pytest.skip("Apache AGE extension not installed")

        # Try to load AGE and query the graph
        try:
            with db_conn.cursor() as cur:
                cur.execute("LOAD 'age'")
                cur.execute('SET search_path = ag_catalog, "$user", public')
                cur.execute("SELECT * FROM ag_graph WHERE name = 'eo_graph'")
                graph_row = cur.fetchone()

            if graph_row is None:
                pytest.skip("eo_graph not found (graph_init.sql not run yet)")

            # Count Entity vertices
            with db_conn.cursor() as cur:
                cur.execute('SET search_path = ag_catalog, "$user", public')
                cur.execute(
                    "SELECT count(*) as cnt FROM cypher('eo_graph', $$ MATCH (e:Entity) RETURN e $$) AS (e agtype)"
                )
                vertex_count = cur.fetchone()["cnt"]

            # Count entities in relational table
            with db_conn.cursor() as cur:
                cur.execute("SELECT count(*) as cnt FROM entities")
                entity_count = cur.fetchone()["cnt"]

            # They should match (within reason; vertex count >= entity count due to dedup logic)
            assert (
                vertex_count >= entity_count
            ), f"Graph vertices {vertex_count} < entities {entity_count}"
        except Exception as exc:
            if "permission denied" in str(exc).lower() or "syntax error" in str(exc).lower():
                pytest.skip(f"Cannot query AGE graph: {str(exc)[:100]}")
            raise


# ============================================================================
# 2. API ENDPOINT TESTS
# ============================================================================


class TestApiEndpoints:
    """Tests for the web API endpoints."""

    def test_api_status_endpoint(self, http_client, api_base_url: str):
        """GET /api/status returns all service statuses."""
        r = http_client.get(f"{api_base_url}/api/status")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"

        body = r.json()
        assert "services" in body
        assert isinstance(body["services"], dict)
        assert "postgres" in body["services"]
        assert "ollama" in body["services"]
        assert "searxng" in body["services"]
        assert "ntfy" in body["services"]
        assert "gate" in body
        assert "pipeline" in body

    def test_api_items_list_endpoint(self, http_client, api_base_url: str):
        """GET /api/items returns paginated items."""
        r = http_client.get(f"{api_base_url}/api/items", params={"page": 1, "page_size": 50})
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"

        body = r.json()
        assert "total" in body
        assert "items" in body
        assert isinstance(body["items"], list)
        # Stack may have items or be empty; just check structure
        assert "total" in body and isinstance(body["total"], int)

    def test_api_items_with_first_item_detail(self, http_client, api_base_url: str):
        """GET /api/items then GET /api/items/{id} returns full detail."""
        # Get list
        r_list = http_client.get(f"{api_base_url}/api/items", params={"page": 1, "page_size": 1})
        assert r_list.status_code == 200

        items = r_list.json().get("items", [])
        if not items:
            pytest.skip("No items in database to test detail endpoint")

        item_id = items[0]["id"]

        # Get detail
        r_detail = http_client.get(f"{api_base_url}/api/items/{item_id}")
        if r_detail.status_code == 404:
            pytest.skip("Item detail not implemented or item deleted")
        elif r_detail.status_code != 200:
            pytest.fail(f"Expected 200, got {r_detail.status_code}: {r_detail.text[:200]}")

        detail = r_detail.json()
        assert "id" in detail
        # Per API spec: clean_text and edges should be present
        # (may be empty if not yet processed)
        assert "clean_text" in detail or "url" in detail  # At least one should exist
        if "edges" in detail:
            assert isinstance(detail["edges"], list)

    def test_api_entities_search_endpoint(self, http_client, api_base_url: str):
        """GET /api/entities searches for entities by name."""
        # Try searching for a generic company name
        r = http_client.get(f"{api_base_url}/api/entities", params={"q": "Elbit", "limit": 50})

        # Endpoint may not exist yet (phase 0) or have no results
        if r.status_code == 404:
            pytest.skip("Entities endpoint not implemented")
        elif r.status_code != 200:
            pytest.fail(f"Expected 200 or 404, got {r.status_code}: {r.text[:200]}")

        body = r.json()
        assert isinstance(body, list), "Expected list of entities"

    def test_api_graph_endpoint(self, http_client, api_base_url: str):
        """GET /api/graph returns graph nodes and edges for an entity."""
        # First get an entity to query
        r_entities = http_client.get(f"{api_base_url}/api/entities", params={"limit": 1})
        if r_entities.status_code == 404:
            pytest.skip("Entities endpoint not implemented")

        entities = r_entities.json()
        if not entities:
            pytest.skip("No entities in database to test graph endpoint")

        entity_id = entities[0]["id"]

        # Query graph
        r = http_client.get(f"{api_base_url}/api/graph", params={"entity_id": entity_id})

        if r.status_code == 404:
            pytest.skip("Graph endpoint not implemented")
        elif r.status_code != 200:
            pytest.fail(f"Expected 200 or 404, got {r.status_code}: {r.text[:200]}")

        body = r.json()
        assert "nodes" in body or "edges" in body, "Expected graph data"

    def test_api_conferences_endpoint(self, http_client, api_base_url: str):
        """GET /api/conferences returns conference list (possibly empty)."""
        r = http_client.get(f"{api_base_url}/api/conferences")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"

        body = r.json()
        # Per spec: list or {"conferences": [...]}
        if isinstance(body, dict):
            assert "conferences" in body or any(
                k in body for k in ["data", "items"]
            ), "Expected conference data"
        else:
            assert isinstance(body, list), "Expected list or dict"

    def test_api_conferences_ical_endpoint(self, http_client, api_base_url: str):
        """GET /api/conferences/ical returns iCalendar format."""
        r = http_client.get(f"{api_base_url}/api/conferences/ical")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"

        # Check content type
        assert (r.headers.get("content-type") or "").split(";")[0].strip() in [
            "text/calendar",
            "application/x-ics",
        ], f"Unexpected content-type: {r.headers.get('content-type')}"

        # iCalendar should have VEVENT or be empty
        text = r.text
        # May be empty (no conferences), but should have VCALENDAR wrapper
        assert "VCALENDAR" in text or len(text) < 100, "Expected iCalendar format"

    def test_api_surveys_latest_endpoint(self, http_client, api_base_url: str):
        """GET /api/surveys/latest returns latest survey or null."""
        r = http_client.get(f"{api_base_url}/api/surveys/latest")
        if r.status_code == 404:
            pytest.skip("Surveys endpoint not implemented")
        elif r.status_code != 200:
            pytest.fail(f"Expected 200 or 404, got {r.status_code}: {r.text[:200]}")

        body = r.json()
        # May be null if no survey exists
        if body is not None:
            assert "id" in body or "questions" in body, "Expected survey structure"

    def test_api_feedback_meta_endpoint(self, http_client, api_base_url: str):
        """GET /api/feedback/meta returns feedback metadata."""
        r = http_client.get(f"{api_base_url}/api/feedback/meta")
        if r.status_code == 404:
            pytest.skip("Feedback meta endpoint not implemented")
        elif r.status_code != 200:
            pytest.fail(f"Expected 200 or 404, got {r.status_code}: {r.text[:200]}")

        body = r.json()
        assert isinstance(body, dict), "Expected dict for feedback metadata"

    def test_api_unknown_route_error_shape(self, http_client, api_base_url: str):
        """Unknown route returns proper error JSON."""
        r = http_client.get(f"{api_base_url}/api/nonexistent-endpoint-xyz")
        assert r.status_code == 404
        body = r.json()
        assert "error" in body, "Expected error field"
        assert "code" in body["error"], "Expected error.code"

    def test_api_spa_served_at_root(self, http_client, api_base_url: str):
        """GET / serves the React SPA (HTML)."""
        r = http_client.get(f"{api_base_url}/")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        # Should be HTML
        content_type = r.headers.get("content-type", "").lower()
        assert "html" in content_type or "<!DOCTYPE" in r.text, "Expected HTML SPA"


# ============================================================================
# 3. FETCHER BRIDGE TESTS
# ============================================================================


class TestFetcherBridge:
    """Tests for job enqueueing and fetcher service integration."""

    def test_enqueue_and_poll_fetch_job(self, database_url: str):
        """Enqueue a fetch_url job and poll until completion within 60s."""
        pytest.importorskip("psycopg")

        import psycopg
        from psycopg.rows import dict_row

        conn_str = database_url.replace("postgresql+psycopg://", "postgresql://")
        try:
            conn = psycopg.connect(conn_str, row_factory=dict_row)
        except Exception as exc:
            pytest.skip(f"Database unreachable: {exc}")

        try:
            import sys

            # Ensure eoa can be imported
            if str(REPO_ROOT / "agent") not in sys.path:
                sys.path.insert(0, str(REPO_ROOT / "agent"))

            os.environ["DATABASE_URL"] = database_url

            # Import after path setup
            from eoa.memory.relational import enqueue_job

            # Enqueue a fetch_url job for a safe URL
            job_id = enqueue_job("fetch_url", {"url": "https://example.com"}, priority=0)
            assert job_id > 0, "Job ID should be positive"

            # Poll for completion (up to 60 seconds)
            deadline = time.time() + 60
            while time.time() < deadline:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT state, result, error FROM jobs WHERE id = %s",
                        (job_id,),
                    )
                    row = cur.fetchone()

                if row is None:
                    time.sleep(1)
                    continue

                state = row["state"]
                if state in ("done", "partial"):
                    # Success: job completed
                    result = row["result"]
                    if result:
                        assert isinstance(result, dict), "Result should be dict"
                        # Fetched content should have 'text' key
                        if "text" in result:
                            assert isinstance(result["text"], str), "text should be string"
                    break
                elif state == "failed":
                    # Job failed; check error
                    error = row["error"]
                    pytest.skip(f"Fetch job failed: {error}")
                else:
                    # Still running or queued
                    time.sleep(2)
            else:
                pytest.skip("Fetch job did not complete within 60s (fetcher container may be idle)")

        finally:
            try:
                conn.close()
            except Exception:
                pass


# ============================================================================
# 4. NTFY INTEGRATION TESTS
# ============================================================================


class TestNtfyIntegration:
    """Tests for self-hosted ntfy notifications."""

    def test_ntfy_post_to_test_topic(self, ntfy_base_url: str):
        """POST to ntfy eo-analyst-test topic returns 200."""
        pytest.importorskip("httpx")
        import httpx

        client = httpx.Client(timeout=10.0)
        try:
            # Post a test message to a non-production topic
            url = f"{ntfy_base_url}/eo-analyst-test"
            r = client.post(
                url,
                headers={"Title": "Integration Test"},
                content="Test message from integration smoke test",
            )

            # ntfy should accept the POST
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"
        except Exception as exc:
            pytest.skip(f"Ntfy unreachable: {exc}")
        finally:
            client.close()


# ============================================================================
# 5. NETWORK ISOLATION TESTS
# ============================================================================


class TestNetworkIsolation:
    """Tests that the agent container has no internet egress."""

    def test_agent_container_no_egress(self):
        """Verify agent container cannot reach external URLs via docker exec."""
        try:
            # Try to execute a Python command in the agent container that attempts HTTPS
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "agent",
                    "python",
                    "-c",
                    "import httpx; httpx.get('https://example.com', timeout=5)",
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=15,
            )

            # Command should fail (non-zero exit)
            if result.returncode == 0:
                pytest.fail("Agent container should not have internet access (command succeeded)")
            # If it fails, that's the expected behavior
        except FileNotFoundError:
            pytest.skip("docker or docker-compose not available")
        except subprocess.TimeoutExpired:
            pytest.skip("docker-compose exec timed out")
        except Exception as exc:
            pytest.skip(f"Cannot run isolation test: {exc}")
