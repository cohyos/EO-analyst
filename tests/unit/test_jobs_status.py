"""Tests for eoa.orchestrator.jobs: run_daily's terminal status computation and _notify's
failure-path when no report was produced (codex review #16).

No DB, no Ollama: `eoa.db.connection` is monkeypatched to fail fast wherever `_notify` might
reach it (it already tolerates that — headlines just come back empty), mirroring
`tests/unit/test_obsidian_export.py`'s stubbing style. `eoa.notify.ntfy` calls are monkeypatched
at the `eoa.orchestrator.jobs` module level (this module's own import of `ntfy`).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_jobs_status.py -q``
"""

from __future__ import annotations

import types
from datetime import UTC, datetime, timedelta

import pytest

from eoa.errors import ResourceUnavailable
from eoa.orchestrator import jobs
from eoa.orchestrator.jobs import RunState, _compute_run_status, _notify, _terminal_state, run_weekly


def _raise_no_db():
    raise RuntimeError("no db in unit tests")


class TestComputeRunStatus:
    """#16: run_daily's terminal done/partial/failed status from per-stage outcomes."""

    def test_done_when_report_ok_and_no_problems(self) -> None:
        stats = {
            "ingest": {"minutes": 1.0},
            "classify": {"minutes": 0.5, "n": 10},
            "report": {"docx": "x.docx", "minutes": 2.0},
        }
        assert _compute_run_status(stats) == "done"

    def test_failed_when_report_stage_missing(self) -> None:
        stats = {"ingest": {"minutes": 1.0}}
        assert _compute_run_status(stats) == "failed"

    def test_failed_when_report_errored(self) -> None:
        stats = {"report": {"error": "boom"}}
        assert _compute_run_status(stats) == "failed"

    def test_failed_when_report_deferred(self) -> None:
        stats = {"report": {"deferred": "gpu busy"}}
        assert _compute_run_status(stats) == "failed"

    def test_failed_when_report_skipped(self) -> None:
        stats = {"report": {"skipped": "no_time"}}
        assert _compute_run_status(stats) == "failed"

    def test_partial_when_report_ok_but_other_stage_errored(self) -> None:
        stats = {"report": {"docx": "x.docx"}, "deep_search": {"error": "boom"}}
        assert _compute_run_status(stats) == "partial"

    def test_partial_when_report_ok_but_other_stage_deferred(self) -> None:
        stats = {"report": {"docx": "x.docx"}, "deep_search": {"deferred": "gpu busy"}}
        assert _compute_run_status(stats) == "partial"

    def test_partial_when_other_stage_skipped(self) -> None:
        stats = {"report": {"docx": "x.docx"}, "analyze": {"skipped": "no_time"}}
        assert _compute_run_status(stats) == "partial"

    def test_partial_when_other_stage_partial(self) -> None:
        stats = {"report": {"docx": "x.docx"}, "classify": {"partial": "deadline"}}
        assert _compute_run_status(stats) == "partial"

    def test_non_dict_stats_values_are_ignored(self) -> None:
        # e.g. rs.stats["total_minutes"] is a float, not a per-stage dict
        stats = {"report": {"docx": "x.docx"}, "total_minutes": 12.3}
        assert _compute_run_status(stats) == "done"

    def test_export_backup_error_makes_it_partial_not_done(self) -> None:
        stats = {"report": {"docx": "x.docx"}, "export_backup": {"backup_error": "disk full"}}
        assert _compute_run_status(stats) == "partial"


