"""Unit tests for `eoa.memory.graph` (plain-SQL `graph_edges`, no Apache AGE/Cypher).

2026-09-05 (ADR-004): `eoa.memory.graph` was rewritten off Apache AGE onto plain SQL
(`graph_edges`). This file replaces the old Cypher/agtype-focused test suite: instead
of monkeypatching `_run_cypher` (which no longer exists), it monkeypatches
`eoa.db.connection` with a minimal fake psycopg3 connection/cursor, in the same style
`tests/unit/test_persist_analysis.py` uses for `eoa.db.connection`. No DB required.
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

from eoa.memory import graph


class _FakeCursor:
    """Minimal stand-in for `conn.cursor()`'s context-managed cursor."""

    def __init__(self, rows: list[dict] | None) -> None:
        self._rows = rows or []
        self.executed: tuple[str, dict | tuple | None] | None = None

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, query: str, params: dict | tuple | None = None) -> None:
        self.executed = (query, params)

    def fetchall(self) -> list[dict]:
        return self._rows

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None


class _FakeConnection:
    """Minimal stand-in for `eoa.db.connection()`'s context-managed connection."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows
        self.last_cursor: _FakeCursor | None = None

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def cursor(self) -> _FakeCursor:
        self.last_cursor = _FakeCursor(self._rows)
        return self.last_cursor


def _fail_connection() -> _FakeConnection:
    raise AssertionError("connection() should not be called for an invalid label")


def _patch_connection(monkeypatch: pytest.MonkeyPatch, rows: list[dict] | None = None) -> _FakeConnection:
    conn = _FakeConnection(rows)
    monkeypatch.setattr(graph, "connection", lambda: conn)
    return conn


# --------------------------------------------------------------------------
# label validation (shared by add_edge/neighbors/edges_of)
# --------------------------------------------------------------------------


class TestLabelValidation:
    def test_add_edge_unknown_label_raises_before_querying(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(graph, "connection", _fail_connection)
        with pytest.raises(ValueError, match="unknown edge label"):
            graph.add_edge(1, 2, "NOT_A_LABEL", 1)

    def test_neighbors_unknown_label_raises_before_querying(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(graph, "connection", _fail_connection)
        with pytest.raises(ValueError, match="unknown edge label"):
            graph.neighbors(1, label="NOT_A_LABEL")

    def test_edges_of_unknown_label_raises_before_querying(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(graph, "connection", _fail_connection)
        with pytest.raises(ValueError, match="unknown edge label"):
            graph.edges_of(1, label="NOT_A_LABEL")


# --------------------------------------------------------------------------
# add_edge
# --------------------------------------------------------------------------


class TestAddEdge:
    def test_upsert_query_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        graph.add_edge(1, 2, "PARTNER_OF", 42, {"evidence": "some evidence"})

        query, params = conn.last_cursor.executed
        assert "INSERT INTO graph_edges" in query
        assert "ON CONFLICT (src_entity_id, dst_entity_id, label, item_id)" in query
        assert params["src"] == 1
        assert params["dst"] == 2
        assert params["label"] == "PARTNER_OF"
        assert params["item_id"] == 42

    def test_returns_empty_dict_when_no_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connection(monkeypatch, rows=[])
        assert graph.add_edge(1, 2, "PARTNER_OF", 1) == {}

    def test_returns_row_as_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = {
            "id": 5,
            "src_entity_id": 1,
            "dst_entity_id": 2,
            "label": "SUPPLIER_OF",
            "item_id": 99,
            "props": {"evidence": "proof"},
            "created_at": None,
            "updated_at": None,
        }
        _patch_connection(monkeypatch, rows=[row])
        result = graph.add_edge(1, 2, "SUPPLIER_OF", 99, {"evidence": "proof"})
        assert result["item_id"] == 99
        assert result["props"]["evidence"] == "proof"

    def test_props_defaults_to_empty_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        graph.add_edge(1, 2, "ACQUIRED", None)
        _query, params = conn.last_cursor.executed
        assert params["item_id"] is None
        # Json(...) wraps the dict; confirm no nested "props" leak into src/dst/label.
        assert params["src"] == 1 and params["dst"] == 2 and params["label"] == "ACQUIRED"


# --------------------------------------------------------------------------
# merge_entity
# --------------------------------------------------------------------------


class TestMergeEntity:
    def test_update_query_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(
            monkeypatch, rows=[{"entity_id": 1, "name": "RTX", "kind": "company", "country": "US"}]
        )
        result = graph.merge_entity(1, "RTX", "company", None)

        query, params = conn.last_cursor.executed
        assert "UPDATE entities" in query
        assert "COALESCE(%(country)s, country)" in query
        assert params == {"entity_id": 1, "name": "RTX", "kind": "company", "country": None}
        assert result["entity_id"] == 1

    def test_returns_empty_dict_when_entity_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connection(monkeypatch, rows=[])
        assert graph.merge_entity(999, "Ghost Co", "company") == {}


# --------------------------------------------------------------------------
# neighbors
# --------------------------------------------------------------------------


class TestNeighbors:
    def test_no_label_depth_1_query_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        graph.neighbors(5)

        query, params = conn.last_cursor.executed
        assert "WITH RECURSIVE walk" in query
        assert "JOIN entities e ON e.id = w.endpoint" in query
        assert params == {"eid": 5, "depth": 1}
        assert "%(label)s" not in query  # no label param referenced when label=None

    def test_with_label_includes_label_param(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        graph.neighbors(7, label="PARTNER_OF", depth=2)

        query, params = conn.last_cursor.executed
        assert "ge.label = %(label)s" in query
        assert params == {"eid": 7, "depth": 2, "label": "PARTNER_OF"}

    def test_depth_clamped_to_max_3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        graph.neighbors(1, depth=99)
        _query, params = conn.last_cursor.executed
        assert params["depth"] == 3

    def test_depth_clamped_to_min_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        graph.neighbors(1, depth=0)
        _query, params = conn.last_cursor.executed
        assert params["depth"] == 1

    def test_returns_rows_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"entity_id": 9, "name": "IAI", "kind": "company", "country": "IL"}]
        _patch_connection(monkeypatch, rows=rows)
        result = graph.neighbors(5)
        assert result == rows


# --------------------------------------------------------------------------
# edges_of
# --------------------------------------------------------------------------


class TestEdgesOf:
    def test_query_shape_no_label(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        graph.edges_of(5)

        query, params = conn.last_cursor.executed
        assert "WITH RECURSIVE walk" in query
        assert "JOIN entities s ON s.id = w.src_entity_id" in query
        assert "JOIN entities d ON d.id = w.dst_entity_id" in query
        assert params == {"eid": 5, "depth": 1}

    def test_query_shape_with_label_and_depth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        graph.edges_of(7, label="PARTNER_OF", depth=3)
        _query, params = conn.last_cursor.executed
        assert params == {"eid": 7, "depth": 3, "label": "PARTNER_OF"}

    def test_parses_row_into_edgerow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {
                "edge_id": 1,
                "src_entity_id": 5,
                "dst_entity_id": 9,
                "label": "PARTNER_OF",
                "item_id": 42,
                "props": {"evidence": 'ידיעה על שת"פ'},
                "created_at": "2026-09-05T00:00:00+00:00",
                "src_name": "RTX",
                "dst_name": "IAI",
            }
        ]
        _patch_connection(monkeypatch, rows=rows)
        result = graph.edges_of(5)

        assert len(result) == 1
        e = result[0]
        assert isinstance(e, graph.EdgeRow)
        assert e.src_entity_id == 5
        assert e.src_name == "RTX"
        assert e.dst_entity_id == 9
        assert e.dst_name == "IAI"
        assert e.label == "PARTNER_OF"
        assert e.item_id == 42
        assert e.evidence == 'ידיעה על שת"פ'
        assert e.created_at == "2026-09-05T00:00:00+00:00"

    def test_missing_props_evidence_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {
                "edge_id": 1,
                "src_entity_id": 5,
                "dst_entity_id": 9,
                "label": "PARTNER_OF",
                "item_id": None,
                "props": {},
                "created_at": None,
                "src_name": "RTX",
                "dst_name": "IAI",
            }
        ]
        _patch_connection(monkeypatch, rows=rows)
        result = graph.edges_of(5)
        assert result[0].item_id is None
        assert result[0].evidence is None

    def test_null_props_column_treated_as_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {
                "edge_id": 1,
                "src_entity_id": 5,
                "dst_entity_id": 9,
                "label": "PARTNER_OF",
                "item_id": 7,
                "props": None,
                "created_at": None,
                "src_name": "RTX",
                "dst_name": "IAI",
            }
        ]
        _patch_connection(monkeypatch, rows=rows)
        result = graph.edges_of(5)
        assert result[0].evidence is None
        assert result[0].item_id == 7

    def test_no_rows_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connection(monkeypatch, rows=[])
        assert graph.edges_of(5) == []


# --------------------------------------------------------------------------
# edge_stats
# --------------------------------------------------------------------------


class TestEdgeStats:
    def test_query_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        graph.edge_stats()
        query, _params = conn.last_cursor.executed
        assert "SELECT label, count(*) AS n FROM graph_edges GROUP BY label" in query

    def test_defaults_all_labels_to_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connection(monkeypatch, rows=[])
        stats = graph.edge_stats()
        assert set(stats.keys()) == graph.EDGE_LABELS
        assert all(v == 0 for v in stats.values())

    def test_counts_by_label(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"label": "PARTNER_OF", "n": 3}, {"label": "COMPETITOR_OF", "n": 5}]
        _patch_connection(monkeypatch, rows=rows)
        stats = graph.edge_stats()
        assert stats["PARTNER_OF"] == 3
        assert stats["COMPETITOR_OF"] == 5
        assert stats["SUPPLIER_OF"] == 0

    def test_unknown_label_in_results_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"label": "NOT_A_REAL_LABEL", "n": 1}]
        _patch_connection(monkeypatch, rows=rows)
        stats = graph.edge_stats()
        assert "NOT_A_REAL_LABEL" not in stats


# --------------------------------------------------------------------------
# named analytic queries
# --------------------------------------------------------------------------


class TestNamedQueries:
    def test_partners_of_competitors_query_shape_and_passthrough(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [{"entity_id": 3, "name": "Elbit", "kind": "company", "country": "IL"}]
        conn = _patch_connection(monkeypatch, rows=rows)
        result = graph.partners_of_competitors("RTX")
        query, params = conn.last_cursor.executed
        assert "COMPETITOR_OF" in query and "PARTNER_OF" in query
        assert params == {"name": "RTX"}
        assert result == rows

    def test_suppliers_of_program_bidders_query_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        graph.suppliers_of_program_bidders("Blue Horizon")
        query, params = conn.last_cursor.executed
        assert "BIDS_AGAINST" in query and "SUPPLIER_OF" in query
        assert params == {"name": "Blue Horizon"}

    def test_startups_linked_to_majors_query_shape_and_default_min_links(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        graph.startups_linked_to_majors()
        query, params = conn.last_cursor.executed
        assert "link_count" in query
        assert params == {"min_links": 2}

    def test_startups_linked_to_majors_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"entity_id": 1, "name": "Startup Co", "kind": "company", "country": "US", "link_count": 4}]
        _patch_connection(monkeypatch, rows=rows)
        assert graph.startups_linked_to_majors(min_links=3) == rows


# --------------------------------------------------------------------------
# ensure_graph
# --------------------------------------------------------------------------


class TestEnsureGraph:
    def test_raises_when_table_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connection(monkeypatch, rows=[{"t": None}])
        with pytest.raises(RuntimeError, match="graph_edges"):
            graph.ensure_graph()

    def test_no_rows_also_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connection(monkeypatch, rows=[])
        with pytest.raises(RuntimeError, match="graph_edges"):
            graph.ensure_graph()

    def test_passes_when_table_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connection(monkeypatch, rows=[{"t": "graph_edges"}])
        graph.ensure_graph()  # should not raise


# --------------------------------------------------------------------------
# entity_timeline (plain relational join, unchanged from before this rewrite)
# --------------------------------------------------------------------------


class TestEntityTimeline:
    def test_query_shape_and_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"id": 1, "kind": "contract_award"}]
        conn = _patch_connection(monkeypatch, rows=rows)
        result = graph.entity_timeline(42)
        query, params = conn.last_cursor.executed
        assert "JOIN events ev" in query
        assert params == (42,)
        assert result == rows
