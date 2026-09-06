"""Tests for the Morning KPI/timeline fix (F12, docs/REVIEW_2026-09-05.md).

Repro: "פריטים שנקלטו 5" while 50 items came in that day, "מכרזים ו-RFI/RFP 0" while there were
open/unknown tenders, and the replay timeline showed "2" for every stage regardless of what that
stage actually did. Root cause: `_night_summary()` scoped every count to the last completed
`daily_run` job's own `[started_at, finished_at]` window instead of a rolling last-24h view of the
DB, and `_last_run()`'s per-stage info was a raw count of `run_log` heartbeat rows (almost always
exactly 2: one `start` + one `done`) instead of that stage's actual outcome/duration.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_morning_kpis.py -q``
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from eoa.api import services


def _route(fixtures: dict[str, Any], default: Any = None):
    """Dispatches `_fetchone`/`_fetchall` calls to a fixture keyed by a distinctive SQL substring."""

    def _fetchone(query: str, params: Any = None) -> Any:
        for needle, value in fixtures.items():
            if needle in query:
                return value(query, params) if callable(value) else value
        return default

    return _fetchone


class TestNightSummary24hWindow:
    """F12: KPI counts must reflect the last 24h of DB state, not the last job's own runtime."""

    def test_items_ingested_counts_full_24h_not_job_runtime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        now = dt.datetime.now(tz=dt.UTC)
        last_job = {
            "id": 1,
            "kind": "daily_run",
            "state": "done",
            # The job itself only ran for 10 minutes...
            "started_at": now - dt.timedelta(hours=5, minutes=10),
            "finished_at": now - dt.timedelta(hours=5),
        }

        def fetchone(query: str, params: Any = None) -> Any:
            if "FROM jobs WHERE kind = 'daily_run'" in query:
                return last_job
            if "FROM items" in query:
                # ...but 50 items came in across the full 24h window this call receives.
                assert params is not None
                start, end = params["start"], params["end"]
                assert (end - start) >= dt.timedelta(hours=23, minutes=59)
                return {"n": 50}
            if "FROM jobs WHERE kind = 'deep_search'" in query:
                return {"n": 2}
            if "FROM run_log WHERE event" in query:
                return {"n": 0}
            if "FROM tenders WHERE status = 'open'" in query:
                return {"n": 6}
            if "FROM tenders WHERE status = 'unknown'" in query:
                return {"n": 1}
            if "FROM tender_forecasts" in query:
                return {"n": 3}
            raise AssertionError(f"unexpected query: {query}")

        monkeypatch.setattr(services, "_fetchone", fetchone)

        summary = services._night_summary()
        assert summary is not None
        assert summary["items_ingested"] == 50
        assert summary["deep_searches"] == 2
        assert summary["duration_min"] == 10.0
        assert summary["state"] == "done"
        assert summary["tenders_open"] == 6
        assert summary["tenders_unknown"] == 1
        assert summary["new_forecasts"] == 3

    def test_no_completed_run_yet_still_reports_24h_counts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fetchone(query: str, params: Any = None) -> Any:
            if "FROM jobs WHERE kind = 'daily_run'" in query:
                return None
            if "FROM items" in query:
                return {"n": 12}
            if "FROM jobs WHERE kind = 'deep_search'" in query:
                return {"n": 0}
            if "FROM run_log WHERE event" in query:
                return {"n": 1}
            if "FROM tenders WHERE status" in query:
                return {"n": 0}
            if "FROM tender_forecasts" in query:
                return {"n": 0}
            raise AssertionError(f"unexpected query: {query}")

        monkeypatch.setattr(services, "_fetchone", fetchone)

        summary = services._night_summary()
        assert summary is not None
        # Never null -- the UI must always render a KPI row.
        assert summary["items_ingested"] == 12
        assert summary["duration_min"] is None
        assert summary["state"] == "none"
        assert summary["errors"] == 1

    def test_classified_excludes_archived_and_unclassified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen_where: list[str] = []

        def fetchone(query: str, params: Any = None) -> Any:
            if "FROM jobs WHERE kind = 'daily_run'" in query:
                return None
            if "FROM items" in query:
                seen_where.append(query)
                return {"n": 3}
            if "FROM jobs WHERE kind = 'deep_search'" in query:
                return {"n": 0}
            if "FROM run_log WHERE event" in query:
                return {"n": 0}
            if "FROM tenders WHERE status" in query:
                return {"n": 0}
            if "FROM tender_forecasts" in query:
                return {"n": 0}
            raise AssertionError(f"unexpected query: {query}")

        monkeypatch.setattr(services, "_fetchone", fetchone)
        services._night_summary()

        classify_queries = [q for q in seen_where if "classify" in q]
        assert classify_queries, "expected a classified-count query"
        assert "archive" in classify_queries[0]
        assert "unclassified" in classify_queries[0]


