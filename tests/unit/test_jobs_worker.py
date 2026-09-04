"""Tests for eoa.orchestrator.jobs: role-aware default worker kinds (#14) and the Worker loop's
terminal-state selection from a handler result's `status` field (#16).

No DB: `claim_next_job` / `finish_job` are monkeypatched at the `eoa.orchestrator.jobs` module
level so `Worker.run()` can be driven for exactly one iteration without touching Postgres.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_jobs_worker.py -q``
"""

from __future__ import annotations

from eoa.orchestrator.jobs import HANDLERS, Worker, _default_kinds, _terminal_state


class TestDefaultKinds:
    """#14: the isolated `agent` worker never claims `ingest` (fetcher-owned)."""

    def test_agent_role_excludes_ingest(self, monkeypatch) -> None:
        monkeypatch.setenv("EOA_ROLE", "agent")
        kinds = _default_kinds()
        assert "ingest" not in kinds
        assert set(kinds) == set(HANDLERS) - {"ingest"}

    def test_host_role_includes_ingest(self, monkeypatch) -> None:
        monkeypatch.setenv("EOA_ROLE", "host")
        kinds = _default_kinds()
        assert "ingest" in kinds
        assert set(kinds) == set(HANDLERS)

    def test_no_role_set_includes_ingest(self, monkeypatch) -> None:
        monkeypatch.delenv("EOA_ROLE", raising=False)
        kinds = _default_kinds()
        assert "ingest" in kinds
        assert set(kinds) == set(HANDLERS)

    def test_other_role_includes_ingest(self, monkeypatch) -> None:
        # only the literal "agent" role is fetcher-isolated; anything else (dev, ci, ...) is host-like
        monkeypatch.setenv("EOA_ROLE", "dev")
        kinds = _default_kinds()
        assert "ingest" in kinds

    def test_worker_uses_default_kinds_when_none_given(self, monkeypatch) -> None:
        monkeypatch.setenv("EOA_ROLE", "agent")
        w = Worker()
        assert "ingest" not in w.kinds

    def test_worker_explicit_kinds_override_role_default(self, monkeypatch) -> None:
        monkeypatch.setenv("EOA_ROLE", "agent")
        w = Worker(kinds=["ingest", "report"])
        assert w.kinds == ["ingest", "report"]


class TestTerminalState:
    """#16: Worker picks the job's terminal state from a handler result's `status` field."""

    def test_recognized_status_wins(self) -> None:
        assert _terminal_state({"status": "partial"}) == "partial"
        assert _terminal_state({"status": "failed"}) == "failed"
        assert _terminal_state({"status": "done"}) == "done"

    def test_unrecognized_status_defaults_to_done(self) -> None:
        assert _terminal_state({"status": "bogus"}) == "done"

    def test_missing_status_defaults_to_done(self) -> None:
        assert _terminal_state({"investigations": 3}) == "done"
        assert _terminal_state({}) == "done"


class TestWorkerRunLoop:
    """One driven iteration of Worker.run(), mocking the DB-touching functions it calls."""

    def _run_one_iteration(self, monkeypatch, *, job, handler_result=None, handler_kind="daily_run"):
        finished: list[dict] = []
        claims: list[dict] = []

        w = Worker(kinds=["daily_run"], poll_seconds=0)

        call_count = {"n": 0}

        def fake_claim(kinds, worker_id=None, **kw):
            claims.append({"kinds": kinds, "worker_id": worker_id})
            call_count["n"] += 1
            if call_count["n"] == 1:
                return job
            w.stop_event.set()  # stop after the first claim so run() returns
            return None

        def fake_finish(job_id, state, *, result=None, error=None, not_before=None, worker_id=None):
            finished.append(
                {"job_id": job_id, "state": state, "result": result, "error": error, "worker_id": worker_id}
            )

        monkeypatch.setattr("eoa.orchestrator.jobs.claim_next_job", fake_claim)
        monkeypatch.setattr("eoa.orchestrator.jobs.finish_job", fake_finish)
        monkeypatch.setattr("eoa.orchestrator.jobs.reap_stale_jobs", lambda *a, **kw: 0)
        if handler_result is not None:
            monkeypatch.setitem(HANDLERS, handler_kind, lambda job: handler_result)

        w.run()
        return claims, finished

    def test_claims_with_worker_id(self, monkeypatch) -> None:
        job = {"id": 42, "kind": "daily_run", "payload": {}}
        claims, finished = self._run_one_iteration(
            monkeypatch, job=job, handler_result={"status": "done", "total_minutes": 1}
        )
        assert len(claims) >= 1
        assert claims[0]["kinds"] == ["daily_run"]
        assert claims[0]["worker_id"]  # non-empty
        assert finished[0]["worker_id"] == claims[0]["worker_id"]

    def test_uses_handler_status_as_terminal_state(self, monkeypatch) -> None:
        job = {"id": 42, "kind": "daily_run", "payload": {}}
        _, finished = self._run_one_iteration(monkeypatch, job=job, handler_result={"status": "partial"})
        assert finished[0]["state"] == "partial"
        assert finished[0]["job_id"] == 42

    def test_defaults_to_done_when_handler_result_has_no_status(self, monkeypatch) -> None:
        job = {"id": 43, "kind": "daily_run", "payload": {}}
        _, finished = self._run_one_iteration(monkeypatch, job=job, handler_result={"investigations": 2})
        assert finished[0]["state"] == "done"

    def test_no_handler_fails_the_job(self, monkeypatch) -> None:
        job = {"id": 44, "kind": "unknown_kind", "payload": {}}
        _, finished = self._run_one_iteration(
            monkeypatch, job=job, handler_result=None, handler_kind="__unused__"
        )
        assert finished[0]["state"] == "failed"
        assert "no handler" in finished[0]["error"]

    def test_handler_exception_fails_the_job(self, monkeypatch) -> None:
        job = {"id": 45, "kind": "daily_run", "payload": {}}

        def boom(_job):
            raise RuntimeError("kaboom")

        monkeypatch.setitem(HANDLERS, "daily_run", boom)
        finished: list[dict] = []
        w = Worker(kinds=["daily_run"], poll_seconds=0)
        call_count = {"n": 0}

        def fake_claim(kinds, worker_id=None, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return job
            w.stop_event.set()
            return None

        def fake_finish(job_id, state, *, result=None, error=None, not_before=None, worker_id=None):
            finished.append({"job_id": job_id, "state": state, "error": error})

        monkeypatch.setattr("eoa.orchestrator.jobs.claim_next_job", fake_claim)
        monkeypatch.setattr("eoa.orchestrator.jobs.finish_job", fake_finish)
        monkeypatch.setattr("eoa.orchestrator.jobs.reap_stale_jobs", lambda *a, **kw: 0)

        w.run()

        assert finished[0]["state"] == "failed"
        assert "kaboom" in finished[0]["error"]
