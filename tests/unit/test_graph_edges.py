"""Unit tests for `eoa.memory.graph.edges_of` / `edge_stats` (graph edge provenance).

No DB/AGE: `_run_cypher` is monkeypatched with a fake returning rows shaped
exactly like a real `cypher()` call would -- a list of dict rows keyed by
the requested output columns, each value a raw agtype string (JSON plus an
optional `::vertex`/`::edge` suffix), same as `tests/unit/test_agtype_parse.py`'s
fixtures. This exercises both the Cypher-string construction and the
agtype-parsing path without a database.
"""

from __future__ import annotations

import json
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


def _vertex(entity_id: int, name: str, kind: str = "company", country: str | None = "US") -> str:
    props: dict = {"entity_id": entity_id, "name": name, "kind": kind}
    if country is not None:
        props["country"] = country
    return json.dumps({"id": entity_id, "label": "Entity", "properties": props}) + "::vertex"


def _edge(edge_id: int, label: str, item_id: int | None = None, evidence: str | None = None) -> str:
    props: dict = {}
    if item_id is not None:
        props["item_id"] = item_id
    if evidence is not None:
        props["evidence"] = evidence
    return (
        json.dumps({"id": edge_id, "label": label, "start_id": 1, "end_id": 2, "properties": props})
        + "::edge"
    )


# --------------------------------------------------------------------------
# add_edge: Cypher string construction (regression -- AGE 1.7 rejects
# `SET r += $props` / `SET r = $props` with a parameterized map: "SET
# clause expects a map". The fix SETs each property individually through
# its own scalar parameter instead of merging a map in one shot.)
# --------------------------------------------------------------------------


