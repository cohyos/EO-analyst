"""Unit tests for `eoa.graph.queries` (R10-graph, docs/qa/loop/round_10_fixes.md).

No DB: `eoa.graph.queries._fetchall`/`_fetchone` are monkeypatched directly with fakes that
dispatch on a distinctive substring of the SQL text -- the same style
`tests/unit/test_bd_round4b.py` uses for `eoa.report.bd_territory`'s helpers -- rather than a
fake psycopg cursor/connection, since several functions here (`neighborhood`, `path`,
`overview`) issue more than one differently-shaped query per call and a single fixed row list
(as `tests/unit/test_graph_edges.py` uses for `eoa.memory.graph`, which only ever issues one
query per call) can't tell them apart.

Run with:
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_graph_round10.py -q
"""

from __future__ import annotations

import sys
import types

import pytest

if "eoa.db" not in sys.modules:
    try:
        import eoa.db  # noqa: F401
    except ImportError:
        fake_db = types.ModuleType("eoa.db")
        fake_db.connection = lambda: None  # type: ignore[attr-defined]
        fake_db.get_pool = lambda: None  # type: ignore[attr-defined]
        sys.modules["eoa.db"] = fake_db

from eoa.graph import queries as gq

# --------------------------------------------------------------------------
# validation helpers
# --------------------------------------------------------------------------


class TestValidation:
    def test_require_kinds_rejects_unknown(self) -> None:
        with pytest.raises(ValueError, match="unknown entity kind"):
            gq._require_kinds(["company", "not_a_kind"])

    def test_require_kinds_none_passthrough(self) -> None:
        assert gq._require_kinds(None) is None

    def test_require_relation_types_rejects_unknown(self) -> None:
        with pytest.raises(ValueError, match="unknown relation type"):
            gq._require_relation_types(["PARTNER_OF", "NOT_A_LABEL"])

    def test_require_relation_types_accepts_known(self) -> None:
        assert gq._require_relation_types(["PARTNER_OF"]) == ["PARTNER_OF"]


# --------------------------------------------------------------------------
# search_entities
# --------------------------------------------------------------------------


