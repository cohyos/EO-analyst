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
from eoa.notify import ntfy
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
        "created_at": datetime.now(tz=UTC) - jobs.WEEKLY_DAILY_WAIT_MAX - timedelta(minutes=1),
    }


def _must_not(what: str):
    def _fail(*_a, **_k):
        raise AssertionError(f"must not {what}")

    return _fail


def _fake_weekly(report_id: int = 42, passed: bool = True):
    return lambda: types.SimpleNamespace(report_id=report_id, qa=types.SimpleNamespace(passed=passed))


class TestRunWeekly:
    """F4/F22/N07 + S01/S02 (SOL-REVIEW3-2026-09-24): run_weekly never runs the nightly pipeline
    itself. It waits (defers, ResourceUnavailable) until tonight's daily_run reaches a terminal
    state -- admitting one through the shared admission gate when none exists -- and builds the
    weekly report only on a terminal daily run."""

    @pytest.mark.parametrize("terminal_state", ["done", "partial", "failed"])
    def test_builds_weekly_report_once_daily_is_terminal(self, monkeypatch, terminal_state) -> None:
        """`partial`/`failed` are terminal too: tonight's data is final either way."""
        monkeypatch.setattr(jobs, "_daily_run_state_tonight", lambda since, s=terminal_state: s)
        monkeypatch.setattr(jobs, "run_daily", _must_not("run the daily pipeline"))
        monkeypatch.setattr("eoa.report.weekly.build_weekly", _fake_weekly())

        stats = run_weekly(_fresh_job())

        assert stats["daily_run_state"] == terminal_state
        assert stats["weekly_report"] == {"report_id": 42, "qa_passed": True}

    @pytest.mark.parametrize("state", ["queued", "running", "deferred", jobs.DAILY_STATE_QUERY_FAILED])
    def test_defers_while_daily_not_terminal_or_state_unknown(self, monkeypatch, state) -> None:
        """S02/R03: a non-terminal daily (and, new, a FAILED state query -- previously read as "no
        daily row") defers; nothing is built and no daily run is admitted."""
        monkeypatch.setattr(jobs, "_daily_run_state_tonight", lambda since: state)
        monkeypatch.setattr(jobs, "run_daily", _must_not("run the daily pipeline"))
        monkeypatch.setattr("eoa.report.weekly.build_weekly", _must_not("build the weekly report yet"))
        from eoa.orchestrator import admission

        monkeypatch.setattr(admission, "admit_daily_run", _must_not("admit a second daily_run"))

        with pytest.raises(ResourceUnavailable):
            run_weekly(_fresh_job())

    def test_no_daily_run_admits_one_through_the_gate_and_defers(self, monkeypatch) -> None:
        """S01: the weekly-first Saturday ordering. Pre-fix, weekly ran the pipeline itself after a
        timeout; now it admits the daily_run through `admission.admit_daily_run` (the same gate as
        the cron and API) and waits for it."""
        monkeypatch.setattr(jobs, "_daily_run_state_tonight", lambda since: None)
        monkeypatch.setattr(jobs, "run_daily", _must_not("run the daily pipeline"))
        monkeypatch.setattr("eoa.report.weekly.build_weekly", _must_not("build the weekly report yet"))
        from eoa.orchestrator import admission

        admitted: list[tuple] = []
        monkeypatch.setattr(
            admission, "admit_daily_run", lambda mode, priority: admitted.append((mode, priority)) or 77
        )

        with pytest.raises(ResourceUnavailable):
            run_weekly(_fresh_job())
        assert admitted == [("full", 2)]

    def test_admission_error_defers_instead_of_failing(self, monkeypatch) -> None:
        monkeypatch.setattr(jobs, "_daily_run_state_tonight", lambda since: None)
        from eoa.orchestrator import admission

        monkeypatch.setattr(admission, "admit_daily_run", lambda mode, priority: _raise_no_db())
        with pytest.raises(ResourceUnavailable):
            run_weekly(_fresh_job())

    def test_anchor_is_the_weekly_jobs_own_creation_time(self, monkeypatch) -> None:
        seen: list[datetime] = []
        monkeypatch.setattr(jobs, "_daily_run_state_tonight", lambda since: seen.append(since) or "done")
        monkeypatch.setattr("eoa.report.weekly.build_weekly", _fake_weekly())
        job = _fresh_job()
        run_weekly(job)
        assert seen == [job["created_at"] - jobs.WEEKLY_DAILY_LOOKBACK]


