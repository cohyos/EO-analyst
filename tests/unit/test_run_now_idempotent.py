"""Tests for U4/F17 (docs/REVIEW_2026-09-05.md): idempotent "run now" + `/api/runs/current`.

Repro: the "הרץ עכשיו" button gave no feedback, so the analyst clicked it twice, enqueueing two
overlapping `daily_run` jobs; separately, a `deep_search` job (#70) ran to completion without ever
showing up anywhere in the UI. Fixed by `services.enqueue_run` refusing a second run while an
equivalent one is queued/running (raising `RunAlreadyActive`, mapped to HTTP 409 by the route) and
`services.current_run_progress` exposing the active run's stage progress plus any other
concurrently-running job.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_run_now_idempotent.py -q``
"""

from __future__ import annotations

from typing import Any

import pytest

from eoa.api import services


class _FakeCursor:
    """F31 (audit 2026-09-24): `enqueue_run` now does its equivalent-job check and its insert
    inside ONE `db.connection()` transaction (an advisory-lock SELECT, an equivalent-job SELECT,
    and -- only if nothing existing was found -- an INSERT ... RETURNING id), instead of the old
    two separate `_fetchone`/`relational.enqueue_job` round trips. This fake mirrors
    `test_jobs_leases.py`'s FakeCursor/FakeConnection style: `fetchone_results` is consumed in the
    order `.fetchone()` is actually called (only the equivalent-job SELECT and the INSERT call it;
    the advisory-lock SELECT does not)."""

    def __init__(self, fetchone_results: list[Any]) -> None:
        self.executed: list[tuple[str, Any]] = []
        self._results = list(fetchone_results)

    def execute(self, query: str, params: Any = None) -> _FakeCursor:
        self.executed.append((query, params))
        return self

    def fetchone(self) -> Any:
        return self._results.pop(0) if self._results else None

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeConnection:
    def __init__(self, cur: _FakeCursor) -> None:
        self._cur = cur

    def cursor(self, row_factory: Any = None) -> _FakeCursor:
        return self._cur

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class TestEnqueueRunIdempotent:
    def test_second_daily_run_while_one_is_running_raises_409_equivalent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        existing_job = {"id": 5, "kind": "daily_run", "state": "running"}
        cur = _FakeCursor([existing_job])
        monkeypatch.setattr(services.db, "connection", lambda *_a, **_kw: _FakeConnection(cur))

        with pytest.raises(services.RunAlreadyActive) as excinfo:
            services.enqueue_run("daily", "full")
        assert excinfo.value.job["id"] == 5
        assert excinfo.value.job["state"] == "running"
        # only the advisory lock + the equivalent-job SELECT ran -- no INSERT was attempted.
        assert len(cur.executed) == 2

    def test_daily_run_blocked_while_weekly_run_covers_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A weekly_run performs the full daily pipeline first -- it counts as "already running"
        for a plain daily-run request too."""
        existing_job = {"id": 9, "kind": "weekly_run", "state": "running"}
        cur = _FakeCursor([existing_job])
        monkeypatch.setattr(services.db, "connection", lambda *_a, **_kw: _FakeConnection(cur))

        with pytest.raises(services.RunAlreadyActive):
            services.enqueue_run("daily", "full")
        _select_query, select_params = cur.executed[1]
        assert "weekly_run" in select_params["kinds"]
        assert "daily_run" in select_params["kinds"]

    def test_no_existing_run_enqueues_normally(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cur = _FakeCursor([None, {"id": 123}])
        monkeypatch.setattr(services.db, "connection", lambda *_a, **_kw: _FakeConnection(cur))

        job_id = services.enqueue_run("daily", "full")
        assert job_id == 123
        # advisory lock, equivalent-job SELECT, INSERT -- all three in the one transaction.
        assert len(cur.executed) == 3
        insert_query, insert_params = cur.executed[2]
        assert "INSERT INTO jobs" in insert_query
        assert insert_params["kind"] == "daily_run"

    def test_unrelated_kind_running_does_not_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An `ingest`-scope request isn't blocked by a running `daily_run` -- they're not in the
        same idempotency group."""
        cur = _FakeCursor([None, {"id": 7}])
        monkeypatch.setattr(services.db, "connection", lambda *_a, **_kw: _FakeConnection(cur))

        assert services.enqueue_run("ingest", "full") == 7
        _select_query, select_params = cur.executed[1]
        assert select_params["kinds"] == ["ingest"]

    def test_concurrent_calls_serialize_through_the_advisory_lock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """F31: the first statement of every call is the `pg_advisory_xact_lock` -- this is what
        actually prevents two concurrent callers from both passing the equivalent-job SELECT
        before either INSERT commits (a real Postgres connection blocks the second caller's lock
        acquisition until the first transaction commits/rolls back; this fake just asserts the
        lock statement precedes the SELECT/INSERT in program order for a single call)."""
        cur = _FakeCursor([None, {"id": 1}])
        monkeypatch.setattr(services.db, "connection", lambda *_a, **_kw: _FakeConnection(cur))

        services.enqueue_run("daily", "full")
        lock_query, lock_params = cur.executed[0]
        assert "pg_advisory_xact_lock" in lock_query
        assert lock_params["ns"] == services._ENQUEUE_RUN_LOCK_NS
        select_query, _ = cur.executed[1]
        assert "SELECT * FROM jobs" in select_query


