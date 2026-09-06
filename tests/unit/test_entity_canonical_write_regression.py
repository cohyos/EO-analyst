"""Regression tests for the 2026-09-06 root-cause investigation into entity 394: canonicalised to
name='Israel'/kind='country' by ``scripts/repair_entities_normalize.py``, later found reverted to
name='ישראל'/kind='company' while a live LLM re-analyze backfill ran.

Kept in a standalone file (rather than added to ``tests/unit/test_upsert_entity_normalization.py``
or ``tests/unit/test_graph_edges.py``) since both were being concurrently edited by other agents
at the time this investigation ran.

Conclusion of the investigation (see file:line references in the accompanying docstrings/comments
in ``agent/eoa/memory/relational.py::upsert_entity``, ``agent/eoa/memory/graph.py::merge_entity``,
and ``agent/eoa/pipeline/classify.py::persist_classification``): no currently-committed/working-
tree write path can reproduce the exact reversion (every path resolves name/kind through
``eoa.pipeline.entity_normalize.canonical_name_and_kind`` -- deterministically and independent of
call order -- before writing), and the live DB shows entity 394 fully gone (zero ``graph_edges``
references) with a fresh canonical "Israel"/"country" row (id 1091) already in place by the time
of this investigation. The tests below pin down the two guarantees that make the *known* write
paths safe, as a regression net against this exact bug recurring.
"""

from __future__ import annotations

import sys
import types

if "eoa.db" not in sys.modules:
    try:
        import eoa.db  # noqa: F401
    except ImportError:
        fake_db = types.ModuleType("eoa.db")
        fake_db.connection = lambda: None  # type: ignore[attr-defined]
        fake_db.get_pool = lambda: None  # type: ignore[attr-defined]
        sys.modules["eoa.db"] = fake_db

from eoa.memory import graph, relational


class _FakeCursor:
    def __init__(self, responses: list[dict | None]):
        self._responses = list(responses)
        self.queries: list[tuple[str, dict]] = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params or {}))

    def fetchone(self):
        return self._responses.pop(0)

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_upsert_entity_hebrew_country_alias_never_renames_canonical_row(monkeypatch):
    """(i) upsert_entity with a Hebrew alias, called after the canonical row already exists, must
    not change `name` -- canonical_name_and_kind resolves 'ישראל' to ('Israel', 'country') BEFORE
    the SQL is built, and the INSERT ... ON CONFLICT (name) DO UPDATE clause never assigns to
    `name` at all (agent/eoa/memory/relational.py::upsert_entity)."""
    # First fetchone(): the case-insensitive existing-row lookup (no differently-cased match,
    # since the row is already spelled exactly "Israel"). Second: the INSERT ... RETURNING id.
    cursor = _FakeCursor([None, {"id": 394}])
    monkeypatch.setattr(relational, "connection", lambda: _FakeConn(cursor))

    entity_id = relational.upsert_entity(name="ישראל", kind="company")

    assert entity_id == 394
    insert_sql, insert_params = cursor.queries[-1]
    assert insert_params["name"] == "Israel"
    assert insert_params["kind"] == "country"
    # The ON CONFLICT clause never assigns to `name` -- structurally cannot revert a canonical row.
    assert "name = EXCLUDED.name" not in insert_sql
    assert "name =" not in insert_sql.split("DO UPDATE SET", 1)[1]


def test_upsert_entity_english_alias_same_guarantee(monkeypatch):
    """Same guarantee via the English spelling, so the test doesn't depend on Hebrew-only
    resolution: 'United States' or 'ארה"ב'-style aliases collapse the same way."""
    cursor = _FakeCursor([None, {"id": 500}])
    monkeypatch.setattr(relational, "connection", lambda: _FakeConn(cursor))

    entity_id = relational.upsert_entity(name="ארה\"ב", kind="org")

    assert entity_id == 500
    _sql, insert_params = cursor.queries[-1]
    assert insert_params["name"] == "United States"
    assert insert_params["kind"] == "country"


def test_merge_entity_canonicalizes_internally_even_with_raw_caller_input(monkeypatch):
    """Defense-in-depth (agent/eoa/memory/graph.py::merge_entity): even if a future caller passes
    a raw, non-canonical name/kind straight through (bypassing upsert_entity's own
    canonicalisation), merge_entity itself must never write the raw spelling over an
    already-canonical row."""

    class _GraphCursor:
        def __init__(self):
            self.executed: tuple[str, dict] | None = None

        def execute(self, query, params=None):
            self.executed = (query, params or {})

        def fetchone(self):
            return {"entity_id": 394, "name": "Israel", "kind": "country", "country": None}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _GraphConn:
        def __init__(self, cur):
            self._cur = cur

        def cursor(self, row_factory=None):
            return self._cur

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    cur = _GraphCursor()
    monkeypatch.setattr(graph, "connection", lambda: _GraphConn(cur))

    result = graph.merge_entity(394, "ישראל", "company", None)

    _query, params = cur.executed
    assert params["name"] == "Israel"
    assert params["kind"] == "country"
    assert result["name"] == "Israel"