class TestWeeklyWaitTimeout:
    """S02 (SOL-REVIEW3-2026-09-24): after WEEKLY_DAILY_WAIT_MAX the job ends `partial` WITHOUT
    building the weekly report -- the round-3 version set `partial` and then fell through to an
    unconditional `build_weekly()` on incomplete daily data -- and never runs the pipeline."""

    @pytest.mark.parametrize("state", [None, "queued", "running", "deferred", jobs.DAILY_STATE_QUERY_FAILED])
    def test_timeout_never_builds_on_incomplete_data(self, monkeypatch, state) -> None:
        monkeypatch.setattr(jobs, "_daily_run_state_tonight", lambda since: state)
        monkeypatch.setattr(jobs, "run_daily", _must_not("duplicate the pipeline"))
        monkeypatch.setattr("eoa.report.weekly.build_weekly", _must_not("build on incomplete data"))
        from eoa.orchestrator import admission

        monkeypatch.setattr(admission, "admit_daily_run", _must_not("admit after the wait budget"))

        stats = run_weekly(_old_job())

        assert stats["status"] == "partial"
        assert stats["weekly_report_skipped"] == "daily_run_not_terminal_after_wait"
        assert stats["daily_run_state"] == state
        assert "weekly_report" not in stats
        assert jobs._terminal_state(stats) == "partial"


class TestDailyRunStateTonight:
    def test_db_error_is_distinct_from_no_row(self, monkeypatch) -> None:
        """S02: a failed query must not read as "no daily_run" (None), which would admit one."""
        monkeypatch.setattr("eoa.db.connection", _raise_no_db)
        assert jobs._daily_run_state_tonight(datetime.now(tz=UTC)) == jobs.DAILY_STATE_QUERY_FAILED

    @pytest.mark.parametrize("row,expected", [({"state": "running"}, "running"), (None, None)])
    def test_query_shape(self, monkeypatch, row, expected) -> None:
        captured: dict = {}

        class _FakeCursor:
            def execute(self, sql, params=None):
                captured["sql"] = sql
                captured["params"] = params

            def fetchone(self):
                return row

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
        since = datetime.now(tz=UTC) - timedelta(hours=6)
        assert jobs._daily_run_state_tonight(since) == expected
        assert "ORDER BY created_at DESC" in captured["sql"]
        # an older daily_run that is still active is tonight's run, whenever it was created
        assert "state IN ('queued', 'running', 'deferred')" in captured["sql"]
        assert captured["params"] == {"since": since}