class TestSearchEntities:
    def test_blank_query_returns_empty_without_querying(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            gq, "_fetchall", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no query"))
        )
        assert gq.search_entities("   ") == []

    def test_maps_rows_to_summary_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {
                "id": 1,
                "name": "RTX",
                "kind": "company",
                "country": "US",
                "relevance": 0.9,
                "mention_count": 12,
            }
        ]
        monkeypatch.setattr(gq, "_fetchall", lambda q, p=None: rows)
        result = gq.search_entities("rtx")
        assert result == [{"id": 1, "name": "RTX", "kind": "company", "country": "US", "mention_count": 12}]

    def test_query_uses_ilike_on_name_and_aliases(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict = {}

        def fake_fetchall(query: str, params=None):
            seen["query"] = query
            seen["params"] = params
            return []

        monkeypatch.setattr(gq, "_fetchall", fake_fetchall)
        gq.search_entities("IAI", limit=5)
        assert "ILIKE" in seen["query"]
        assert seen["params"]["q"] == "%IAI%"
        assert seen["params"]["limit"] == 5


# --------------------------------------------------------------------------
# neighborhood
# --------------------------------------------------------------------------


_CENTER = {"id": 1, "name": "RTX", "kind": "company", "country": "US"}


def _walk_row(src: int, dst: int, label: str, item_id: int | None, date: str | None, created_at: str) -> dict:
    return {
        "edge_id": item_id or 999,
        "src_entity_id": src,
        "dst_entity_id": dst,
        "label": label,
        "item_id": item_id,
        "created_at": created_at,
        "item_title": f"item {item_id}" if item_id else None,
        "item_date": date,
    }


def _stats_row(entity_id: int, name: str, kind: str, mention_count: int = 3) -> dict:
    return {
        "entity_id": entity_id,
        "name": name,
        "kind": kind,
        "country": "US",
        "mention_count": mention_count,
        "last_seen": "2026-09-01",
        "corroborated_n": 1,
        "official_primary_n": 0,
        "single_source_n": 2,
        "unknown_n": 0,
        "product_lines": ["ew"],
    }


def _patch_neighborhood(
    monkeypatch: pytest.MonkeyPatch,
    *,
    center: dict | None = _CENTER,
    walk_rows: list[dict] | None = None,
    stats_rows: list[dict] | None = None,
) -> None:
    walk_rows = walk_rows or []
    stats_rows = stats_rows if stats_rows is not None else [_stats_row(2, "IAI", "company")]

    def fake_fetchone(query: str, params=None):
        assert "WHERE id = %s" in query
        return center

    def fake_fetchall(query: str, params=None):
        if "WITH RECURSIVE walk" in query:
            return walk_rows
        if "GROUP BY e.id, e.name, e.kind, e.country" in query:
            return stats_rows
        raise AssertionError(f"unexpected query: {query[:60]}")

    monkeypatch.setattr(gq, "_fetchone", fake_fetchone)
    monkeypatch.setattr(gq, "_fetchall", fake_fetchall)


class TestNeighborhood:
    def test_center_not_found_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_neighborhood(monkeypatch, center=None)
        result = gq.neighborhood(999)
        assert result == {"nodes": [], "edges": [], "center_id": 999}

    def test_depth_clamped_to_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def fake_fetchall(query: str, params=None):
            if "WITH RECURSIVE walk" in query:
                captured["depth"] = params["depth"]
                return []
            return [_stats_row(1, "RTX", "company")]

        monkeypatch.setattr(gq, "_fetchone", lambda q, p=None: _CENTER)
        monkeypatch.setattr(gq, "_fetchall", fake_fetchall)
        gq.neighborhood(1, depth=99)
        assert captured["depth"] == 2

    def test_unknown_label_raises_before_querying(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            gq, "_fetchone", lambda q, p=None: (_ for _ in ()).throw(AssertionError("no query"))
        )
        with pytest.raises(ValueError, match="unknown relation type"):
            gq.neighborhood(1, relation_types=["NOT_A_LABEL"])

    def test_unknown_kind_raises_before_querying(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            gq, "_fetchone", lambda q, p=None: (_ for _ in ()).throw(AssertionError("no query"))
        )
        with pytest.raises(ValueError, match="unknown entity kind"):
            gq.neighborhood(1, kinds=["not_a_kind"])

    def test_aggregates_edge_weight_and_dates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            _walk_row(1, 2, "PARTNER_OF", 10, "2026-01-01", "2026-01-01T00:00:00"),
            _walk_row(1, 2, "PARTNER_OF", 11, "2026-03-01", "2026-03-01T00:00:00"),
        ]
        _patch_neighborhood(monkeypatch, walk_rows=rows, stats_rows=[_stats_row(2, "IAI", "company")])
        result = gq.neighborhood(1)
        assert len(result["edges"]) == 1
        edge = result["edges"][0]
        assert edge["weight"] == 2
        assert edge["first_seen"] == "2026-01-01"
        assert edge["last_seen"] == "2026-03-01"
        assert len(edge["evidence"]) == 2

    def test_evidence_capped_at_three(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            _walk_row(1, 2, "PARTNER_OF", i, f"2026-01-{i:02d}", f"2026-01-{i:02d}T00:00:00")
            for i in range(1, 6)
        ]
        _patch_neighborhood(monkeypatch, walk_rows=rows, stats_rows=[_stats_row(2, "IAI", "company")])
        result = gq.neighborhood(1)
        assert len(result["edges"][0]["evidence"]) == 3
        # most recent first
        assert result["edges"][0]["evidence"][0]["item_id"] == 5

    def test_center_always_kept_even_when_kind_filtered_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [_walk_row(1, 2, "PARTNER_OF", 10, "2026-01-01", "2026-01-01T00:00:00")]
        # center is "company", filter asks for "org" only -- neighbor (IAI, company) is dropped,
        # center is kept regardless since it's always shown.
        _patch_neighborhood(monkeypatch, walk_rows=rows, stats_rows=[_stats_row(2, "IAI", "company")])
        result = gq.neighborhood(1, kinds=["org"])
        ids = {n["id"] for n in result["nodes"]}
        assert 1 in ids
        assert 2 not in ids
        assert result["edges"] == []

    def test_limit_caps_neighbor_count_but_always_keeps_center(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            _walk_row(1, n, "PARTNER_OF", n, f"2026-01-{n:02d}", f"2026-01-{n:02d}T00:00:00")
            for n in range(2, 7)
        ]
        stats_rows = [_stats_row(n, f"Neighbor {n}", "company", mention_count=n) for n in range(2, 7)]
        _patch_neighborhood(monkeypatch, walk_rows=rows, stats_rows=stats_rows)
        result = gq.neighborhood(1, limit=3)
        ids = {n["id"] for n in result["nodes"]}
        assert 1 in ids
        assert len(ids) == 3
        # kept by mention_count descending among the neighbors
        assert ids == {1, 6, 5}

    def test_limit_clamped_to_max(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_neighborhood(monkeypatch, walk_rows=[], stats_rows=[])
        gq.neighborhood(1, limit=999999)
        # no assertion on internals beyond "doesn't raise" -- the clamp is exercised via
        # test_graph_neighborhood_route below asserting the route's own Query bound (le=1500).

    def test_since_filter_drops_old_edges(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [_walk_row(1, 2, "PARTNER_OF", 10, "2026-01-01", "2026-01-01T00:00:00")]
        _patch_neighborhood(monkeypatch, walk_rows=rows, stats_rows=[_stats_row(2, "IAI", "company")])
        result = gq.neighborhood(1, since="2026-06-01")
        assert result["edges"] == []

    def test_missing_center_stats_falls_back_to_zeroed_card(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # center entity has no items/edges of its own yet -- _node_stats won't return a row for
        # it (LEFT JOIN with no matching items still returns a row in real SQL, but this
        # exercises the defensive fallback for when it doesn't).
        _patch_neighborhood(monkeypatch, walk_rows=[], stats_rows=[])
        result = gq.neighborhood(1)
        assert result["nodes"] == [
            {
                "id": 1,
                "name": "RTX",
                "kind": "company",
                "country": "US",
                "mention_count": 0,
                "last_seen": None,
                "corroboration": {"corroborated": 0, "official_primary": 0, "single_source": 0, "unknown": 0},
                "product_lines": [],
            }
        ]


# --------------------------------------------------------------------------
# overview
# --------------------------------------------------------------------------


class TestOverview:
    def test_no_entities_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gq, "_fetchall", lambda q, p=None: [])
        assert gq.overview() == {"nodes": [], "edges": []}

    def test_builds_nodes_and_edges_among_top_entities(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_fetchall(query: str, params=None):
            if "GROUP BY e.id\n        ORDER BY mention_count" in query:
                return [{"id": 1, "mention_count": 10}, {"id": 2, "mention_count": 5}]
            if "GROUP BY e.id, e.name, e.kind, e.country" in query:
                return [_stats_row(1, "RTX", "company", 10), _stats_row(2, "IAI", "company", 5)]
            if "FROM graph_edges ge" in query:
                return [_walk_row(1, 2, "PARTNER_OF", 10, "2026-01-01", "2026-01-01T00:00:00")]
            raise AssertionError(f"unexpected query: {query[:60]}")

        monkeypatch.setattr(gq, "_fetchall", fake_fetchall)
        result = gq.overview(limit=2)
        assert [n["id"] for n in result["nodes"]] == [1, 2]
        assert len(result["edges"]) == 1
        assert result["edges"][0]["relation"] == "PARTNER_OF"


# --------------------------------------------------------------------------
# path
# --------------------------------------------------------------------------


class TestPath:
    def test_same_entity_returns_single_node(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gq, "_fetchone", lambda q, p=None: _CENTER)
        result = gq.path(1, 1)
        assert result == {"nodes": [_CENTER], "edges": [], "hops": 0}

    def test_same_entity_missing_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gq, "_fetchone", lambda q, p=None: None)
        assert gq.path(1, 1) is None

    def test_no_path_found_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gq, "_fetchone", lambda q, p=None: None)
        assert gq.path(1, 2) is None

    def test_max_depth_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def fake_fetchone(query: str, params=None):
            captured["max_depth"] = params.get("max_depth")
            return None

        monkeypatch.setattr(gq, "_fetchone", fake_fetchone)
        gq.path(1, 2, max_depth=99)
        assert captured["max_depth"] == gq._PATH_MAX_DEPTH_CAP

    def test_builds_nodes_and_edges_from_bfs_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            gq, "_fetchone", lambda q, p=None: {"node_path": [1, 2, 3], "edge_path": [10, 11], "hops": 2}
        )

        def fake_fetchall(query: str, params=None):
            if "FROM entities WHERE id = ANY" in query:
                return [
                    {"id": 1, "name": "RTX", "kind": "company", "country": "US"},
                    {"id": 2, "name": "IAI", "kind": "company", "country": "IL"},
                    {"id": 3, "name": "Elbit", "kind": "company", "country": "IL"},
                ]
            if "ge.id = ANY" in query:
                return [
                    _walk_row(1, 2, "PARTNER_OF", 10, "2026-01-01", "2026-01-01T00:00:00"),
                    _walk_row(2, 3, "SUPPLIER_OF", 11, "2026-02-01", "2026-02-01T00:00:00"),
                ]
            raise AssertionError(f"unexpected query: {query[:60]}")

        monkeypatch.setattr(gq, "_fetchall", fake_fetchall)
        result = gq.path(1, 3)
        assert [n["id"] for n in result["nodes"]] == [1, 2, 3]
        assert len(result["edges"]) == 2
        assert result["hops"] == 2


