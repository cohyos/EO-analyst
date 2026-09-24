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


# --------------------------------------------------------------------------
# F08: exclude_id -- a retry must never match an item against its own already-committed embedding
# --------------------------------------------------------------------------


class TestExcludeSelf:
    def test_load_candidates_adds_exclude_clause_and_param(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        vector.nearest([1.0, 0.0], days=7, exclude_id=42)

        query, params = conn.last_cursor.executed
        assert "id != %(exclude_id)s" in query
        assert params["exclude_id"] == 42

    def test_no_exclude_id_omits_clause(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        vector.nearest([1.0, 0.0])

        query, params = conn.last_cursor.executed
        assert "exclude_id" not in query
        assert "exclude_id" not in params

    def test_find_duplicate_excludes_its_own_just_committed_vector(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression for F08: a retry re-embeds item 5 while its own vector (committed on a prior,
        crashed run before the stage marker was written) is already in the DB. Without
        `exclude_id`, the item would match itself at cosine 1.0 and become its own `dedup_of`."""
        rows = [
            {"id": 5, "embedding": [1.0, 0.0]},  # item 5's own already-committed vector
            {"id": 9, "embedding": [0.0, 1.0]},  # unrelated, dissimilar
        ]
        _patch_connection(monkeypatch, rows=rows)
        result = vector.find_duplicate([1.0, 0.0], threshold=0.9, days=7, exclude_id=5)
        assert result is None  # id 5 excluded, id 9 doesn't clear the threshold


class TestCompletedStageFilter:
    """E01/N06 (SOL-REVIEW3-2026-09-24): the dedup candidate pool must exclude EVERY item still
    pending the embed stage (its stored vector may be stale), not only the current LIMIT batch --
    pre-fix `load_candidate_vectors` had no stage filter at all."""

    def test_load_candidate_vectors_filters_on_completed_stage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, rows=[])
        vector.load_candidate_vectors(30, completed_stage="embed_dedup")

        query, params = conn.last_cursor.executed
        assert "%(stage)s = ANY(COALESCE(processed_stages" in query
        assert params["stage"] == "embed_dedup"

    def test_dedup_stage_passes_its_own_stage_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.pipeline import dedup

        seen: dict[str, object] = {}

        def _fake_load(days, completed_stage=None):
            seen["completed_stage"] = completed_stage
            return []

        monkeypatch.setattr(dedup, "load_candidate_vectors", _fake_load)
        monkeypatch.setattr(dedup, "get_items_for_stage", lambda *a, **k: [])
        monkeypatch.setattr(dedup, "checkpoint", lambda: None)
        dedup.run_dedup()
        assert seen["completed_stage"] == dedup.STAGE


# --------------------------------------------------------------------------
# F08 efficiency: in-memory scoring + atomic embedding/dedup_of/stage commit
# --------------------------------------------------------------------------


class TestFindDuplicateInMemory:
    def test_excludes_self_and_matches_best_candidate(self) -> None:
        candidates = [(5, [1.0, 0.0]), (9, [0.99, 0.01])]
        result = vector.find_duplicate_in_memory([1.0, 0.0], candidates, threshold=0.9, exclude_id=5)
        assert result is not None
        assert result[0] == 9

    def test_below_threshold_returns_none(self) -> None:
        candidates = [(9, [0.0, 1.0])]
        result = vector.find_duplicate_in_memory([1.0, 0.0], candidates, threshold=0.9)
        assert result is None

    def test_zero_query_vector_returns_none(self) -> None:
        candidates = [(9, [1.0, 0.0])]
        result = vector.find_duplicate_in_memory([0.0, 0.0], candidates, threshold=0.9)
        assert result is None


class _TrackingCursor(_FakeCursor):
    def __init__(self, rows: list[dict] | None, log: list[str]) -> None:
        super().__init__(rows)
        self._log = log

    def execute(self, query: str, params: dict | None = None) -> None:
        self._log.append(query)
        super().execute(query, params)


class _TrackingConnection(_FakeConnection):
    def __init__(self, rows: list[dict] | None, log: list[str]) -> None:
        super().__init__(rows)
        self._log = log
        self.cursor_calls = 0

    def cursor(self, row_factory=None) -> _TrackingCursor:
        self.cursor_calls += 1
        self.last_cursor = _TrackingCursor(self._rows, self._log)
        return self.last_cursor


class TestCommitDedupResult:
    def test_writes_embedding_dedup_of_and_stage_on_one_cursor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        executed: list[str] = []
        conn = _TrackingConnection([], executed)
        monkeypatch.setattr(vector, "connection", lambda: conn)

        vector.commit_dedup_result(5, [0.1, 0.2], dedup_of=9, stage="embed_dedup")

        # One `cursor()` call, one `with connection()` block, one commit -- the atomicity fix: a
        # crash can no longer land between the embedding write and the stage marker.
        assert conn.cursor_calls == 1
        assert len(executed) == 3
        assert any("embedding" in q for q in executed)
        assert any("dedup_of" in q for q in executed)
        assert any("processed_stages" in q for q in executed)

    def test_always_writes_dedup_of_even_when_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """N05 (SOL-REVIEW-2026-09-24 round 2): `dedup_of` is written on EVERY call, not skipped
        when this pass found no duplicate -- a re-embed (F09's quality-upgrade reprocessing) must
        be able to CLEAR a stale `dedup_of` computed against the item's old, worse content, not
        just set one. This fails against the old code, which skipped the `dedup_of` UPDATE
        entirely whenever `dedup_of is None` and so could never clear an existing link."""
        executed: list[str] = []
        conn = _TrackingConnection([], executed)
        monkeypatch.setattr(vector, "connection", lambda: conn)

        vector.commit_dedup_result(5, [0.1, 0.2], dedup_of=None, stage="embed_dedup")

        assert len(executed) == 3  # embedding + dedup_of (clearing it) + stage marker
        assert any("embedding" in q for q in executed)
        assert any("dedup_of" in q for q in executed)
        assert any("processed_stages" in q for q in executed)

    def test_clearing_dedup_of_passes_null_not_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Same fix, asserted on the actual bound parameter rather than just query presence: the
        `dedup_of` UPDATE's own param is `None` (NULL), proving it actively clears rather than
        merely re-running a no-op."""
        captured_params: list[dict | None] = []

        class _ParamCapturingCursor(_FakeCursor):
            def execute(self, query: str, params: dict | None = None) -> None:
                if "SET dedup_of" in query:
                    captured_params.append(params)
                super().execute(query, params)

        class _ParamCapturingConnection(_FakeConnection):
            def cursor(self, row_factory=None) -> _ParamCapturingCursor:
                self.last_cursor = _ParamCapturingCursor(self._rows)
                return self.last_cursor

        monkeypatch.setattr(vector, "connection", lambda: _ParamCapturingConnection([]))

        vector.commit_dedup_result(5, [0.1, 0.2], dedup_of=None, stage="embed_dedup")

        assert captured_params == [{"dedup_of": None, "item_id": 5}]
