"""F32 follow-up (SOL-REVIEW-2026-09-24 review): `test_patents_routes.py`'s heatmap coverage
inspects SQL text against a *fake* cursor with hand-built rows -- it never actually asks PostgreSQL
to evaluate `CROSS JOIN LATERAL unnest(...) x2` against a GROUP BY, which is exactly the construct
the old, buggy query (two parallel `unnest()`s, positionally zipped) and the fixed one disagree on.

This test runs the real cell-count SQL from `eoa.api.routes.patents.patents_heatmap` against a live
PostgreSQL connection, inside a transaction that is rolled back at teardown (no `commit()` call
anywhere in this file) so it leaves no rows behind. One patent with 2 CPC codes x 2 assignees must
produce exactly 4 cells, each count 1 -- the old positional-zip query produces only 2.

Skips (does not fail) when DATABASE_URL / a local Postgres is unreachable, same convention as
`tests/integration/conftest.py`'s `db_conn` fixture.

Run with: ``DATABASE_URL=... PYTHONPATH=agent python -m pytest tests/unit/test_patents_heatmap_postgres.py -q``
"""

from __future__ import annotations

import os

import pytest

# The exact cell-count query body from `eoa.api.routes.patents.patents_heatmap` (F32 fix) --
# duplicated here (rather than importing and monkeypatching `connection`) so this test exercises
# the SQL text itself directly against Postgres, independent of the top-N CPC/assignee ranking
# step (which depends on whatever real data already lives in the shared dev database).
_CELL_QUERY = """
    SELECT c AS cpc, a AS assignee, count(*) AS n
    FROM patents p
    CROSS JOIN LATERAL unnest(p.cpc) AS c
    CROSS JOIN LATERAL unnest(p.assignees) AS a
    WHERE p.cpc && %(cpc_codes)s AND p.assignees && %(assignees)s
      AND c = ANY(%(cpc_codes)s) AND a = ANY(%(assignees)s)
    GROUP BY c, a
"""

# The OLD, buggy query (SOL-AUDIT-2026-09-24 F32): two parallel unnest()s in one SELECT are paired
# POSITIONALLY by Postgres (like zip()), not as a cross product.
_OLD_BUGGY_CELL_QUERY = """
    SELECT c AS cpc, a AS assignee, count(*) AS n
    FROM (SELECT unnest(cpc) AS c, unnest(assignees) AS a FROM patents WHERE pub_number = %(pub)s) s
    WHERE c = ANY(%(cpc_codes)s) AND a = ANY(%(assignees)s)
    GROUP BY c, a
"""

_TEST_CPC = ["F32-TEST-CPC-A", "F32-TEST-CPC-B"]
_TEST_ASSIGNEES = ["F32-TEST-ASSIGNEE-A", "F32-TEST-ASSIGNEE-B"]


@pytest.fixture()
def pg_conn():
    pytest.importorskip("psycopg")
    import psycopg
    from psycopg.rows import dict_row

    url = os.environ.get(
        "DATABASE_URL", "postgresql://eoa@127.0.0.1:5432/eoanalyst"
    ).replace("postgresql+psycopg://", "postgresql://")
    try:
        conn = psycopg.connect(url, row_factory=dict_row)
    except Exception as exc:
        pytest.skip(f"Postgres unreachable: {exc}")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM patents LIMIT 1")
    except Exception as exc:
        conn.rollback()
        conn.close()
        pytest.skip(f"patents table unavailable: {exc}")
    yield conn
    conn.rollback()  # F32 test hygiene: never commits, so the inserted row never persists.
    conn.close()


def _insert_test_patent(pg_conn) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO patents (pub_number, title, cpc, assignees) "
            "VALUES (%(pub)s, %(title)s, %(cpc)s, %(assignees)s)",
            {
                "pub": "F32-TEST-PUB-0001",
                "title": "F32 heatmap cross-product regression fixture",
                "cpc": _TEST_CPC,
                "assignees": _TEST_ASSIGNEES,
            },
        )


class TestHeatmapCellQueryAgainstPostgres:
    def test_two_cpc_two_assignees_produce_four_cells(self, pg_conn) -> None:
        _insert_test_patent(pg_conn)
        with pg_conn.cursor() as cur:
            cur.execute(_CELL_QUERY, {"cpc_codes": _TEST_CPC, "assignees": _TEST_ASSIGNEES})
            rows = cur.fetchall()

        pairs = {(r["cpc"], r["assignee"]): r["n"] for r in rows}
        assert len(pairs) == 4, f"expected 4 CPC x assignee cells, got {pairs}"
        assert set(pairs) == {
            (c, a) for c in _TEST_CPC for a in _TEST_ASSIGNEES
        }
        assert all(n == 1 for n in pairs.values())

    def test_old_positional_zip_query_undercounts_the_same_fixture(self, pg_conn) -> None:
        """Discriminator: the pre-fix query (still present here for comparison only) produces just
        2 cells -- 0-with-0, 1-with-1 -- for the identical 2x2 fixture, proving the fixed query
        above is not accidentally equivalent to it."""
        _insert_test_patent(pg_conn)
        with pg_conn.cursor() as cur:
            cur.execute(
                _OLD_BUGGY_CELL_QUERY,
                {"pub": "F32-TEST-PUB-0001", "cpc_codes": _TEST_CPC, "assignees": _TEST_ASSIGNEES},
            )
            rows = cur.fetchall()
        assert len(rows) == 2
