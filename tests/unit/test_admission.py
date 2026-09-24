"""Tests for eoa.orchestrator.admission (F31, SOL-REVIEW2-2026-09-24): the shared admission gate
that both `eoa.api.services.enqueue_run` (the API's "run now" button) and the scheduler
(`eoa.orchestrator.main`'s "daily"/"weekly" cron jobs, `reconcile_missed_night_run`) now go
through, so a "run now" click racing a scheduler tick can never enqueue two active
`daily_run`/`weekly_run`/`report` jobs.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_admission.py -q``
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from eoa.api import services
from eoa.orchestrator import admission


class _FakeCursor:
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


class TestAdmitRun:
    def test_no_existing_job_inserts_and_returns_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cur = _FakeCursor([None, {"id": 42}])
        monkeypatch.setattr(admission.db, "connection", lambda *_a, **_kw: _FakeConnection(cur))

        job_id = admission.admit_run(
            "daily_run", {"mode": "full"}, priority=2, equivalent_kinds=["daily_run"]
        )

        assert job_id == 42
        lock_query, lock_params = cur.executed[0]
        assert "pg_advisory_xact_lock" in lock_query
        assert lock_params["ns"] == admission.ADMISSION_LOCK_NS

    def test_existing_equivalent_job_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        existing = {"id": 5, "kind": "weekly_run", "state": "running"}
        cur = _FakeCursor([existing])
        monkeypatch.setattr(admission.db, "connection", lambda *_a, **_kw: _FakeConnection(cur))

        with pytest.raises(admission.RunAlreadyActive) as excinfo:
            admission.admit_run("daily_run", equivalent_kinds=admission.equivalent_kinds_for("daily_run"))
        assert excinfo.value.job["id"] == 5


class TestAdmitDailyAndWeeklyRun:
    def test_admit_daily_run_returns_none_instead_of_raising(self, monkeypatch: pytest.MonkeyPatch) -> None:
        existing = {"id": 5, "kind": "daily_run", "state": "running"}
        cur = _FakeCursor([existing])
        monkeypatch.setattr(admission.db, "connection", lambda *_a, **_kw: _FakeConnection(cur))

        assert admission.admit_daily_run("full", priority=2) is None

    def test_admit_weekly_run_not_blocked_by_active_daily_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The scope note in admission.py's module docstring: weekly_run must NOT be blocked just
        because a daily_run is active -- run_weekly waits on it."""
        cur = _FakeCursor([None, {"id": 9}])
        monkeypatch.setattr(admission.db, "connection", lambda *_a, **_kw: _FakeConnection(cur))

        job_id = admission.admit_weekly_run("full", priority=3)

        assert job_id == 9
        _select_query, select_params = cur.executed[2]
        assert select_params["kinds"] == ["weekly_run"]

    def test_admit_weekly_run_still_blocks_a_second_weekly_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        existing = {"id": 3, "kind": "weekly_run", "state": "queued"}
        cur = _FakeCursor([existing])
        monkeypatch.setattr(admission.db, "connection", lambda *_a, **_kw: _FakeConnection(cur))

        assert admission.admit_weekly_run("full", priority=3) is None