class TestAddEdgeCypher:
    def test_no_map_merge_syntax_in_generated_cypher(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def fake_run_cypher(cypher_body, params, out_columns):
            captured.update(cypher_body=cypher_body, params=params, out_columns=out_columns)
            return []

        monkeypatch.setattr(graph, "_run_cypher", fake_run_cypher)
        graph.add_edge(1, 2, "PARTNER_OF", 42, {"evidence": "some evidence"})

        cypher_body = captured["cypher_body"]
        # The buggy forms must never reappear.
        assert "+= $props" not in cypher_body
        assert "= $props" not in cypher_body
        # Each property is set individually through its own scalar param.
        assert "r.item_id = $p" in cypher_body
        assert "r.evidence = $p" in cypher_body

    def test_params_are_flat_scalars_not_a_nested_map(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def fake_run_cypher(cypher_body, params, out_columns):
            captured["params"] = params
            return []

        monkeypatch.setattr(graph, "_run_cypher", fake_run_cypher)
        graph.add_edge(1, 2, "SUPPLIER_OF", 7, {"evidence": "ev"})

        params = captured["params"]
        assert params["src"] == 1
        assert params["dst"] == 2
        # No nested "props" dict anywhere in the params -- every value is a
        # flat scalar so AGE receives a genuine agtype scalar per param.
        assert "props" not in params
        assert set(params.values()) >= {1, 2, 7, "ev"}
        for value in params.values():
            assert not isinstance(value, dict)

    def test_item_id_and_evidence_round_trip_via_edges_of_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The generated SET clause still stamps item_id/evidence, so `edges_of()`
        (which reads those two property names) keeps working unchanged."""
        captured: dict = {}

        def fake_run_cypher(cypher_body, params, out_columns):
            captured.update(cypher_body=cypher_body, params=params)
            return [{"r": _edge(1, "SUPPLIER_OF", item_id=params["p1"], evidence=params["p0"])}]

        monkeypatch.setattr(graph, "_run_cypher", fake_run_cypher)
        result = graph.add_edge(1, 2, "SUPPLIER_OF", 99, {"evidence": "proof"})

        assert result["properties"]["item_id"] == 99
        assert result["properties"]["evidence"] == "proof"

    def test_unsafe_property_key_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fail(*a, **k):
            raise AssertionError("_run_cypher should not be called for an unsafe key")

        monkeypatch.setattr(graph, "_run_cypher", fail)
        with pytest.raises(ValueError, match="unsafe edge property key"):
            graph.add_edge(1, 2, "PARTNER_OF", 1, {"evidence; DROP TABLE entities;--": "x"})

    def test_unknown_edge_label_raises_before_querying(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fail(*a, **k):
            raise AssertionError("_run_cypher should not be called for an invalid label")

        monkeypatch.setattr(graph, "_run_cypher", fail)
        with pytest.raises(ValueError, match="unknown edge label"):
            graph.add_edge(1, 2, "NOT_A_LABEL", 1)


# --------------------------------------------------------------------------
# edges_of: Cypher string construction
# --------------------------------------------------------------------------


class TestEdgesOfCypher:
    def test_no_label_depth_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def fake_run_cypher(cypher_body, params, out_columns):
            captured.update(cypher_body=cypher_body, params=params, out_columns=out_columns)
            return []

        monkeypatch.setattr(graph, "_run_cypher", fake_run_cypher)
        graph.edges_of(5)

        assert captured["params"] == {"eid": 5}
        assert "MATCH p = (a:Entity {entity_id: $eid})-[*1..1]-(b:Entity)" in captured["cypher_body"]
        assert "UNWIND relationships(p) AS r" in captured["cypher_body"]
        assert captured["out_columns"] == "s agtype, e agtype, r agtype"

    def test_with_label_and_depth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def fake_run_cypher(cypher_body, params, out_columns):
            captured["cypher_body"] = cypher_body

        monkeypatch.setattr(graph, "_run_cypher", lambda *a, **k: fake_run_cypher(*a, **k) or [])
        graph.edges_of(7, label="PARTNER_OF", depth=3)

        assert "[:PARTNER_OF*1..3]" in captured["cypher_body"]

    def test_unknown_label_raises_before_querying(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fail(*a, **k):
            raise AssertionError("_run_cypher should not be called for an invalid label")

        monkeypatch.setattr(graph, "_run_cypher", fail)
        with pytest.raises(ValueError, match="unknown edge label"):
            graph.edges_of(1, label="NOT_A_LABEL")


# --------------------------------------------------------------------------
# edges_of: agtype parsing
# --------------------------------------------------------------------------


class TestEdgesOfParsing:
    def test_parses_edge_row_into_edgerow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {
                "s": _vertex(5, "RTX", country="US"),
                "e": _vertex(9, "IAI", country="IL"),
                "r": _edge(1, "PARTNER_OF", item_id=42, evidence='ידיעה על שת"פ'),
            }
        ]
        monkeypatch.setattr(graph, "_run_cypher", lambda *a, **k: rows)

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
        assert e.created_at is None  # never stamped today -- honest, not invented

    def test_multiple_edges_both_directions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {"s": _vertex(5, "RTX"), "e": _vertex(9, "IAI"), "r": _edge(1, "PARTNER_OF", item_id=1)},
            {"s": _vertex(9, "IAI"), "e": _vertex(5, "RTX"), "r": _edge(2, "COMPETITOR_OF", item_id=2)},
        ]
        monkeypatch.setattr(graph, "_run_cypher", lambda *a, **k: rows)

        result = graph.edges_of(5)
        assert len(result) == 2
        assert {e.label for e in result} == {"PARTNER_OF", "COMPETITOR_OF"}
        by_label = {e.label: e for e in result}
        assert by_label["PARTNER_OF"].src_entity_id == 5
        assert by_label["COMPETITOR_OF"].src_entity_id == 9

    def test_missing_item_id_and_evidence_are_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"s": _vertex(5, "RTX"), "e": _vertex(9, "IAI"), "r": _edge(1, "PARTNER_OF")}]
        monkeypatch.setattr(graph, "_run_cypher", lambda *a, **k: rows)

        result = graph.edges_of(5)
        assert result[0].item_id is None
        assert result[0].evidence is None

    def test_skips_row_with_unparseable_vertex(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"s": "not json at all", "e": _vertex(9, "IAI"), "r": _edge(1, "PARTNER_OF")}]
        monkeypatch.setattr(graph, "_run_cypher", lambda *a, **k: rows)

        assert graph.edges_of(5) == []

    def test_tolerates_already_flattened_vertex_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`_vertex_fields`/`_edge_fields` tolerate a dict without a nested "properties" key too."""
        rows = [
            {
                "s": {"entity_id": 5, "name": "RTX"},
                "e": {"entity_id": 9, "name": "IAI"},
                "r": {"label": "PARTNER_OF", "item_id": 7},
            }
        ]
        monkeypatch.setattr(graph, "_run_cypher", lambda *a, **k: rows)

        result = graph.edges_of(5)
        assert len(result) == 1
        assert result[0].src_name == "RTX"
        assert result[0].item_id == 7


# --------------------------------------------------------------------------
# edge_stats
# --------------------------------------------------------------------------


class TestEdgeStats:
    def test_cypher_construction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def fake_run_cypher(cypher_body, params, out_columns):
            captured.update(cypher_body=cypher_body, params=params, out_columns=out_columns)
            return []

        monkeypatch.setattr(graph, "_run_cypher", fake_run_cypher)
        graph.edge_stats()

        assert "MATCH ()-[r]->()" in captured["cypher_body"]
        assert "label(r)" in captured["cypher_body"]
        assert captured["out_columns"] == "lbl agtype, n agtype"

    def test_defaults_all_labels_to_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(graph, "_run_cypher", lambda *a, **k: [])
        stats = graph.edge_stats()
        assert set(stats.keys()) == graph.EDGE_LABELS
        assert all(v == 0 for v in stats.values())

    def test_counts_by_label(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"lbl": '"PARTNER_OF"', "n": "3"}, {"lbl": '"COMPETITOR_OF"', "n": "5"}]
        monkeypatch.setattr(graph, "_run_cypher", lambda *a, **k: rows)

        stats = graph.edge_stats()
        assert stats["PARTNER_OF"] == 3
        assert stats["COMPETITOR_OF"] == 5
        assert stats["SUPPLIER_OF"] == 0

    def test_unknown_label_in_results_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"lbl": '"NOT_A_REAL_LABEL"', "n": "1"}]
        monkeypatch.setattr(graph, "_run_cypher", lambda *a, **k: rows)

        stats = graph.edge_stats()
        assert "NOT_A_REAL_LABEL" not in stats