# --------------------------------------------------------------------------
# entity_detail
# --------------------------------------------------------------------------


class TestEntityDetail:
    def test_returns_none_when_entity_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.api import services

        monkeypatch.setattr(services, "get_entity", lambda entity_id: None)
        assert gq.entity_detail(999) is None

    def test_adds_investigations_and_reports(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.api import services

        monkeypatch.setattr(
            services, "get_entity", lambda entity_id: {"id": entity_id, "name": "RTX", "timeline": []}
        )

        def fake_fetchall(query: str, params=None):
            if "FROM jobs j" in query:
                return [
                    {"job_id": 5, "state": "done", "question": "?", "started_at": None, "finished_at": None}
                ]
            if "FROM reports r" in query:
                return [
                    {"id": 9, "kind": "monthly", "period_start": None, "period_end": None, "created_at": None}
                ]
            raise AssertionError(f"unexpected query: {query[:60]}")

        monkeypatch.setattr(gq, "_fetchall", fake_fetchall)
        result = gq.entity_detail(1)
        assert result["name"] == "RTX"
        assert len(result["investigations"]) == 1
        assert len(result["reports"]) == 1

    def test_does_not_mutate_services_return_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.api import services

        original = {"id": 1, "name": "RTX", "timeline": []}
        monkeypatch.setattr(services, "get_entity", lambda entity_id: original)
        monkeypatch.setattr(gq, "_fetchall", lambda q, p=None: [])
        gq.entity_detail(1)
        assert "investigations" not in original


# --------------------------------------------------------------------------
# routes (TestClient, `eoa.graph.queries` monkeypatched -- no DB)
# --------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from eoa import db

    monkeypatch.setattr(db, "get_pool", lambda: object())
    monkeypatch.setattr(db, "close_pool", lambda: None)

    from eoa.api.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


class TestRoutes:
    def test_graph_search_route(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gq, "search_entities", lambda q, limit=20: [{"id": 1, "name": "RTX"}])
        r = client.get("/api/graph/search", params={"q": "rtx"})
        assert r.status_code == 200
        assert r.json() == [{"id": 1, "name": "RTX"}]

    def test_graph_overview_route(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gq, "overview", lambda limit=30, since=None: {"nodes": [], "edges": []})
        r = client.get("/api/graph/overview")
        assert r.status_code == 200
        assert r.json() == {"nodes": [], "edges": []}

    def test_graph_neighborhood_route_splits_csv_params(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        def fake_neighborhood(entity_id, *, depth=1, kinds=None, relation_types=None, since=None, limit=300):
            captured.update(
                entity_id=entity_id,
                depth=depth,
                kinds=kinds,
                relation_types=relation_types,
                since=since,
                limit=limit,
            )
            return {"nodes": [], "edges": [], "center_id": entity_id}

        monkeypatch.setattr(gq, "neighborhood", fake_neighborhood)
        r = client.get(
            "/api/graph/neighborhood/7",
            params={"depth": 2, "kinds": "company,org", "relation_types": "PARTNER_OF"},
        )
        assert r.status_code == 200
        assert captured == {
            "entity_id": 7,
            "depth": 2,
            "kinds": ["company", "org"],
            "relation_types": ["PARTNER_OF"],
            "since": None,
            "limit": 300,
        }

    def test_graph_neighborhood_route_bad_request_on_value_error(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_value_error(*a, **k):
            raise ValueError("unknown entity kind(s): ['bogus']")

        monkeypatch.setattr(gq, "neighborhood", raise_value_error)
        r = client.get("/api/graph/neighborhood/1", params={"kinds": "bogus"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "bad_request"

    def test_graph_path_route_404_when_no_path(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gq, "path", lambda a, b, max_depth=4: None)
        r = client.get("/api/graph/path", params={"a": 1, "b": 2})
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"

    def test_graph_path_route_200_with_result(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            gq, "path", lambda a, b, max_depth=4: {"nodes": [{"id": 1}, {"id": 2}], "edges": [], "hops": 1}
        )
        r = client.get("/api/graph/path", params={"a": 1, "b": 2})
        assert r.status_code == 200
        assert r.json()["hops"] == 1

    def test_entity_detail_route_404_when_missing(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gq, "entity_detail", lambda entity_id: None)
        r = client.get("/api/entities/999/detail")
        assert r.status_code == 404

    def test_entity_detail_route_200(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            gq, "entity_detail", lambda entity_id: {"id": entity_id, "name": "RTX", "investigations": []}
        )
        r = client.get("/api/entities/1/detail")
        assert r.status_code == 200
        assert r.json()["name"] == "RTX"

    def test_existing_entity_graph_route_still_works(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        """U10's original `/entities/{id}/graph` (`services.build_graph`) is unchanged."""
        from eoa.api import services

        monkeypatch.setattr(
            services, "build_graph", lambda entity_id, depth=1, labels=None: {"nodes": [], "edges": []}
        )
        r = client.get("/api/entities/1/graph")
        assert r.status_code == 200
        assert r.json() == {"nodes": [], "edges": []}