class _ConcurrentFakeDB:
    """A faithful in-memory stand-in for the `jobs` table plus a single Postgres advisory-lock
    namespace -- proves the actual concurrency-safety property with real threads, mirroring
    `tests/unit/test_run_now_idempotent.py`'s `_ConcurrentFakeDB`."""

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
            self._db._lock.acquire()
            self._conn._locked = True
            self._last = None
        elif "UPDATE jobs SET state = 'failed'" in query:
            kinds = {params["kind"]}
            for j in self._db._jobs:
                if j["kind"] in kinds and j["state"] == "deferred" and j.get("stale"):
                    j["state"] = "failed"
            self._last = None
        elif "SELECT * FROM jobs" in query:
            kinds = set(params["kinds"])
            match = next(
                (
                    j
                    for j in self._db._jobs
                    if j["kind"] in kinds
                    and (j["state"] in ("queued", "running") or (j["state"] == "deferred" and not j.get("stale")))
                ),
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


class TestApiAndSchedulerRaceYieldsOneJob:
    """F31 (SOL-REVIEW2-2026-09-24): the exact race the review flagged -- an API "run now" call
    (`services.enqueue_run`) and a scheduler tick (`admission.admit_daily_run`, standing in for
    the orchestrator's "daily" cron job / `reconcile_missed_night_run`) landing at the same
    instant. Both now go through `admission.admit_run` sharing one advisory lock + equivalence
    check, so exactly one `daily_run` job must result -- repeated since a race can pass by luck
    once."""

    def test_api_run_now_and_scheduler_tick_race_to_exactly_one_job(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for attempt in range(20):
            db = _ConcurrentFakeDB()
            # `services.db` and `admission.db` are both the same `eoa.db` module object (each
            # imported it via `from eoa import db`) -- patching the attribute once here is enough
            # for both call sites to see the fake.
            monkeypatch.setattr(services.db, "connection", db.connection)

            results: list[Any] = []
            errors: list[Exception] = []
            barrier = threading.Barrier(2)

            def _api_call() -> None:
                barrier.wait(timeout=5)
                try:
                    results.append(("api", services.enqueue_run("daily", "full")))
                except services.RunAlreadyActive as exc:
                    errors.append(exc)

            def _scheduler_tick() -> None:
                barrier.wait(timeout=5)
                job_id = admission.admit_daily_run("full", priority=2)
                if job_id is not None:
                    results.append(("scheduler", job_id))

            threads = [threading.Thread(target=_api_call), threading.Thread(target=_scheduler_tick)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            assert len(results) == 1, f"attempt {attempt}: expected exactly one winner, got {results!r}"
            assert len(db._jobs) == 1, f"attempt {attempt}: expected exactly one job row, got {db._jobs!r}"
            assert len(errors) == 1, f"attempt {attempt}: expected exactly one RunAlreadyActive"


class TestSaturdayWeeklyAndDailyAdmission:
    """S01 (SOL-REVIEW3-2026-09-24): on Saturday the "daily" and "weekly" crons both fire at 01:00.
    Round 3 put `weekly_run` in the daily equivalence set but checked only `weekly_run` for weekly,
    so weekly-first admission made the scheduled daily skip. Both lock orders must now yield
    exactly one daily_run and one weekly_run."""

    @pytest.mark.parametrize("order", [("weekly", "daily"), ("daily", "weekly")])
    def test_both_lock_orders_admit_one_daily_and_one_weekly(
        self, monkeypatch: pytest.MonkeyPatch, order: tuple[str, str]
    ) -> None:
        db = _ConcurrentFakeDB()
        monkeypatch.setattr(admission.db, "connection", db.connection)
        admit = {
            "daily": lambda: admission.admit_daily_run("full", 2),
            "weekly": lambda: admission.admit_weekly_run("full", 3),
        }
        ids = [admit[name]() for name in order]
        assert all(i is not None for i in ids)
        assert sorted(j["kind"] for j in db._jobs) == ["daily_run", "weekly_run"]

    def test_weekly_handler_admission_racing_the_daily_cron_yields_one_daily(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`run_weekly` admits a missing daily_run through `admit_daily_run`; racing the cron's
        own `admit_daily_run` (and a weekly cron tick) it must still produce ONE daily_run."""
        for attempt in range(20):
            db = _ConcurrentFakeDB()
            monkeypatch.setattr(admission.db, "connection", db.connection)
            barrier = threading.Barrier(3)

            def _run(fn: Any) -> None:
                barrier.wait(timeout=5)
                fn()

            threads = [
                threading.Thread(target=_run, args=(lambda: admission.admit_daily_run("full", 2),)),
                threading.Thread(target=_run, args=(lambda: admission.admit_daily_run("full", 2),)),
                threading.Thread(target=_run, args=(lambda: admission.admit_weekly_run("full", 3),)),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)
            kinds = sorted(j["kind"] for j in db._jobs)
            assert kinds == ["daily_run", "weekly_run"], f"attempt {attempt}: {kinds!r}"


class TestDeferredCountsAsActive:
    """S01: a recently `deferred` equivalent run is still pending a retry -- admitting another
    next to it would run the pipeline twice once both are claimed."""

    def test_deferred_daily_blocks_a_second_daily(self, monkeypatch: pytest.MonkeyPatch) -> None:
        db = _ConcurrentFakeDB()
        db._jobs.append({"id": 1, "kind": "daily_run", "state": "deferred"})
        db._next_id = 2
        monkeypatch.setattr(admission.db, "connection", db.connection)
        assert admission.admit_daily_run("full", 2) is None
        assert len(db._jobs) == 1

    def test_query_includes_recent_deferred_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cur = _FakeCursor([None, {"id": 1}])
        monkeypatch.setattr(admission.db, "connection", lambda *_a, **_kw: _FakeConnection(cur))
        admission.admit_run("daily_run", equivalent_kinds=["daily_run"])
        select_query, select_params = cur.executed[2]
        assert (
            "state = 'deferred' AND created_at > now() - make_interval(hours => %(deferred_hours)s)"
            in select_query
        )
        assert select_params["deferred_hours"] == admission.DEFERRED_ACTIVE_HOURS

    def test_weekly_is_not_in_the_daily_equivalence_set(self) -> None:
        assert admission.equivalent_kinds_for("daily_run") == ["daily_run"]
        assert admission.equivalent_kinds_for("weekly_run") == ["weekly_run"]


class TestStaleDeferredIsRetiredNotIgnored:
    """T01 (SOL-REVIEW4-2026-09-24): round 4 merely IGNORED a deferred row older than
    DEFERRED_ACTIVE_HOURS, but the worker still claims it once `not_before` passes -- so it could
    run after its replacement (two pipelines). Admission now retires it in the same locked
    transaction before checking."""

    def test_stale_deferred_daily_is_retired_and_replaced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        db = _ConcurrentFakeDB()
        db._jobs.append({"id": 1, "kind": "daily_run", "state": "deferred", "stale": True})
        db._next_id = 2
        monkeypatch.setattr(admission.db, "connection", db.connection)

        assert admission.admit_daily_run("full", 2) == 2
        states = {j["id"]: j["state"] for j in db._jobs}
        assert states == {1: "failed", 2: "queued"}  # the old one can no longer be claimed

    def test_retirement_sql_runs_before_the_check_under_the_lock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cur = _FakeCursor([None, {"id": 1}])
        monkeypatch.setattr(admission.db, "connection", lambda *_a, **_kw: _FakeConnection(cur))
        admission.admit_run("daily_run", equivalent_kinds=["daily_run"])
        queries = [q for q, _ in cur.executed]
        assert "pg_advisory_xact_lock" in queries[0]
        assert "UPDATE jobs SET state = 'failed'" in queries[1]
        assert "state = 'deferred' AND created_at <= now() - make_interval" in queries[1]
        assert "SELECT * FROM jobs" in queries[2]


class TestReportNeverSuppressesDaily:
    """T02 (SOL-REVIEW4-2026-09-24): an active standalone `report` must not block a daily_run
    (pre-fix, the symmetric closure did, and on Saturday weekly's admission of the missing daily
    kept failing until its wait budget ran out); a daily_run still blocks a `report`."""

    def test_active_report_does_not_block_daily(self, monkeypatch: pytest.MonkeyPatch) -> None:
        db = _ConcurrentFakeDB()
        db._jobs.append({"id": 1, "kind": "report", "state": "running"})
        db._next_id = 2
        monkeypatch.setattr(admission.db, "connection", db.connection)
        assert admission.admit_daily_run("full", 2) == 2

    def test_active_daily_still_blocks_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        db = _ConcurrentFakeDB()
        db._jobs.append({"id": 1, "kind": "daily_run", "state": "running"})
        db._next_id = 2
        monkeypatch.setattr(admission.db, "connection", db.connection)
        with pytest.raises(admission.RunAlreadyActive):
            admission.admit_run("report", equivalent_kinds=admission.equivalent_kinds_for("report"))


class TestRetirementScopedToAdmittedKind:
    """V03 (SOL-REVIEW5-2026-09-24): a standalone `report` admission must not retire an overdue
    deferred `daily_run` -- the report only builds a report, so the daily's analysis would never
    run. Only an admission of the SAME kind (its replacement) retires a stale deferred row."""

    def test_report_admission_leaves_stale_deferred_daily_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        db = _ConcurrentFakeDB()
        db._jobs.append({"id": 1, "kind": "daily_run", "state": "deferred", "stale": True})
        db._next_id = 2
        monkeypatch.setattr(admission.db, "connection", db.connection)

        admission.admit_run("report", equivalent_kinds=admission.equivalent_kinds_for("report"))

        assert db._jobs[0]["state"] == "deferred"

    def test_retirement_sql_targets_only_the_admitted_kind(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cur = _FakeCursor([None, {"id": 1}])
        monkeypatch.setattr(admission.db, "connection", lambda *_a, **_kw: _FakeConnection(cur))
        admission.admit_run("report", equivalent_kinds=["report", "daily_run"])
        query, params = cur.executed[1]
        assert "WHERE kind = %(kind)s AND state = 'deferred'" in query
        assert params["kind"] == "report"


class TestRefusedAdmissionKeepsRetirement:
    """T01 follow-up (SOL-REVIEW5-2026-09-24): `RunAlreadyActive` used to be raised INSIDE the
    `db.connection()` block, so the context manager rolled back the stale-deferred retirement."""

    def test_raise_happens_after_the_transaction_exits_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        exits: list[object] = []

        class _RecordingConnection(_FakeConnection):
            def __exit__(self, exc_type, *rest: object) -> bool:
                exits.append(exc_type)
                return False

        cur = _FakeCursor([{"id": 5, "kind": "daily_run", "state": "running"}])
        monkeypatch.setattr(admission.db, "connection", lambda *_a, **_kw: _RecordingConnection(cur))

        with pytest.raises(admission.RunAlreadyActive):
            admission.admit_run("daily_run", equivalent_kinds=["daily_run"])
        assert exits == [None]  # committed, not rolled back by an in-flight exception
