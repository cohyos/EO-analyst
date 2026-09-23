"""Tests for eoa.orchestrator.main: F07's startup night-run reconciliation and F22's
weekly-vs-daily scheduler priority (audit 2026-09-24).

No real scheduler run, no DB: `build_scheduler()` is built (not started) and its jobs' own
callables are invoked directly with `enqueue_job`/`enqueue_daily` monkeypatched to capture args
instead of touching Postgres. `reconcile_missed_night_run` is tested with a fixed clock and a
fake `db.connection`.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_orchestrator_main_schedule.py -q``
"""

from __future__ import annotations

from datetime import datetime as real_datetime
from zoneinfo import ZoneInfo

import pytest

from eoa.orchestrator import main

_TZ = ZoneInfo("Asia/Jerusalem")


class _FixedDatetime(real_datetime):
    _fixed: real_datetime

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls._fixed if tz is None else cls._fixed.astimezone(tz)


def _fixed_now(monkeypatch: pytest.MonkeyPatch, when: real_datetime) -> None:
    fixed_cls = type("_Fixed", (_FixedDatetime,), {"_fixed": when})
    monkeypatch.setattr(main, "datetime", fixed_cls)


class _FakeCursor:
    def __init__(self, *, has_row: bool) -> None:
        self.has_row = has_row
        self.executed: list[tuple[str, dict | None]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return {"exists": 1} if self.has_row else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestReconcileMissedNightRun:
    """F07 (audit 2026-09-24): a restart just after 01:00 used to skip that night's run outright
    -- APScheduler's `misfire_grace_time` only covers a fire missed while the process wasn't
    running yet, and once back up, the "daily" job's next fire time is tomorrow's 01:00."""

    def test_enqueues_when_inside_window_and_nothing_covers_tonight(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fixed_now(monkeypatch, real_datetime(2026, 3, 15, 1, 5, tzinfo=_TZ))  # 5 min after 01:00
        cur = _FakeCursor(has_row=False)
        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConn(cur))
        enqueued = []
        monkeypatch.setattr(main, "enqueue_daily", lambda mode, priority: enqueued.append((mode, priority)) or 99)

        main.reconcile_missed_night_run()

        assert enqueued == [("full", 2)]

    def test_does_nothing_when_a_daily_or_weekly_job_already_covers_tonight(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fixed_now(monkeypatch, real_datetime(2026, 3, 15, 1, 5, tzinfo=_TZ))
        cur = _FakeCursor(has_row=True)
        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConn(cur))

        def _boom(mode, priority):
            raise AssertionError("must not enqueue a second daily run when tonight is already covered")

        monkeypatch.setattr(main, "enqueue_daily", _boom)

        main.reconcile_missed_night_run()  # must not raise

    def test_does_nothing_outside_the_night_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fixed_now(monkeypatch, real_datetime(2026, 3, 15, 14, 0, tzinfo=_TZ))  # mid-afternoon

        def _boom(mode, priority):
            raise AssertionError("must not enqueue a daily run outside the night window")

        monkeypatch.setattr(main, "enqueue_daily", _boom)

        def _boom_db():
            raise AssertionError("must not even query the DB outside the night window")

        monkeypatch.setattr("eoa.db.connection", _boom_db)

        main.reconcile_missed_night_run()  # must not raise, must not query

    def test_db_failure_is_swallowed_never_enqueues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Best-effort: a DB error while checking coverage must not crash startup, and must not
        risk a duplicate run by enqueueing blind."""
        _fixed_now(monkeypatch, real_datetime(2026, 3, 15, 1, 5, tzinfo=_TZ))

        def _raise_db():
            raise RuntimeError("db unreachable")

        monkeypatch.setattr("eoa.db.connection", _raise_db)

        def _boom(mode, priority):
            raise AssertionError("must not enqueue when the coverage check itself failed")

        monkeypatch.setattr(main, "enqueue_daily", _boom)

        main.reconcile_missed_night_run()  # must not raise

    def test_window_just_before_open_does_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fixed_now(monkeypatch, real_datetime(2026, 3, 15, 0, 59, tzinfo=_TZ))  # 1 min before 01:00

        def _boom(mode, priority):
            raise AssertionError("must not enqueue before the night window opens")

        monkeypatch.setattr(main, "enqueue_daily", _boom)
        monkeypatch.setattr("eoa.db.connection", lambda: (_ for _ in ()).throw(AssertionError("no DB query")))

        main.reconcile_missed_night_run()

    def test_window_at_close_boundary_does_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fixed_now(monkeypatch, real_datetime(2026, 3, 15, 6, 0, tzinfo=_TZ))  # exactly the window end

        def _boom(mode, priority):
            raise AssertionError("must not enqueue once the night window has closed")

        monkeypatch.setattr(main, "enqueue_daily", _boom)
        monkeypatch.setattr("eoa.db.connection", lambda: (_ for _ in ()).throw(AssertionError("no DB query")))

        main.reconcile_missed_night_run()


class TestSchedulerDailyWeeklyPriority:
    """F22 (audit 2026-09-24): `weekly` gets a numerically worse (higher) scheduler priority than
    `daily` -- not equal -- so `claim_next_job`'s `ORDER BY priority ASC` picks `daily_run` first
    whenever both are queued at the same 01:00 tick, reducing how often `run_weekly`'s own
    `_daily_run_already_covered` guard is the only thing standing between a Saturday night and two
    full pipeline runs."""

    def test_weekly_priority_is_worse_than_daily(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Never `.start()`ed -- `build_scheduler()` only registers jobs; APScheduler stores the
        # callables as plain function references, so invoking `.func()` directly runs them
        # synchronously without any scheduler machinery (and an unstarted BackgroundScheduler
        # raises on `.shutdown()`, so none is needed here).
        sched = main.build_scheduler()
        daily_calls = []
        weekly_calls = []
        monkeypatch.setattr(
            main, "enqueue_daily", lambda mode, priority: daily_calls.append((mode, priority)) or 1
        )
        monkeypatch.setattr(
            main,
            "enqueue_job",
            lambda kind, payload, priority: weekly_calls.append((kind, payload, priority)) or 2,
        )

        sched.get_job("daily").func()
        sched.get_job("weekly").func()

        assert daily_calls == [("full", 2)]
        assert weekly_calls == [("weekly_run", {"mode": "full"}, 3)]
        # the actual correctness property: daily's priority number is strictly lower (= claimed
        # first by `ORDER BY priority ASC`) than weekly's.
        assert daily_calls[0][1] < weekly_calls[0][2]
