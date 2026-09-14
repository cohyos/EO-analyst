"""Tests for eoa.memory.relational's job-lease additions (codex review #15):
`claim_next_job`'s deferred-pickup + lease stamping, `finish_job`'s `not_before` param and
worker-ownership warning, and `reap_stale_jobs`'s SQL shape.

No real Postgres: `eoa.memory.relational.connection` is monkeypatched to a fake
connection/cursor pair that records every executed query + params (mirroring
`tests/unit/test_obsidian_export.py`'s "no DB" stubbing style, but exercising the actual SQL
string this module builds rather than mocking the query builder away).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_jobs_leases.py -q``
"""

from __future__ import annotations

import datetime as dt

from eoa.memory.relational import claim_next_job, finish_job, heartbeat, reap_stale_jobs


class FakeCursor:
    def __init__(self, fetchone_result=None, fetchall_result=None):
        self.executed: list[tuple[str, dict | None]] = []
        self._fetchone_result = fetchone_result
        self._fetchall_result = [] if fetchall_result is None else fetchall_result

    def execute(self, query, params=None):
        self.executed.append((query, params))
        return self

    def fetchone(self):
        return self._fetchone_result

    def fetchall(self):
        return self._fetchall_result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeLog:
    """Stand-in for the module's structlog logger, recording calls instead of emitting."""

    def __init__(self):
        self.warnings: list[tuple[str, dict]] = []
        self.infos: list[tuple[str, dict]] = []

    def warning(self, event, **kw):
        self.warnings.append((event, kw))

    def info(self, event, **kw):
        self.infos.append((event, kw))

    def debug(self, event, **kw):
        pass

    def error(self, event, **kw):
        pass


class TestClaimNextJob:
    def test_heartbeat_uses_real_logger_without_event_argument_collision(self, monkeypatch):
        cur = FakeCursor()
        monkeypatch.setattr("eoa.memory.relational.connection", lambda: FakeConnection(cur))
        heartbeat(5, "tenders", "progress")
        assert len(cur.executed) == 2

    def test_query_selects_queued_and_deferred_and_stamps_lease(self, monkeypatch) -> None:
        cur = FakeCursor(fetchone_result={"id": 5, "kind": "deep_search"})
        conn = FakeConnection(cur)
        monkeypatch.setattr("eoa.memory.relational.connection", lambda: conn)

        row = claim_next_job(["deep_search"], worker_id="host-A:111", lease_seconds=60)

        assert row == {"id": 5, "kind": "deep_search"}
        query, params = cur.executed[0]
        assert "state IN ('queued', 'deferred')" in query
        assert "worker_id" in query
        assert "lease_expires_at" in query
        assert params["worker_id"] == "host-A:111"
        assert params["lease_seconds"] == 60
        assert params["kinds"] == ["deep_search"]

    def test_returns_none_when_nothing_claimed(self, monkeypatch) -> None:
        cur = FakeCursor(fetchone_result=None)
        conn = FakeConnection(cur)
        monkeypatch.setattr("eoa.memory.relational.connection", lambda: conn)

        assert claim_next_job(["ingest"]) is None

    def test_default_lease_seconds_is_900(self, monkeypatch) -> None:
        cur = FakeCursor(fetchone_result=None)
        conn = FakeConnection(cur)
        monkeypatch.setattr("eoa.memory.relational.connection", lambda: conn)

        claim_next_job(["ingest"], worker_id="w1")

        _, params = cur.executed[0]
        assert params["lease_seconds"] == 900


class TestFinishJobNotBefore:
    def test_passes_not_before_through_to_query_params(self, monkeypatch) -> None:
        cur = FakeCursor(fetchone_result={"prior_worker_id": None})
        conn = FakeConnection(cur)
        monkeypatch.setattr("eoa.memory.relational.connection", lambda: conn)

        nb = dt.datetime(2026, 9, 4, 12, 0, tzinfo=dt.UTC)
        finish_job(7, "deferred", error="gpu busy", not_before=nb)

        query, params = cur.executed[0]
        assert params["not_before"] == nb
        assert params["state"] == "deferred"
        assert params["job_id"] == 7
        assert params["error"] == "gpu busy"
        assert "state IN ('running', 'deferred', 'queued')" in query

    def test_not_before_defaults_to_none_and_preserves_existing_via_coalesce(self, monkeypatch) -> None:
        cur = FakeCursor(fetchone_result={"prior_worker_id": None})
        conn = FakeConnection(cur)
        monkeypatch.setattr("eoa.memory.relational.connection", lambda: conn)

        finish_job(7, "done")

        query, params = cur.executed[0]
        assert params["not_before"] is None
        assert "COALESCE(%(not_before)s, not_before)" in query