# --------------------------------------------------------------------------
# add_edge: live-DB regression for the "SET clause expects a map" bug.
# Requires the docker-compose stack (`docker compose up postgres`) with
# `db/graph_init.sql` applied. Skips gracefully when unreachable; always
# cleans up the one edge it creates, identified by a marker `item_id` no
# real pipeline would ever use, so it never touches anyone else's data.
# --------------------------------------------------------------------------


@pytest.mark.integration
class TestAddEdgeLiveDB:
    _MARKER_ITEM_ID = -987654321

    def test_add_edge_then_read_back_via_edges_of(self) -> None:
        pytest.importorskip("psycopg")
        try:
            from eoa.db import connection

            with connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT id FROM entities ORDER BY id LIMIT 2")
                rows = cur.fetchall()
        except Exception as exc:
            pytest.skip(f"live DB unreachable: {exc}")

        if len(rows) < 2:
            pytest.skip("need at least 2 existing entities in the live DB for this test")

        src_id, dst_id = rows[0]["id"], rows[1]["id"]

        try:
            created = graph.add_edge(
                src_id,
                dst_id,
                "DERIVED_FROM",
                self._MARKER_ITEM_ID,
                {"evidence": "integration test edge -- safe to ignore/delete"},
            )
            assert created.get("properties", {}).get("item_id") == self._MARKER_ITEM_ID
            assert (
                created.get("properties", {}).get("evidence")
                == "integration test edge -- safe to ignore/delete"
            )

            edges = graph.edges_of(src_id, label="DERIVED_FROM")
            match = [e for e in edges if e.item_id == self._MARKER_ITEM_ID]
            assert match, "edge just created via add_edge() was not found by edges_of()"
            assert match[0].src_entity_id == src_id
            assert match[0].dst_entity_id == dst_id
            assert match[0].evidence == "integration test edge -- safe to ignore/delete"
        finally:
            # Clean up: delete only the edge(s) carrying our marker item_id,
            # never a blanket delete that could touch real data.
            graph._run_cypher(
                """
                MATCH (a:Entity {entity_id: $src})-[r:DERIVED_FROM]->(b:Entity {entity_id: $dst})
                WHERE r.item_id = $marker
                DELETE r
                """,
                {"src": src_id, "dst": dst_id, "marker": self._MARKER_ITEM_ID},
                "r agtype",
            )
