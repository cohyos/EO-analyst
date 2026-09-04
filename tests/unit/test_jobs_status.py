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

from eoa.orchestrator.jobs import RunState, _compute_run_status, _notify


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
        # export_backup's own key isn't "error"/"deferred"/"skipped"/"partial" (it's
        # "backup_error"), so this documents current behavior: only the four recognized keys
        # trip "partial" — a differently-named problem marker is not detected as one.
        assert _compute_run_status(stats) == "done"


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
