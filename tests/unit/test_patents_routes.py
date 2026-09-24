"""Unit tests for `eoa.api.routes.patents` (F32, F39, docs/qa/content_review/SOL-AUDIT-2026-09-24.md):

- F39: `GET /api/patents`'s `total` must reflect the SAME filters as the returned rows, not the
  whole table's size.
- F32: `GET /api/patents/heatmap`'s CPC x assignee cell query must form the actual cross product
  per patent (`CROSS JOIN LATERAL unnest(...)` twice), not pair the two arrays positionally.

Mirrors `tests/unit/test_payloads_round3.py`'s "fake DB cursor, no live Postgres" convention for a
self-contained route module (`eoa.api.routes.patents` queries the DB directly, same as
`eoa.api.routes.payloads`/`eoa.api.routes.security_review`).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_patents_routes.py -q``
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class _FakeCursor:
    def __init__(
        self, responses: dict[str, object] | None = None, fetchall_responses: dict[str, list] | None = None
    ):
        self.responses = responses or {}
        self.fetchall_responses = fetchall_responses or {}
        self.executed: list[tuple] = []
        self._last_query = ""

    def execute(self, query, params=None):
        self.executed.append((query, params))
        self._last_query = query
        return self

    def fetchone(self):
        for key, value in self.responses.items():
            if key in self._last_query:
                return value
        return None

    def fetchall(self):
        for key, value in self.fetchall_responses.items():
            if key in self._last_query:
                return value
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, cur: _FakeCursor):
        self._cur = cur

    def cursor(self, *a, **k):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def client() -> TestClient:
    from eoa.api.app import create_app

    return TestClient(create_app())


class TestListPatentsRouteTotal:
    def test_total_uses_the_same_where_clause_as_the_row_query(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        rows = [{"id": 1, "assignees": ["Acme"], "cpc": ["F41G7/00"], "value_score": 90}]
        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM patents WHERE": {"c": 1}},
            fetchall_responses={"SELECT * FROM patents WHERE": rows},
        )
        monkeypatch.setattr("eoa.api.routes.patents.connection", lambda: _FakeConnection(cur))

        r = client.get("/api/patents", params={"assignee": "Acme"})
        assert r.status_code == 200
        assert r.json()["total"] == 1

        count_calls = [q for q, _p in cur.executed if "SELECT count(*) AS c FROM patents" in q]
        assert len(count_calls) == 1
        assert "assignee" in count_calls[0]

    def test_unfiltered_and_filtered_counts_differ_in_query_and_params(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """Regression guard for F39: the count query used to always be the bare
        `SELECT count(*) AS c FROM patents` -- the whole table -- no matter what filter params the
        row query itself used. This asserts the count statement actually carries the filter's own
        parameter, not just matching row-query filter text by coincidence."""
        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM patents WHERE": {"c": 3}},
            fetchall_responses={"SELECT * FROM patents WHERE": []},
        )
        monkeypatch.setattr("eoa.api.routes.patents.connection", lambda: _FakeConnection(cur))

        client.get("/api/patents", params={"subdomain": "eo_pods", "min_value_score": 50})

        count_query, count_params = next(
            (q, p) for q, p in cur.executed if "SELECT count(*) AS c FROM patents" in q
        )
        assert "subdomain = %(subdomain)s" in count_query
        assert "value_score >= %(min_value_score)s" in count_query
        assert count_params["subdomain"] == "eo_pods"
        assert count_params["min_value_score"] == 50


class TestListPatentsRoutePagination:
    """R06/F33 (SOL-REVIEW2-2026-09-24 review): `page`/`limit` -> LIMIT/OFFSET, same 1-based
    `page` convention as `GET /api/tech/items` (`eoa.api.services.list_tech_items`)."""

    def test_page_2_applies_the_offset(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM patents WHERE": {"c": 5}},
            fetchall_responses={"SELECT * FROM patents WHERE": []},
        )
        monkeypatch.setattr("eoa.api.routes.patents.connection", lambda: _FakeConnection(cur))

        r = client.get("/api/patents", params={"limit": 2, "page": 2})
        assert r.status_code == 200
        body = r.json()
        assert body == {"patents": [], "total": 5, "page": 2, "limit": 2, "has_more": True}

        row_query, row_params = next((q, p) for q, p in cur.executed if "LIMIT %(limit)s" in q)
        assert "OFFSET %(offset)s" in row_query
        assert row_params["offset"] == 2  # (page - 1) * limit == (2 - 1) * 2

    def test_has_more_is_false_on_the_last_page(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        rows = [{"id": 1, "assignees": ["Acme"], "cpc": [], "value_score": 10}]
        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM patents WHERE": {"c": 1}},
            fetchall_responses={"SELECT * FROM patents WHERE": rows},
        )
        monkeypatch.setattr("eoa.api.routes.patents.connection", lambda: _FakeConnection(cur))

        r = client.get("/api/patents", params={"limit": 200, "page": 1})
        assert r.json()["has_more"] is False


class TestListPatentsRouteSearchWildcardEscaping:
    """R06/P3 (SOL-REVIEW3-2026-09-24 "search"): see the identical fix/comment in
    `eoa.api.routes.payloads` -- `ILIKE` treats `%`/`_` as wildcards; `_escape_ilike_term` +
    `ILIKE ... ESCAPE '\\'` make the server's free-text `q` match a literal `%`/`_` instead of
    treating it as a wildcard."""

    def test_percent_query_is_escaped_and_matched_literally(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        from eoa.api.routes.patents import _escape_ilike_term

        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM patents WHERE": {"c": 0}},
            fetchall_responses={"SELECT * FROM patents WHERE": []},
        )
        monkeypatch.setattr("eoa.api.routes.patents.connection", lambda: _FakeConnection(cur))

        client.get("/api/patents", params={"q": "%"})

        row_query, row_params = next((q, p) for q, p in cur.executed if "LIMIT %(limit)s" in q)
        # Pre-fix code sent the raw `%` straight into the pattern with no `ESCAPE` clause -- this
        # would have matched every title/abstract instead of a literal percent sign.
        assert row_params["q"] == f"%{_escape_ilike_term('%')}%"
        assert row_params["q"] == "%\\%%"
        assert "ILIKE %(q)s ESCAPE '\\'" in row_query

    def test_underscore_query_is_escaped_and_matched_literally(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM patents WHERE": {"c": 0}},
            fetchall_responses={"SELECT * FROM patents WHERE": []},
        )
        monkeypatch.setattr("eoa.api.routes.patents.connection", lambda: _FakeConnection(cur))

        client.get("/api/patents", params={"q": "read_out"})

        _row_query, row_params = next((q, p) for q, p in cur.executed if "LIMIT %(limit)s" in q)
        # A literal underscore must be escaped to `\_` -- otherwise it matches ANY single
        # character (SQL `_` wildcard), e.g. "readXout" would also match.
        assert row_params["q"] == "%read\\_out%"


class TestPatentFacetsTotal:
    """R09 (SOL-REVIEW2-2026-09-24 review): an unfiltered existence count so the UI's "database
    empty" decision can't be fooled by every patent having a null assignee AND null subdomain
    (which would make both facet VALUE lists empty even though the table has rows)."""

    def test_facets_response_carries_an_unfiltered_total(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM patents": {"c": 9}},
            fetchall_responses={},
        )
        monkeypatch.setattr("eoa.api.routes.patents.connection", lambda: _FakeConnection(cur))

        r = client.get("/api/patents/facets")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 9
        assert body["assignees"] == []
        assert body["subdomains"] == []


class TestPatentsHeatmapCrossProduct:
    def test_cell_query_uses_cross_join_lateral_not_parallel_unnest(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """F32: two sibling `unnest()`s in one SELECT list pair elements positionally in Postgres
        (row 0 with row 0, row 1 with row 1, ...) instead of forming every combination -- a patent
        with 2 CPC codes and 2 assignees must contribute 4 cells, which only `CROSS JOIN LATERAL`
        (applied twice, one per array) can produce."""
        cpc_rows = [{"cpc": "F41G7/00", "n": 5}, {"cpc": "G01S17/00", "n": 3}]
        assignee_rows = [{"assignee": "Acme", "n": 4}, {"assignee": "Widgets Inc", "n": 4}]
        # Four cells -- one per (cpc, assignee) combination -- is what a correct CROSS JOIN LATERAL
        # query would return for 2 patents each carrying both CPC codes and both assignees.
        cell_rows = [
            {"cpc": "F41G7/00", "assignee": "Acme", "n": 2},
            {"cpc": "F41G7/00", "assignee": "Widgets Inc", "n": 2},
            {"cpc": "G01S17/00", "assignee": "Acme", "n": 2},
            {"cpc": "G01S17/00", "assignee": "Widgets Inc", "n": 2},
        ]
        cur = _FakeCursor(
            fetchall_responses={
                "SELECT c AS cpc, count(*) AS n": cpc_rows,
                "SELECT a AS assignee, count(*) AS n": assignee_rows,
                "SELECT c AS cpc, a AS assignee, count(*) AS n": cell_rows,
            }
        )
        monkeypatch.setattr("eoa.api.routes.patents.connection", lambda: _FakeConnection(cur))

        r = client.get("/api/patents/heatmap")
        assert r.status_code == 200
        body = r.json()
        assert len(body["cells"]) == 4

        cell_query = next(
            q for q, _p in cur.executed if "SELECT c AS cpc, a AS assignee, count(*) AS n" in q
        )
        assert "CROSS JOIN LATERAL unnest(p.cpc) AS c" in cell_query
        assert "CROSS JOIN LATERAL unnest(p.assignees) AS a" in cell_query
        # The old positional-pairing form must be gone entirely.
        assert "unnest(cpc) AS c, unnest(assignees) AS a" not in cell_query

    def test_no_cpc_or_assignees_skips_the_cell_query_entirely(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        cur = _FakeCursor(fetchall_responses={})
        monkeypatch.setattr("eoa.api.routes.patents.connection", lambda: _FakeConnection(cur))
        r = client.get("/api/patents/heatmap")
        assert r.status_code == 200
        assert r.json() == {"cpc_codes": [], "assignees": [], "cells": []}