class TestTerminalState:
    """F23 (audit 2026-09-24): a handler result's `*_error` field must never be silently
    defaulted to `done` -- whether the handler set no `status` at all (monthly/dossier/patent-
    survey/single-target bd/product-line builders) or set one that only covers PART of the
    result (run_weekly's own daily-pipeline `status` plus a separate `weekly_report_error`)."""

    def test_recognized_status_wins_when_nothing_else_is_wrong(self) -> None:
        assert _terminal_state({"status": "done", "report": {"docx": "x"}}) == "done"
        assert _terminal_state({"status": "partial"}) == "partial"
        assert _terminal_state({"status": "failed"}) == "failed"

    def test_no_status_and_no_problem_defaults_to_done(self) -> None:
        assert _terminal_state({"monthly_report": {"report_id": 5, "qa_passed": True}}) == "done"

    def test_lone_error_with_no_status_and_nothing_else_built_is_failed(self) -> None:
        """run_monthly/run_product_dossier/run_patent_survey's own failure shape."""
        assert _terminal_state({"monthly_report_error": "no items this month"}) == "failed"
        assert _terminal_state({"product_dossier_error": "extraction failed"}) == "failed"
        assert _terminal_state({"patent_survey_error": "no patents found"}) == "failed"

    def test_multi_target_loop_partial_success_is_partial_not_done(self) -> None:
        """run_bd_report/run_product_line_report's loop-over-all-targets shape: some targets
        succeeded (report_id present), one failed."""
        res = {
            "bd_reports": {
                "telaviv": {"report_id": 1, "qa_passed": True},
                "berlin": {"error": "no items in territory"},
            }
        }
        assert _terminal_state(res) == "partial"

    def test_multi_target_loop_total_failure_is_failed(self) -> None:
        res = {"bd_reports": {"telaviv": {"error": "boom"}, "berlin": {"error": "boom"}}}
        assert _terminal_state(res) == "failed"

    def test_status_done_downgraded_to_partial_by_sibling_error_key(self) -> None:
        """run_weekly's own shape: the daily-pipeline portion computed `status=done` (via
        run_daily's `_compute_run_status`), but the separate weekly-report build afterward
        failed -- must not stay `done`."""
        res = {
            "status": "done",
            "report": {"docx": "x.docx"},
            "weekly_report_error": "trend synthesis failed",
        }
        assert _terminal_state(res) == "partial"

    def test_status_failed_stays_failed_even_with_extra_error_keys(self) -> None:
        res = {"status": "failed", "report": {"error": "boom"}, "weekly_report_error": "also boom"}
        assert _terminal_state(res) == "failed"

    def test_handler_without_status_field_never_defaults_to_done_on_error(self) -> None:
        """The exact bug: `res.get("status")` is None and the old code unconditionally returned
        `"done"` for ANY handler result with no `status` key, even one carrying only an error."""
        assert _terminal_state({"tech_daily_report_error": "no items"}) != "done"


def _fresh_job(job_id: int = 1) -> dict:
    return {"id": job_id, "kind": "weekly_run", "payload": {}, "created_at": datetime.now(tz=UTC)}


def _old_job(job_id: int = 1) -> dict:
    return {
        "id": job_id,
        "kind": "weekly_run",
        "payload": {},
        "created_at": datetime.now(tz=UTC) - timedelta(hours=4),
    }