class TestRecentErrors:
    """U2: backs the Morning "שגיאות אחרונות" drawer."""

    def test_extracts_message_from_detail_and_skips_traceback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {
                "id": 5,
                "job_id": 1,
                "stage": "classify",
                "event": "error",
                "detail": {"error": "LLMOutputError: bad json", "trace": "a" * 2000},
                "at": dt.datetime.now(tz=dt.UTC),
            }
        ]
        monkeypatch.setattr(services, "_fetchall", lambda *_a, **_kw: rows)

        errors = services.recent_errors()
        assert len(errors) == 1
        assert errors[0]["stage"] == "classify"
        assert errors[0]["message"] == "LLMOutputError: bad json"
        assert "trace" not in errors[0]

    def test_no_errors_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "_fetchall", lambda *_a, **_kw: [])
        assert services.recent_errors() == []


class TestLastRunTimeline:
    """F12: per-stage timeline entries carry a real status/duration, not a heartbeat-row count."""

    def test_stage_status_and_minutes_from_terminal_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        job_row = {
            "id": 42,
            "kind": "daily_run",
            "state": "partial",
            "started_at": dt.datetime.now(tz=dt.UTC) - dt.timedelta(minutes=30),
            "finished_at": dt.datetime.now(tz=dt.UTC),
        }
        log_rows = [
            {"stage": "ingest", "event": "start", "detail": {"stage": "ingest"}, "heartbeat_at": None},
            {
                "stage": "ingest",
                "event": "done",
                "detail": {"fetched": 50, "minutes": 4.2},
                "heartbeat_at": None,
            },
            {"stage": "classify", "event": "start", "detail": {}, "heartbeat_at": None},
            {
                "stage": "classify",
                "event": "error",
                "detail": {"error": "boom", "minutes": 1.1},
                "heartbeat_at": None,
            },
            {"stage": "deep_search", "event": "skipped_no_time", "detail": {}, "heartbeat_at": None},
            # "triage" never runs at all this time -- should show up as "pending".
        ]

        def fetchone(query: str, params: Any = None) -> Any:
            if "FROM jobs WHERE kind IN" in query:
                return job_row
            raise AssertionError(f"unexpected query: {query}")

        def fetchall(query: str, params: Any = None) -> Any:
            if "FROM run_log WHERE job_id" in query:
                return log_rows
            raise AssertionError(f"unexpected query: {query}")

        monkeypatch.setattr(services, "_fetchone", fetchone)
        monkeypatch.setattr(services, "_fetchall", fetchall)

        last_run = services._last_run()
        assert last_run is not None
        stages = last_run["stages"]

        assert stages["ingest"]["status"] == "done"
        assert stages["ingest"]["minutes"] == 4.2

        assert stages["classify"]["status"] == "failed"
        assert stages["classify"]["minutes"] == 1.1

        assert stages["deep_search"]["status"] == "skipped"

        # Never reached -- must not silently disappear, and must not look "done".
        assert stages["triage"]["status"] == "pending"
        assert stages["triage"]["minutes"] is None

        # Old behaviour asserted a plain event-row `events` counter (near-always 2); that field is
        # gone from the new shape entirely.
        assert "events" not in stages["ingest"]

    def test_running_job_stage_without_terminal_event_reports_running(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log_rows = [
            {"stage": "ingest", "event": "start", "detail": {"stage": "ingest"}, "heartbeat_at": None},
        ]
        monkeypatch.setattr(services, "_fetchall", lambda *_a, **_kw: log_rows)
        result = services._stage_timeline_from_log(1, "running")
        assert result["ingest"]["status"] == "running"

        # But once the job itself is no longer running, a stage stuck mid-flight reads as done
        # rather than forever "running" (the job ended one way or another).
        result2 = services._stage_timeline_from_log(1, "failed")
        assert result2["ingest"]["status"] == "done"
