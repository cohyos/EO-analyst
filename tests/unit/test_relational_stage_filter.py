"""Unit test for eoa.memory.relational.get_items_for_stage's F22 (docs/REVIEW_2026-09-05.md)
additive ``item_ids`` filter -- used by orchestrator.jobs's post_tenders_catchup mini-stage to
scope embed_dedup/classify/triage to a specific handful of items instead of the whole stage
backlog.

No DB: `eoa.db.connection` is monkeypatched with a fake cursor that records the executed SQL/params
and returns a canned row set.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_relational_stage_filter.py -q``
"""

from __future__ import annotations

import pytest

from eoa.memory import relational


class _FakeCursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.last_sql: str | None = None
        self.last_params: dict | None = None

    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params or {}

    def fetchall(self):
        return list(self._rows)

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


def test_get_items_for_stage_without_item_ids_passes_none(monkeypatch):
    cursor = _FakeCursor([{"id": 1}])
    monkeypatch.setattr("eoa.memory.relational.connection", lambda: _FakeConn(cursor))

    rows = relational.get_items_for_stage("classify", limit=10)

    assert rows == [{"id": 1}]
    assert "id = ANY(%(item_ids)s::bigint[])" in cursor.last_sql
    assert cursor.last_params["item_ids"] is None


def test_get_items_for_stage_with_item_ids_passes_them_through(monkeypatch):
    cursor = _FakeCursor([{"id": 7}])
    monkeypatch.setattr("eoa.memory.relational.connection", lambda: _FakeConn(cursor))

    rows = relational.get_items_for_stage("triage", limit=50, item_ids=[7, 8, 9])

    assert rows == [{"id": 7}]
    assert cursor.last_params["item_ids"] == [7, 8, 9]
    assert cursor.last_params["stage"] == "triage"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
