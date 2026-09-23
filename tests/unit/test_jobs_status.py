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


class TestRunWeekly:
    """F4: run_weekly must not re-run the full nightly pipeline when a separate daily_run job
    already covered tonight -- it should only build the weekly report on top of whatever that
    other job already did."""

    def test_skips_daily_pipeline_when_already_covered(self, monkeypatch) -> None:
        monkeypatch.setattr(jobs, "_daily_run_already_covered", lambda: True)

        def _fail_if_called(job):
            raise AssertionError("run_daily should not be called when a daily_run already covered tonight")

        monkeypatch.setattr(jobs, "run_daily", _fail_if_called)

        class _Paths:
            report_id = 42
            qa = types.SimpleNamespace(passed=True)

        monkeypatch.setattr("eoa.report.weekly.build_weekly", lambda: _Paths())

        stats = run_weekly({"id": 1, "kind": "weekly_run", "payload": {}})

        assert stats["daily_pipeline_skipped"] == "daily_run_already_covered"
        assert stats["weekly_report"] == {"report_id": 42, "qa_passed": True}

    def test_runs_daily_pipeline_when_not_covered(self, monkeypatch) -> None:
        monkeypatch.setattr(jobs, "_daily_run_already_covered", lambda: False)
        calls: list[dict] = []
        monkeypatch.setattr(jobs, "run_daily", lambda job: (calls.append(job), {"report": {"docx": "x"}})[1])

        class _Paths:
            report_id = 7
            qa = types.SimpleNamespace(passed=False)

        monkeypatch.setattr("eoa.report.weekly.build_weekly", lambda: _Paths())

        job = {"id": 2, "kind": "weekly_run", "payload": {}}
        stats = run_weekly(job)

        assert calls == [job]
        assert "daily_pipeline_skipped" not in stats
        assert stats["report"] == {"docx": "x"}
        assert stats["weekly_report"] == {"report_id": 7, "qa_passed": False}

    def test_daily_run_already_covered_returns_false_on_db_error(self, monkeypatch) -> None:
        monkeypatch.setattr("eoa.db.connection", _raise_no_db)
        assert jobs._daily_run_already_covered() is False

    def test_daily_run_already_covered_query_includes_queued(self, monkeypatch) -> None:
        """F22 (audit 2026-09-24): both `daily_run` and `weekly_run` are enqueued around the same
        01:00 tick -- if `weekly_run` happens to be claimed first, a `daily_run` row that merely
        exists but is still `queued` (not yet claimed) must already count as "covered" so
        `run_weekly` does not also run the full pipeline itself, which would run it twice."""
        captured: dict = {}

        class _FakeCursor:
            def execute(self, sql, params=None):
                captured["sql"] = sql
                captured["params"] = params

            def fetchone(self):
                return None

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
        jobs._daily_run_already_covered()
        assert "'queued'" in captured["sql"]
        assert "'running'" in captured["sql"]
        assert "'done'" in captured["sql"]
        assert "'partial'" in captured["sql"]


class TestNotify:
    """#16: _notify() sends a failure push (not "report ready") when no docx was produced."""

    def test_sends_failure_when_no_docx(self, monkeypatch) -> None:
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
