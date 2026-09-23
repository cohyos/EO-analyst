"""Tests for eoa.orchestrator.jobs._night_deadline -- F40 (audit 2026-09-24, orchestrator half).

`_night_deadline` computes the monotonic deadline for tonight's night-window end
(Asia/Jerusalem). `end` is built via `now.replace(...)`, so it shares the exact same `ZoneInfo`
tzinfo *instance* as `now` -- CPython's aware-datetime subtraction skips UTC-offset normalization
whenever both operands' `tzinfo` attributes are the same object (a documented optimization) and
falls back to a naive wall-clock subtraction instead. Across a DST transition inside the night
window this silently over/under-counts the deadline by exactly the one-hour shift.

No DB, no real clock: `datetime.now` is monkeypatched via a fixed-`now` wrapper around the real
`eoa.orchestrator.jobs.datetime` class.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_jobs_night_deadline.py -q``
"""

from __future__ import annotations

import time
from datetime import datetime as real_datetime
from zoneinfo import ZoneInfo

import pytest

from eoa.orchestrator import jobs

_TZ = ZoneInfo("Asia/Jerusalem")


class _FixedDatetime(real_datetime):
    """A `datetime` subclass whose `now(tz)` always returns a fixed instant -- swapped in for
    `eoa.orchestrator.jobs.datetime` so `_night_deadline` sees a specific pre-DST-transition
    wall-clock time without touching the real clock."""

    _fixed: real_datetime

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls._fixed if tz is None else cls._fixed.astimezone(tz)


def _fixed_now(monkeypatch: pytest.MonkeyPatch, when: real_datetime) -> None:
    fixed_cls = type("_Fixed", (_FixedDatetime,), {"_fixed": when})
    monkeypatch.setattr(jobs, "datetime", fixed_cls)


def _fake_settings(*, night_end: str = "06:00", grace_minutes: int = 5, timezone: str = "Asia/Jerusalem"):
    from types import SimpleNamespace

    return SimpleNamespace(
        timezone=timezone,
        schedule=SimpleNamespace(
            night_window=SimpleNamespace(end=night_end), deadline_grace_minutes=grace_minutes
        ),
    )


class TestNightDeadlineDST:
    def test_spring_forward_deadline_matches_real_utc_elapsed_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Israel's 2026 spring-forward transition is 2026-03-27 02:00->03:00 IST. `now` is just
        before midnight the night before; the night-window end (06:00) falls just after the
        transition. The real elapsed wall-clock-to-UTC time is 5h35m (not 6h35m -- the naive
        wall-clock subtraction the old code effectively did)."""
        now = real_datetime(2026, 3, 26, 23, 30, tzinfo=_TZ)
        monkeypatch.setattr(jobs, "settings", lambda: _fake_settings(night_end="06:05", grace_minutes=0))
        _fixed_now(monkeypatch, now)

        t0 = time.monotonic()
        deadline = jobs._night_deadline()
        elapsed_hours = (deadline - t0) / 3600

        # correct: (2026-03-27 06:05+03:00) - (2026-03-26 23:30+02:00) = 5h35m real elapsed time.
        assert elapsed_hours == pytest.approx(5 + 35 / 60, abs=0.01)
        # the pre-fix bug computed the naive wall-clock difference instead: 6h35m (one hour too
        # long) -- explicitly assert we are NOT reproducing that wrong value.
        assert elapsed_hours != pytest.approx(6 + 35 / 60, abs=0.01)

    def test_fall_back_deadline_matches_real_utc_elapsed_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Israel's 2026 fall-back transition is 2026-10-25 02:00->01:00 IST (clocks move back an
        hour) -- the mirror-image DST error: the naive wall-clock subtraction would UNDER-count
        the real elapsed time by an hour here."""
        now = real_datetime(2026, 10, 24, 23, 30, tzinfo=_TZ)
        monkeypatch.setattr(jobs, "settings", lambda: _fake_settings(night_end="06:05", grace_minutes=0))
        _fixed_now(monkeypatch, now)

        t0 = time.monotonic()
        deadline = jobs._night_deadline()
        elapsed_hours = (deadline - t0) / 3600

        end_utc = real_datetime(2026, 10, 25, 6, 5, tzinfo=_TZ).astimezone(ZoneInfo("UTC"))
        now_utc = now.astimezone(ZoneInfo("UTC"))
        expected_hours = (end_utc - now_utc).total_seconds() / 3600
        assert elapsed_hours == pytest.approx(expected_hours, abs=0.01)

    def test_no_dst_boundary_is_unaffected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Control: a night with no DST transition gives the plain wall-clock difference either
        way -- this must keep working exactly as before."""
        now = real_datetime(2026, 6, 15, 23, 0, tzinfo=_TZ)
        monkeypatch.setattr(jobs, "settings", lambda: _fake_settings(night_end="06:00", grace_minutes=5))
        _fixed_now(monkeypatch, now)

        t0 = time.monotonic()
        deadline = jobs._night_deadline()
        elapsed_hours = (deadline - t0) / 3600
        assert elapsed_hours == pytest.approx(7 + 5 / 60, abs=0.01)

    def test_grace_minutes_included(self, monkeypatch: pytest.MonkeyPatch) -> None:
        now = real_datetime(2026, 6, 15, 5, 0, tzinfo=_TZ)
        monkeypatch.setattr(jobs, "settings", lambda: _fake_settings(night_end="06:00", grace_minutes=15))
        _fixed_now(monkeypatch, now)

        t0 = time.monotonic()
        deadline = jobs._night_deadline()
        elapsed_hours = (deadline - t0) / 3600
        assert elapsed_hours == pytest.approx(1 + 15 / 60, abs=0.01)