class TestRunWeekly:
    """F4/F22/N07 (SOL-REVIEW-2026-09-24): run_weekly must not re-run the full nightly pipeline
    when a separate daily_run job already covered tonight, AND must not build the weekly report
    on top of a daily_run that is merely queued/running (still-in-flight data) or hasn't even been
    inserted yet -- it must wait (defer itself, ResourceUnavailable) for that daily_run to reach a
    terminal state first, up to WEEKLY_DAILY_WAIT_MAX, after which it falls back to running the
    pipeline itself (the original standalone-weekly behaviour)."""

    def test_skips_daily_pipeline_when_daily_done_tonight(self, monkeypatch) -> None:
        monkeypatch.setattr(jobs, "_daily_run_state_tonight", lambda: "done")

        def _fail_if_called(job):
            raise AssertionError("run_daily should not be called when a daily_run already covered tonight")

        monkeypatch.setattr(jobs, "run_daily", _fail_if_called)

        class _Paths:
            report_id = 42
            qa = types.SimpleNamespace(passed=True)

        monkeypatch.setattr("eoa.report.weekly.build_weekly", lambda: _Paths())

        stats = run_weekly(_fresh_job())

        assert stats["daily_pipeline_skipped"] == "daily_run_already_covered"
        assert stats["daily_run_state"] == "done"
        assert stats["weekly_report"] == {"report_id": 42, "qa_passed": True}

    def test_skips_daily_pipeline_when_daily_partial_or_failed(self, monkeypatch) -> None:
        """A daily_run that finished `partial` or `failed` is still TERMINAL -- tonight's data is
        final either way, so weekly proceeds rather than waiting forever for a state that will
        never become `done`."""
        for terminal_state in ("partial", "failed"):
            monkeypatch.setattr(jobs, "_daily_run_state_tonight", lambda s=terminal_state: s)
            monkeypatch.setattr(
                jobs, "run_daily", lambda job: (_ for _ in ()).throw(AssertionError("must not rerun pipeline"))
            )
            monkeypatch.setattr(
                "eoa.report.weekly.build_weekly",
                lambda: types.SimpleNamespace(report_id=1, qa=types.SimpleNamespace(passed=True)),
            )
            stats = run_weekly(_fresh_job())
            assert stats["daily_run_state"] == terminal_state

    def test_defers_when_daily_still_running(self, monkeypatch) -> None:
        """THE regression this closes: the old code treated `running` as "covered" and built the
        weekly report immediately, on top of not-yet-finished daily analysis. Old code: no
        exception, `run_daily` skipped, `build_weekly` called right away. New code: defers
        (ResourceUnavailable) instead, and touches neither."""
        monkeypatch.setattr(jobs, "_daily_run_state_tonight", lambda: "running")
        monkeypatch.setattr(
            jobs, "run_daily", lambda job: (_ for _ in ()).throw(AssertionError("must not run pipeline yet"))
        )
        monkeypatch.setattr(
            "eoa.report.weekly.build_weekly",
            lambda: (_ for _ in ()).throw(AssertionError("must not build weekly report yet")),
        )

        with pytest.raises(ResourceUnavailable):
            run_weekly(_fresh_job())

    def test_defers_when_no_daily_row_yet(self, monkeypatch) -> None:
        """F22's claim-ordering race: weekly claimed before the scheduler's daily_run INSERT even
        committed. Old code treated "no row found" as "not covered" and ran the FULL pipeline
        itself immediately -- which the real daily_run job then also ran once claimed, producing
        two daily reports/notifications the same night. New code waits instead."""
        monkeypatch.setattr(jobs, "_daily_run_state_tonight", lambda: None)
        monkeypatch.setattr(
            jobs, "run_daily", lambda job: (_ for _ in ()).throw(AssertionError("must not run pipeline yet"))
        )

        with pytest.raises(ResourceUnavailable):
            run_weekly(_fresh_job())

    def test_falls_back_to_full_pipeline_after_wait_timeout(self, monkeypatch) -> None:
        """A weekly run that has been waiting past WEEKLY_DAILY_WAIT_MAX with no daily_run ever
        reaching a terminal state (or none at all) gives up waiting and runs the pipeline itself --
        preserves the original "weekly can stand alone" fallback."""
        monkeypatch.setattr(jobs, "_daily_run_state_tonight", lambda: "running")
        calls: list[dict] = []
        monkeypatch.setattr(jobs, "run_daily", lambda job: (calls.append(job), {"report": {"docx": "x"}})[1])

        class _Paths:
            report_id = 7
            qa = types.SimpleNamespace(passed=False)

        monkeypatch.setattr("eoa.report.weekly.build_weekly", lambda: _Paths())

        job = _old_job()
        stats = run_weekly(job)

        assert calls == [job]
        assert stats["report"] == {"docx": "x"}
        assert stats["weekly_report"] == {"report_id": 7, "qa_passed": False}

    def test_daily_run_state_tonight_returns_none_on_db_error(self, monkeypatch) -> None:
        monkeypatch.setattr("eoa.db.connection", _raise_no_db)
        assert jobs._daily_run_state_tonight() is None

    def test_daily_run_state_tonight_reads_most_recent_row(self, monkeypatch) -> None:
        captured: dict = {}

        class _FakeCursor:
            def execute(self, sql, params=None):
                captured["sql"] = sql
                captured["params"] = params

            def fetchone(self):
                return {"state": "running"}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _FakeConn:
            def cursor(self):
                return _FakeCursor()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConn())
        assert jobs._daily_run_state_tonight() == "running"
        assert "ORDER BY created_at DESC" in captured["sql"]


class TestNotify:
    """#16: _notify() sends a failure push (not "report ready") when no docx was produced.

    N03 (SOL-REVIEW-2026-09-24): every test in this class that expects the send path to actually
    run also stubs `mark_notification_sent` to report "not yet sent" (True) -- these tests are
    about the docx-present/missing branches, not the idempotency gate itself (see
    TestNotifyIdempotency below for that)."""

    def _allow_send(self, monkeypatch) -> None:
        monkeypatch.setattr("eoa.memory.relational.mark_notification_sent", lambda kind, key: True)

    def test_sends_failure_when_no_docx(self, monkeypatch) -> None:
        self._allow_send(monkeypatch)
        monkeypatch.setattr("eoa.db.connection", _raise_no_db)
        failures: list[tuple[str, str]] = []
        report_readies: list[tuple] = []
        monkeypatch.setattr(
            "eoa.orchestrator.jobs.ntfy.failure", lambda stage, msg: failures.append((stage, msg))
        )
        monkeypatch.setattr(
            "eoa.orchestrator.jobs.ntfy.report_ready",
            lambda *a, **kw: report_readies.append((a, kw)),
        )

        rs = RunState(job_id=1)
        result = _notify(rs, paths=types.SimpleNamespace())  # no .docx attribute at all

        assert result["report_missing"] is True
        assert len(failures) == 1
        assert failures[0][0] == "report"
        assert not report_readies

    def test_sends_failure_when_docx_is_none(self, monkeypatch) -> None:
        self._allow_send(monkeypatch)
        monkeypatch.setattr("eoa.db.connection", _raise_no_db)
        failures: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "eoa.orchestrator.jobs.ntfy.failure", lambda stage, msg: failures.append((stage, msg))
        )
        monkeypatch.setattr("eoa.orchestrator.jobs.ntfy.report_ready", lambda *a, **kw: pytest_fail())

        def pytest_fail():
            raise AssertionError("report_ready should not be called when docx is missing")

        rs = RunState(job_id=1)
        result = _notify(rs, paths=types.SimpleNamespace(docx=None))

        assert result["report_missing"] is True
        assert len(failures) == 1

    def test_sends_report_ready_when_docx_present(self, monkeypatch) -> None:
        self._allow_send(monkeypatch)
        monkeypatch.setattr("eoa.db.connection", _raise_no_db)
        failures: list[tuple] = []
        monkeypatch.setattr("eoa.orchestrator.jobs.ntfy.failure", lambda *a, **kw: failures.append((a, kw)))
        sent: dict = {}
        monkeypatch.setattr(
            "eoa.orchestrator.jobs.ntfy.report_ready",
            lambda kind, path_docx, headlines, ui_url=None: sent.update(
                kind=kind, path_docx=path_docx, headlines=headlines
            ),
        )

        rs = RunState(job_id=1)
        result = _notify(rs, paths=types.SimpleNamespace(docx="output/reports/2026-09-04.docx"))

        assert "report_missing" not in result
        assert sent["path_docx"] == "output/reports/2026-09-04.docx"
        assert not failures


