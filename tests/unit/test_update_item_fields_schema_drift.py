"""Unit test for eoa.memory.relational.update_item_fields's D1 round-1 fix (docs/qa/loop/
round_1_fixes.md): a DB behind ``HEAD`` (missing an additive migration's column, e.g.
``tech_maturity``/``israel_relevance``) used to crash the whole call with ``UndefinedColumn``
instead of writing the columns that do exist and skipping the ones that don't.

No DB: `eoa.db.connection` is monkeypatched with a fake cursor/connection, following the pattern
in test_relational_stage_filter.py.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_update_item_fields_schema_drift.py -q``
"""

from __future__ import annotations

from eoa.memory import relational


class _FakeCursor:
    def __init__(self, existing_columns: set[str]):
        self._existing_columns = existing_columns
        self.executed: list[tuple[str, dict | None]] = []

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params))

    def fetchall(self):
        # Only the information_schema.columns lookup calls fetchall() in this code path.
        return [{"column_name": c} for c in self._existing_columns]

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


def _patch(monkeypatch, existing_columns: set[str]) -> _FakeCursor:
    relational._items_columns_cache = None  # reset the process-lifetime cache between tests
    cursor = _FakeCursor(existing_columns)
    monkeypatch.setattr("eoa.memory.relational.connection", lambda: _FakeConn(cursor))
    return cursor


def test_missing_column_silently_dropped_others_written(monkeypatch) -> None:
    cursor = _patch(monkeypatch, existing_columns={"id", "summary_he", "so_what_he"})

    relational.update_item_fields(1, summary_he="x", tech_maturity="prototype")

    update_calls = [(sql, params) for sql, params in cursor.executed if "UPDATE items" in sql]
    assert len(update_calls) == 1
    sql, params = update_calls[0]
    assert "summary_he" in sql
    assert "tech_maturity" not in sql
    assert params == {"summary_he": "x", "item_id": 1}


def test_all_fields_missing_issues_no_update(monkeypatch) -> None:
    cursor = _patch(monkeypatch, existing_columns={"id"})

    relational.update_item_fields(1, tech_maturity="prototype", israel_relevance=0.9)

    update_calls = [sql for sql, _params in cursor.executed if "UPDATE items" in sql]
    assert update_calls == []


def test_all_fields_present_unaffected(monkeypatch) -> None:
    cursor = _patch(monkeypatch, existing_columns={"id", "summary_he", "score", "level"})

    relational.update_item_fields(1, summary_he="x", score=5, level="orange")

    update_calls = [(sql, params) for sql, params in cursor.executed if "UPDATE items" in sql]
    assert len(update_calls) == 1
    _sql, params = update_calls[0]
    assert params == {"summary_he": "x", "score": 5, "level": "orange", "item_id": 1}


def test_unknown_field_still_raises_before_any_query(monkeypatch) -> None:
    cursor = _patch(monkeypatch, existing_columns={"id"})
    import pytest

    with pytest.raises(ValueError):
        relational.update_item_fields(1, not_a_real_field="x")
    assert cursor.executed == []


def test_columns_lookup_cached_across_calls(monkeypatch) -> None:
    cursor = _patch(monkeypatch, existing_columns={"id", "summary_he"})

    relational.update_item_fields(1, summary_he="a")
    relational.update_item_fields(2, summary_he="b")

    info_schema_calls = [sql for sql, _p in cursor.executed if "information_schema.columns" in sql]
    assert len(info_schema_calls) == 1  # cached after the first lookup