class TestFinishJobLeaseOwnership:
    def test_rejects_mismatched_owner_in_update(self, monkeypatch) -> None:
        cur = FakeCursor(fetchone_result=None)
        conn = FakeConnection(cur)
        fake_log = FakeLog()
        monkeypatch.setattr("eoa.memory.relational.connection", lambda: conn)
        monkeypatch.setattr("eoa.memory.relational.log", fake_log)

        finish_job(7, "done", worker_id="host-B:222")

        query, params = cur.executed[0]
        assert "worker_id = %(worker_id)s" in query
        assert params["worker_id"] == "host-B:222"
        assert any(evt == "job.finish_no_matching_row" for evt, _ in fake_log.warnings)
        assert not any(evt == "job.finished" for evt, _ in fake_log.infos)

    def test_no_warning_when_worker_id_matches_lease_holder(self, monkeypatch) -> None:
        cur = FakeCursor(fetchone_result={"prior_worker_id": "host-A:111"})
        conn = FakeConnection(cur)
        fake_log = FakeLog()
        monkeypatch.setattr("eoa.memory.relational.connection", lambda: conn)
        monkeypatch.setattr("eoa.memory.relational.log", fake_log)

        finish_job(7, "done", worker_id="host-A:111")

        assert not any(evt == "job.finish_worker_mismatch" for evt, _ in fake_log.warnings)

    def test_no_warning_when_worker_id_not_given(self, monkeypatch) -> None:
        cur = FakeCursor(fetchone_result={"prior_worker_id": "host-A:111"})
        conn = FakeConnection(cur)
        fake_log = FakeLog()
        monkeypatch.setattr("eoa.memory.relational.connection", lambda: conn)
        monkeypatch.setattr("eoa.memory.relational.log", fake_log)

        finish_job(7, "done")  # no worker_id -> best-effort check simply skipped

        assert not any(evt == "job.finish_worker_mismatch" for evt, _ in fake_log.warnings)

    def test_warns_when_no_matching_row(self, monkeypatch) -> None:
        """A job already reaped/finished by someone else: the UPDATE matches no row."""
        cur = FakeCursor(fetchone_result=None)
        conn = FakeConnection(cur)
        fake_log = FakeLog()
        monkeypatch.setattr("eoa.memory.relational.connection", lambda: conn)
        monkeypatch.setattr("eoa.memory.relational.log", fake_log)

        finish_job(7, "done")

        assert any(evt == "job.finish_no_matching_row" for evt, _ in fake_log.warnings)


class TestReapStaleJobs:
    def test_query_marks_running_jobs_with_expired_lease_as_failed(self, monkeypatch) -> None:
        cur = FakeCursor(fetchall_result=[{"id": 1}, {"id": 2}])
        conn = FakeConnection(cur)
        monkeypatch.setattr("eoa.memory.relational.connection", lambda: conn)

        count = reap_stale_jobs(max_age_hours=6)

        assert count == 2
        query, params = cur.executed[0]
        assert "state = 'running'" in query
        assert "state = 'failed'" in query
        assert "stale lease" in query
        assert "lease_expires_at" in query
        assert params == {"max_age_hours": 6}

    def test_returns_zero_when_nothing_is_stale(self, monkeypatch) -> None:
        cur = FakeCursor(fetchall_result=[])
        conn = FakeConnection(cur)
        monkeypatch.setattr("eoa.memory.relational.connection", lambda: conn)

        assert reap_stale_jobs() == 0

    def test_default_max_age_hours_is_six(self, monkeypatch) -> None:
        cur = FakeCursor(fetchall_result=[])
        conn = FakeConnection(cur)
        monkeypatch.setattr("eoa.memory.relational.connection", lambda: conn)

        reap_stale_jobs()

        _, params = cur.executed[0]
        assert params["max_age_hours"] == 6

    def test_logs_warning_with_reaped_count_when_rows_found(self, monkeypatch) -> None:
        cur = FakeCursor(fetchall_result=[{"id": 9}])
        conn = FakeConnection(cur)
        fake_log = FakeLog()
        monkeypatch.setattr("eoa.memory.relational.connection", lambda: conn)
        monkeypatch.setattr("eoa.memory.relational.log", fake_log)

        reap_stale_jobs()

        assert any(evt == "jobs.reaped_stale" for evt, _ in fake_log.warnings)
