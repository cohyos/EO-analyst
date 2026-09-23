"""Unit tests for `eoa.memory.vector` (numpy cosine similarity over `items.embedding real[]`).

2026-09-05 (ADR-004): pgvector is gone -- `nearest()`/`find_duplicate()` now load
candidate embeddings via plain SQL and score them in numpy. No DB required: `connection()`
is monkeypatched with a minimal fake psycopg3 connection/cursor, in the same style
`tests/unit/test_graph_edges.py` / `tests/unit/test_persist_analysis.py` use.
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

from eoa.memory import vector


class _FakeCursor:
    def __init__(self, rows: list[dict] | None) -> None:
        self._rows = rows or []
        self.executed: tuple[str, dict | None] | None = None

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, query: str, params: dict | None = None) -> None:
        self.executed = (query, params)

    def fetchall(self) -> list[dict]:
        return self._rows

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None


class _FakeConnection:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows
        self.last_cursor: _FakeCursor | None = None

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def cursor(self, row_factory=None) -> _FakeCursor:
        self.last_cursor = _FakeCursor(self._rows)
        return self.last_cursor


def _patch_connection(monkeypatch: pytest.MonkeyPatch, rows: list[dict] | None = None) -> _FakeConnection:
    conn = _FakeConnection(rows)
    monkeypatch.setattr(vector, "connection", lambda: conn)
    return conn


# --------------------------------------------------------------------------
# upsert_embedding
# --------------------------------------------------------------------------


class TestUpsertEmbedding:
    def test_query_shape_and_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        vector.upsert_embedding(7, [0.1, 0.2, 0.3])

        query, params = conn.last_cursor.executed
        assert "UPDATE items SET embedding" in query
        assert params["item_id"] == 7
        assert params["vec"] == [0.1, 0.2, 0.3]
        assert all(isinstance(x, float) for x in params["vec"])


# --------------------------------------------------------------------------
# nearest
# --------------------------------------------------------------------------


class TestNearest:
    def test_zero_query_vector_returns_empty_without_querying(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fail() -> None:
            raise AssertionError("connection() should not be called for an all-zero query vector")

        monkeypatch.setattr(vector, "connection", fail)
        assert vector.nearest([0.0, 0.0, 0.0]) == []

    def test_orders_by_similarity_descending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {"id": 1, "embedding": [0.0, 1.0]},  # orthogonal -> similarity 0
            {"id": 2, "embedding": [1.0, 0.0]},  # identical -> similarity 1
            {"id": 3, "embedding": [-1.0, 0.0]},  # opposite -> similarity -1
        ]
        _patch_connection(monkeypatch, rows=rows)

        result = vector.nearest([1.0, 0.0], limit=10)

        assert [item_id for item_id, _sim in result] == [2, 1, 3]
        assert result[0][1] == pytest.approx(1.0)
        assert result[1][1] == pytest.approx(0.0)
        assert result[2][1] == pytest.approx(-1.0)

    def test_respects_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"id": i, "embedding": [1.0, 0.0]} for i in range(5)]
        _patch_connection(monkeypatch, rows=rows)

        result = vector.nearest([1.0, 0.0], limit=2)
        assert len(result) == 2

    def test_skips_candidates_with_mismatched_dimension(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {"id": 1, "embedding": [1.0, 0.0, 0.0]},  # wrong dim, skipped
            {"id": 2, "embedding": [1.0, 0.0]},
        ]
        _patch_connection(monkeypatch, rows=rows)

        result = vector.nearest([1.0, 0.0], limit=10)
        assert [item_id for item_id, _sim in result] == [2]

    def test_skips_null_and_zero_candidates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {"id": 1, "embedding": None},
            {"id": 2, "embedding": []},
            {"id": 3, "embedding": [0.0, 0.0]},
            {"id": 4, "embedding": [1.0, 0.0]},
        ]
        _patch_connection(monkeypatch, rows=rows)

        result = vector.nearest([1.0, 0.0], limit=10)
        assert [item_id for item_id, _sim in result] == [4]

    def test_days_window_adds_clause_and_param(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        vector.nearest([1.0, 0.0], days=7)

        query, params = conn.last_cursor.executed
        assert "COALESCE(published_at, created_at) >= now()" in query
        assert params["days"] == 7

    def test_no_days_omits_clause(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        vector.nearest([1.0, 0.0])

        query, params = conn.last_cursor.executed
        assert "COALESCE(published_at, created_at)" not in query
        assert "days" not in params


# --------------------------------------------------------------------------
# find_duplicate
# --------------------------------------------------------------------------


class TestFindDuplicate:
    def test_no_candidates_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connection(monkeypatch, rows=[])
        assert vector.find_duplicate([1.0, 0.0], threshold=0.9, days=7) is None

    def test_below_threshold_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"id": 1, "embedding": [0.0, 1.0]}]  # similarity 0.0
        _patch_connection(monkeypatch, rows=rows)
        assert vector.find_duplicate([1.0, 0.0], threshold=0.9, days=7) is None

    def test_at_or_above_threshold_returns_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"id": 1, "embedding": [1.0, 0.0]}]  # similarity 1.0
        _patch_connection(monkeypatch, rows=rows)
        result = vector.find_duplicate([1.0, 0.0], threshold=0.9, days=7)
        assert result is not None
        item_id, similarity = result
        assert item_id == 1
        assert similarity == pytest.approx(1.0)