class TestNotify:
    """#16: _notify() sends a failure push (not "report ready") when no docx was produced.

    R02/N03 (SOL-REVIEW2-2026-09-24): every test in this class that expects the send path to
    actually run also stubs `claim_notification_pending` to report "claimed, go ahead" (True) --
    these tests are about the docx-present/missing branches, not the claim/result state machine
    itself (see TestNotifyIdempotency below for that). `ntfy.report_ready`/`.failure` mocks now
    return a `Sent`-like object (`.ok`) since `_notify` reads that to decide `sent`/`failed`."""

    def _allow_send(self, monkeypatch) -> None:
        monkeypatch.setattr("eoa.memory.relational.claim_notification_pending", lambda kind, key: True)
        monkeypatch.setattr(
            "eoa.memory.relational.mark_notification_result",
            lambda kind, key, ok, payload=None: None,
        )

    def test_sends_failure_when_no_docx(self, monkeypatch) -> None:
        self._allow_send(monkeypatch)
        monkeypatch.setattr("eoa.db.connection", _raise_no_db)
        failures: list[tuple[str, str]] = []
        report_readies: list[tuple] = []
        monkeypatch.setattr(
            "eoa.orchestrator.jobs.ntfy.failure",
            lambda stage, msg: failures.append((stage, msg)) or ntfy.Sent(True, None, "u"),
        )
        monkeypatch.setattr(
            "eoa.orchestrator.jobs.ntfy.report_ready",
            lambda *a, **kw: report_readies.append((a, kw)) or ntfy.Sent(True, None, "u"),
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
            "eoa.orchestrator.jobs.ntfy.failure",
            lambda stage, msg: failures.append((stage, msg)) or ntfy.Sent(True, None, "u"),
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
            )
            or ntfy.Sent(True, None, "u"),
        )

        rs = RunState(job_id=1)
        result = _notify(rs, paths=types.SimpleNamespace(docx="output/reports/2026-09-04.docx"))

        assert "report_missing" not in result
        assert "notification_error" not in result
        assert sent["path_docx"] == "output/reports/2026-09-04.docx"
        assert not failures

    def test_delivery_failure_without_exception_marks_failed_and_flags_partial(
        self, monkeypatch
    ) -> None:
        """R02/N03: `ntfy.send` can return `Sent(ok=False)` without raising (HTTP error, timeout,
        unreachable server). `_notify` must record `failed` (not `sent`) via
        `mark_notification_result`, and surface a `notification_error` key so the run's overall
        status (`has_incomplete_work`) is `partial`, not `done`."""
        monkeypatch.setattr("eoa.memory.relational.claim_notification_pending", lambda kind, key: True)
        recorded: list[tuple[str, str, bool]] = []
        monkeypatch.setattr(
            "eoa.memory.relational.mark_notification_result",
            lambda kind, key, ok, payload=None: recorded.append((kind, key, ok)),
        )
        monkeypatch.setattr("eoa.db.connection", _raise_no_db)
        monkeypatch.setattr(
            "eoa.orchestrator.jobs.ntfy.report_ready", lambda *a, **kw: ntfy.Sent(False, None, "http://x")
        )

        rs = RunState(job_id=55)
        result = _notify(rs, paths=types.SimpleNamespace(docx="output/reports/2026-09-04.docx"))

        assert recorded == [("daily_report", "55", False)]
        assert "notification_error" in result


