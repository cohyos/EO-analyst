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