class TestNotifyIdempotency:
    """N03 (SOL-REVIEW-2026-09-24): a stale-job replay of the SAME job (reap -> deferred ->
    reclaimed, same `job_id`) must not send the nightly notification twice. Old code: `_notify`
    always sends unconditionally, so replaying the same job through the `notify` stage a second
    time sends `ntfy.report_ready` (or `.failure`) a second time -- these tests fail against that
    code since it never calls `mark_notification_sent` at all and always emits a push."""

    def test_skips_send_when_already_sent(self, monkeypatch) -> None:
        monkeypatch.setattr("eoa.memory.relational.mark_notification_sent", lambda kind, key: False)
        monkeypatch.setattr("eoa.db.connection", _raise_no_db)
        report_readies: list[tuple] = []
        failures: list[tuple] = []
        monkeypatch.setattr(
            "eoa.orchestrator.jobs.ntfy.report_ready", lambda *a, **kw: report_readies.append((a, kw))
        )
        monkeypatch.setattr("eoa.orchestrator.jobs.ntfy.failure", lambda *a, **kw: failures.append((a, kw)))

        rs = RunState(job_id=99)
        result = _notify(rs, paths=types.SimpleNamespace(docx="output/reports/2026-09-04.docx"))

        assert result == {"notification_skipped": "already_sent_for_this_job"}
        assert not report_readies
        assert not failures

    def test_sends_when_not_yet_sent_and_keys_on_job_id(self, monkeypatch) -> None:
        """The idempotency key must be stable across a stale-job replay of the exact same job --
        `rs.job_id` (the jobs row is updated in place by `reap_stale_jobs`, never re-inserted)."""
        calls: list[tuple[str, str]] = []

        def _mark(kind, key):
            calls.append((kind, key))
            return True

        monkeypatch.setattr("eoa.memory.relational.mark_notification_sent", _mark)
        monkeypatch.setattr("eoa.db.connection", _raise_no_db)
        report_readies: list[tuple] = []
        monkeypatch.setattr(
            "eoa.orchestrator.jobs.ntfy.report_ready", lambda *a, **kw: report_readies.append((a, kw))
        )

        rs = RunState(job_id=123)
        _notify(rs, paths=types.SimpleNamespace(docx="output/reports/2026-09-04.docx"))

        assert calls == [("daily_report", "123")]
        assert report_readies

    def test_second_call_for_same_job_id_is_skipped_by_real_marker(self, monkeypatch) -> None:
        """Exercises the actual state-transition (not just a mocked bool): a real
        insert-once/skip-thereafter dict standing in for the DB's UNIQUE(kind, key) constraint --
        first call claims and sends, replaying the identical job_id afterward is silently skipped,
        matching what reap -> deferred -> reclaim does to a real `jobs` row."""
        seen: set[tuple[str, str]] = set()

        def _mark(kind, key):
            k = (kind, key)
            if k in seen:
                return False
            seen.add(k)
            return True

        monkeypatch.setattr("eoa.memory.relational.mark_notification_sent", _mark)
        monkeypatch.setattr("eoa.db.connection", _raise_no_db)
        report_readies: list[tuple] = []
        monkeypatch.setattr(
            "eoa.orchestrator.jobs.ntfy.report_ready", lambda *a, **kw: report_readies.append((a, kw))
        )

        rs = RunState(job_id=7)
        paths = types.SimpleNamespace(docx="output/reports/2026-09-04.docx")

        first = _notify(rs, paths=paths)
        second = _notify(rs, paths=paths)  # stale-job replay: same job_id, notify stage reruns

        assert "notification_skipped" not in first
        assert second == {"notification_skipped": "already_sent_for_this_job"}
        assert len(report_readies) == 1
