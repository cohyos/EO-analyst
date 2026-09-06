"""Unit tests for scripts/backfill_source_last_fetched.py (D9 round-1 fix, docs/qa/loop/
round_1_fixes.md, ``sources_recently_fetched``). No DB -- a fake cursor stands in for the join
query and the UPDATE.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_backfill_source_last_fetched.py -q``
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "backfill_source_last_fetched.py"
_spec = importlib.util.spec_from_file_location("backfill_source_last_fetched", _SCRIPT_PATH)
bslf = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(bslf)


class _FakeCursor:
    def __init__(self, candidate_rows: list[dict]):
        self._candidate_rows = candidate_rows
        self.updates: list[dict] = []

    def execute(self, query, params=None):
        if "UPDATE sources" in query:
            self.updates.append(params)

    def fetchall(self):
        return list(self._candidate_rows)

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


def test_repair_applies_update_for_each_candidate(monkeypatch):
    now = dt.datetime(2026, 9, 6, tzinfo=dt.UTC)
    rows = [{"id": 1, "name": "Source A", "last_fetched_at": None, "max_fetched_at": now}]
    cursor = _FakeCursor(rows)
    monkeypatch.setattr("eoa.db.connection", lambda: _FakeConn(cursor))

    report = bslf.repair(dry_run=False)

    assert report == [{"id": 1, "name": "Source A", "before": None, "after": now}]
    assert cursor.updates == [{"v": now, "id": 1}]


def test_dry_run_does_not_write(monkeypatch):
    now = dt.datetime(2026, 9, 6, tzinfo=dt.UTC)
    rows = [{"id": 1, "name": "Source A", "last_fetched_at": None, "max_fetched_at": now}]
    cursor = _FakeCursor(rows)
    monkeypatch.setattr("eoa.db.connection", lambda: _FakeConn(cursor))

    report = bslf.repair(dry_run=True)

    assert len(report) == 1
    assert cursor.updates == []


def test_no_candidates_is_empty(monkeypatch):
    cursor = _FakeCursor([])
    monkeypatch.setattr("eoa.db.connection", lambda: _FakeConn(cursor))

    assert bslf.repair(dry_run=False) == []