class TestCurrentRunProgress:
    def test_no_active_run_returns_none_current(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "_fetchone", lambda *_a, **_kw: None)
        monkeypatch.setattr(services, "_fetchall", lambda *_a, **_kw: [])

        result = services.current_run_progress()
        assert result == {"current": None, "other_running": []}

    def test_running_daily_run_reports_stage_and_eta(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import datetime as dt

        started = dt.datetime.now(tz=dt.UTC) - dt.timedelta(minutes=12)
        primary_job = {"id": 1, "kind": "daily_run", "state": "running", "started_at": started}
        log_rows = [
            {"stage": "ingest", "event": "start", "detail": {}, "heartbeat_at": None},
            {"stage": "ingest", "event": "done", "detail": {"minutes": 5.0}, "heartbeat_at": None},
            {"stage": "embed_dedup", "event": "start", "detail": {}, "heartbeat_at": None},
        ]

        def fetchone(query: str, params: Any = None) -> Any:
            if "kind = ANY" in query and "running', 'queued'" in query:
                return primary_job
            raise AssertionError(query)

        def fetchall(query: str, params: Any = None) -> Any:
            if "FROM run_log WHERE job_id" in query:
                return log_rows
            if "detail->>'minutes'" in query:
                return []  # no history yet -- falls back to config budget
            if "state = 'running' AND id !=" in query:
                return []
            raise AssertionError(query)

        monkeypatch.setattr(services, "_fetchone", fetchone)
        monkeypatch.setattr(services, "_fetchall", fetchall)

        result = services.current_run_progress()
        current = result["current"]
        assert current is not None
        assert current["job_id"] == 1
        assert current["current_stage"] == "embed_dedup"
        assert current["elapsed_min"] == pytest.approx(12.0, abs=0.5)
        assert current["eta_min"] is not None and current["eta_min"] > 0
        ingest_entry = next(s for s in current["stages"] if s["stage"] == "ingest")
        assert ingest_entry["status"] == "done"
        assert ingest_entry["minutes"] == 5.0

    def test_other_running_job_listed_separately(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """F17: a deep_search job running concurrently with (or without) a primary run must be
        surfaced, not invisible."""
        other_job = {"id": 70, "kind": "deep_search", "started_at": None}

        def fetchone(query: str, params: Any = None) -> Any:
            return None  # no primary run active

        def fetchall(query: str, params: Any = None) -> Any:
            if "state = 'running' AND id !=" in query:
                assert params["exclude"] == -1
                return [other_job]
            raise AssertionError(query)

        monkeypatch.setattr(services, "_fetchone", fetchone)
        monkeypatch.setattr(services, "_fetchall", fetchall)

        result = services.current_run_progress()
        assert result["current"] is None
        assert result["other_running"] == [{"job_id": 70, "kind": "deep_search", "started_at": None}]