class TestNotifyIdempotency:
    """N03/R02 (SOL-REVIEW-2026-09-24 / SOL-REVIEW2-2026-09-24): a stale-job replay of the SAME
    job (reap -> deferred -> reclaimed, same `job_id`) must not send the nightly notification
    twice. Old code: `_notify` always sends unconditionally, so replaying the same job through the
    `notify` stage a second time sends `ntfy.report_ready` (or `.failure`) a second time -- these
    tests fail against that code since it never calls `claim_notification_pending` at all and
    always emits a push."""

    def test_skips_send_when_already_sent(self, monkeypatch) -> None:
        monkeypatch.setattr("eoa.memory.relational.claim_notification_pending", lambda kind, key: False)
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

    def test_sends_when_claimed_and_keys_on_job_id(self, monkeypatch) -> None:
        """The idempotency key must be stable across a stale-job replay of the exact same job --
        `rs.job_id` (the jobs row is updated in place by `reap_stale_jobs`, never re-inserted)."""
        claim_calls: list[tuple[str, str]] = []

        def _claim(kind, key):
            claim_calls.append((kind, key))
            return True

        monkeypatch.setattr("eoa.memory.relational.claim_notification_pending", _claim)
        result_calls: list[tuple[str, str, bool]] = []
        monkeypatch.setattr(
            "eoa.memory.relational.mark_notification_result",
            lambda kind, key, ok, payload=None: result_calls.append((kind, key, ok)),
        )
        monkeypatch.setattr("eoa.db.connection", _raise_no_db)
        report_readies: list[tuple] = []
        monkeypatch.setattr(
            "eoa.orchestrator.jobs.ntfy.report_ready",
            lambda *a, **kw: report_readies.append((a, kw)) or ntfy.Sent(True, None, "u"),
        )

        rs = RunState(job_id=123)
        _notify(rs, paths=types.SimpleNamespace(docx="output/reports/2026-09-04.docx"))

        assert claim_calls == [("daily_report", "123")]
        assert result_calls == [("daily_report", "123", True)]
        assert report_readies

    def test_second_call_for_same_job_id_is_skipped_by_real_state_machine(self, monkeypatch) -> None:
        """Exercises the actual state-transition (not just a mocked bool): a real in-memory
        pending/sent dict standing in for the DB's `(kind, key)` row -- first call claims, sends,
        and marks `sent`; replaying the identical job_id afterward is silently skipped (already
        `sent`, never reclaimed), matching what reap -> deferred -> reclaim does to a real `jobs`
        row and its `notifications_sent` marker."""
        state: dict[tuple[str, str], str] = {}

        def _claim(kind, key):
            k = (kind, key)
            if state.get(k) == "sent":
                return False
            state[k] = "pending"
            return True

        def _result(kind, key, ok, payload=None):
            state[(kind, key)] = "sent" if ok else "failed"

        monkeypatch.setattr("eoa.memory.relational.claim_notification_pending", _claim)
        monkeypatch.setattr("eoa.memory.relational.mark_notification_result", _result)
        monkeypatch.setattr("eoa.db.connection", _raise_no_db)
        report_readies: list[tuple] = []
        monkeypatch.setattr(
            "eoa.orchestrator.jobs.ntfy.report_ready",
            lambda *a, **kw: report_readies.append((a, kw)) or ntfy.Sent(True, None, "u"),
        )

        rs = RunState(job_id=7)
        paths = types.SimpleNamespace(docx="output/reports/2026-09-04.docx")

        first = _notify(rs, paths=paths)
        second = _notify(rs, paths=paths)  # stale-job replay: same job_id, notify stage reruns

        assert "notification_skipped" not in first
        assert second == {"notification_skipped": "already_sent_for_this_job"}
        assert len(report_readies) == 1

    def test_failed_send_is_retried_on_replay_and_eventually_sent(self, monkeypatch) -> None:
        """R02/N03: the exact regression this closes -- a failed send (`Sent(ok=False)`, no
        exception) must NOT be treated as `sent`; a later replay of the same job_id must retry it,
        and once that retry succeeds, a THIRD replay must finally skip (now genuinely `sent`)."""
        state: dict[tuple[str, str], str] = {}

        def _claim(kind, key):
            k = (kind, key)
            if state.get(k) == "sent":
                return False
            state[k] = "pending"
            return True

        def _result(kind, key, ok, payload=None):
            state[(kind, key)] = "sent" if ok else "failed"

        monkeypatch.setattr("eoa.memory.relational.claim_notification_pending", _claim)
        monkeypatch.setattr("eoa.memory.relational.mark_notification_result", _result)
        monkeypatch.setattr("eoa.db.connection", _raise_no_db)

        outcomes = iter([ntfy.Sent(False, None, "http://x"), ntfy.Sent(True, "id1", "http://x")])
        monkeypatch.setattr("eoa.orchestrator.jobs.ntfy.report_ready", lambda *a, **kw: next(outcomes))

        rs = RunState(job_id=7)
        paths = types.SimpleNamespace(docx="output/reports/2026-09-04.docx")

        first = _notify(rs, paths=paths)  # send fails (ok=False) -- state becomes "failed"
        assert "notification_error" in first
        assert state[("daily_report", "7")] == "failed"

        second = _notify(rs, paths=paths)  # replay retries (was "failed", not "sent") -- succeeds
        assert "notification_error" not in second
        assert "notification_skipped" not in second
        assert state[("daily_report", "7")] == "sent"

        third = _notify(rs, paths=paths)  # now genuinely sent -- skipped for good
        assert third == {"notification_skipped": "already_sent_for_this_job"}

    def test_exception_during_send_marks_failed_not_left_pending(self, monkeypatch) -> None:
        """R02/N03: an exception during the actual send (not just `Sent(ok=False)`) must also
        record `failed` (via the `except` clause in `_notify`), not leave the row stuck `pending`
        until the stale-claim window expires."""
        monkeypatch.setattr("eoa.memory.relational.claim_notification_pending", lambda kind, key: True)
        recorded: list[tuple[str, str, bool]] = []
        monkeypatch.setattr(
            "eoa.memory.relational.mark_notification_result",
            lambda kind, key, ok, payload=None: recorded.append((kind, key, ok)),
        )
        monkeypatch.setattr("eoa.db.connection", _raise_no_db)

        def _raise(*_a, **_kw):
            raise RuntimeError("ntfy server unreachable")

        monkeypatch.setattr("eoa.orchestrator.jobs.ntfy.report_ready", _raise)

        rs = RunState(job_id=8)
        with pytest.raises(RuntimeError, match="ntfy server unreachable"):
            _notify(rs, paths=types.SimpleNamespace(docx="output/reports/2026-09-04.docx"))

        assert recorded == [("daily_report", "8", False)]
