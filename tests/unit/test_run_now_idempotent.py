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

import threading
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

    def test_daily_run_not_blocked_by_weekly_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """S01 (SOL-REVIEW3-2026-09-24): a weekly_run no longer runs the daily pipeline -- it waits
        for a daily_run -- so it must not be in a daily request's equivalence set."""
        cur = _FakeCursor([None, {"id": 124}])
        monkeypatch.setattr(services.db, "connection", lambda *_a, **_kw: _FakeConnection(cur))

        assert services.enqueue_run("daily", "full") == 124
        _select_query, select_params = cur.executed[1]
        assert "weekly_run" not in select_params["kinds"]
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


class TestEquivalentKindsSymmetric:
    """F31 follow-up: `_equivalent_kinds_for` closes `_RUN_IDEMPOTENCY_GROUPS` into a symmetric
    relation (`"report"`'s entry lists `daily_run`, `daily_run` has no entry of its own). S01
    (SOL-REVIEW3-2026-09-24): `weekly_run` is its own singleton group -- it waits on a daily_run
    instead of competing with it."""

    def test_daily_run_group_includes_report(self) -> None:
        assert set(services._equivalent_kinds_for("daily_run")) == {"daily_run", "report"}

    def test_weekly_run_is_its_own_group(self) -> None:
        assert services._equivalent_kinds_for("weekly_run") == ["weekly_run"]

    def test_report_group(self) -> None:
        assert set(services._equivalent_kinds_for("report")) == {"daily_run", "report"}

    def test_unrelated_kind_is_its_own_singleton_group(self) -> None:
        assert services._equivalent_kinds_for("ingest") == ["ingest"]


class _ConcurrentFakeDB:
    """A minimal, faithful in-memory stand-in for the `jobs` table plus a single Postgres
    advisory-lock namespace -- enough to prove `enqueue_run`'s actual concurrency-safety property
    end to end with REAL threads (not just asserting SQL statement order for one call, as the
    `_FakeCursor` tests above do). Used instead of the real dev DB because the shared dev database
    already has a genuine `daily_run` job running tonight (see task brief: do not restart/interfere
    with the live app) -- both scopes would immediately see it as already-active and neither would
    ever reach the INSERT, which would prove nothing about concurrency safety."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: list[dict[str, Any]] = []
        self._next_id = 1

    def connection(self, *_a: Any, **_kw: Any) -> _ConcurrentFakeConnection:
        return _ConcurrentFakeConnection(self)


class _ConcurrentFakeConnection:
    def __init__(self, db: _ConcurrentFakeDB) -> None:
        self._db = db
        self._locked = False

    def cursor(self, row_factory: Any = None) -> _ConcurrentFakeCursor:
        return _ConcurrentFakeCursor(self._db, self)

    def __enter__(self) -> _ConcurrentFakeConnection:
        return self

    def __exit__(self, *exc: object) -> bool:
        if self._locked:
            self._db._lock.release()
            self._locked = False
        return False


class _ConcurrentFakeCursor:
    def __init__(self, db: _ConcurrentFakeDB, conn: _ConcurrentFakeConnection) -> None:
        self._db = db
        self._conn = conn
        self._last: Any = None

    def execute(self, query: str, params: Any = None) -> _ConcurrentFakeCursor:
        if "pg_advisory_xact_lock" in query:
            # A single fixed key (this fake only ever locks the one shared `threading.Lock`) --
            # faithfully models the FIXED code's single advisory lock. The old, per-group-keyed
            # lock would need two DIFFERENT `threading.Lock`s here to reproduce; this fake
            # intentionally cannot represent that regression (see the module-level docstring on
            # why a real-DB test isn't safe here) -- `TestEquivalentKindsSymmetric` above and the
            # per-call `_FakeCursor` tests cover the lock-key and SQL-shape regressions instead.
            self._db._lock.acquire()
            self._conn._locked = True
            self._last = None
        elif "SELECT * FROM jobs" in query:
            kinds = set(params["kinds"])
            match = next(
                (j for j in self._db._jobs if j["kind"] in kinds and j["state"] in ("queued", "running")),
                None,
            )
            self._last = match
        elif "INSERT INTO jobs" in query:
            job = {"id": self._db._next_id, "kind": params["kind"], "state": "queued"}
            self._db._next_id += 1
            self._db._jobs.append(job)
            self._last = {"id": job["id"]}
        else:
            raise AssertionError(f"unexpected query in fake: {query}")
        return self

    def fetchone(self) -> Any:
        return self._last

    def __enter__(self) -> _ConcurrentFakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class TestEnqueueRunConcurrencyFaithfulFake:
    """F31 (SOL-REVIEW-2026-09-24): concurrent `report` + `daily` requests, run as real threads
    against the faithful in-memory fake above, must yield exactly one active job -- state
    transition: two concurrent callers in, one `RunAlreadyActive` + one committed job out. Repeated
    several times since a race is exactly the kind of thing that can pass by luck once."""

    def test_concurrent_report_and_daily_yields_exactly_one_job(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for attempt in range(20):
            db = _ConcurrentFakeDB()
            monkeypatch.setattr(services.db, "connection", db.connection)

            results: list[tuple[str, int]] = []
            errors: list[tuple[str, Exception]] = []
            barrier = threading.Barrier(2)

            def _go(scope: str) -> None:
                barrier.wait(timeout=5)
                try:
                    job_id = services.enqueue_run(scope, "full")
                    results.append((scope, job_id))
                except services.RunAlreadyActive as exc:
                    errors.append((scope, exc))

            threads = [threading.Thread(target=_go, args=(s,)) for s in ("report", "daily")]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            assert len(results) == 1, f"attempt {attempt}: expected exactly one success, got {results!r}"
            assert len(errors) == 1, f"attempt {attempt}: expected exactly one RunAlreadyActive"
            assert len(db._jobs) == 1


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
