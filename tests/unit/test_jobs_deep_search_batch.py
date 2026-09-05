"""Tests for U8-6b (Revision 2026-09-06) wiring in eoa.orchestrator.jobs.run_deep_searches:
cloud mode delegates every claimed deep_search job of the run to one
`investigate_batch_cloud` call; local mode (default) is unchanged; a cloud-batch failure falls
back to the local per-job loop for the jobs already claimed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from eoa.errors import LLMOutputError
from eoa.orchestrator import jobs


def _job(job_id: int, *, question: str = "q", item_id: int | None = None, level: str = "yellow") -> dict:
    return {"id": job_id, "payload": {"question": question, "item_id": item_id, "level": level}}


def _fake_settings(mode: str, *, max_per_night: int = 4):
    return SimpleNamespace(
        deep_search=SimpleNamespace(max_per_night=max_per_night, per_investigation_timeout_min=25),
        llm_providers=SimpleNamespace(mode=mode),
        stages={},
    )


@pytest.fixture(autouse=True)
def _no_worker_id(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(jobs, "_worker_id", lambda: "test-worker")


class TestLocalModeUnchanged:
    def test_never_calls_investigate_batch_cloud(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(jobs, "settings", lambda: _fake_settings("local"))
        jobs_queue = [_job(1)]
        monkeypatch.setattr(jobs, "claim_next_job", lambda kinds, worker_id: jobs_queue.pop(0) if jobs_queue else None)
        finished = []
        monkeypatch.setattr(jobs, "finish_job", lambda *a, **k: finished.append(a))

        def boom(*a, **k):
            raise AssertionError("investigate_batch_cloud must not be imported/called in local mode")

        import eoa.search.deep_search as ds_mod

        monkeypatch.setattr(ds_mod, "investigate_batch_cloud", boom, raising=False)
        monkeypatch.setattr(
            ds_mod,
            "investigate",
            lambda *a, **k: SimpleNamespace(outcome="not_found", result=SimpleNamespace(outcome="not_found", answer_he="x")),
        )
        rs = SimpleNamespace(time_left_min=lambda: None)
        result = jobs.run_deep_searches(rs)
        assert result["investigations"] == 1


class TestCloudModeBatchDelegation:
    def test_claims_up_to_cap_and_delegates_in_one_call(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(jobs, "settings", lambda: _fake_settings("cloud", max_per_night=3))
        jobs_queue = [_job(1), _job(2), _job(3), _job(4)]  # a 4th job must not be claimed (cap=3)
        monkeypatch.setattr(jobs, "claim_next_job", lambda kinds, worker_id: jobs_queue.pop(0) if jobs_queue else None)
        finished = []
        monkeypatch.setattr(jobs, "finish_job", lambda job_id, status, **k: finished.append((job_id, status)))

        def fake_batch(pending):
            assert len(pending) == 3
            results = {
                p["job_id"]: SimpleNamespace(
                    outcome="found", result=SimpleNamespace(outcome="found", answer_he="ans", sources=[])
                )
                for p in pending
            }
            return results, "תובנה"

        import eoa.search.deep_search as ds_mod

        monkeypatch.setattr(ds_mod, "investigate_batch_cloud", fake_batch)
        monkeypatch.setattr(jobs, "_investigation_result_payload", lambda inv: {})
        rs = SimpleNamespace(time_left_min=lambda: None)
        result = jobs.run_deep_searches(rs)
        assert result["investigations"] == 3
        assert len(jobs_queue) == 1  # the 4th job left unclaimed
        assert {jid for jid, status in finished} == {1, 2, 3}
        assert all(status == "done" for _jid, status in finished)
        assert result["cross_insights_he"] == "תובנה"

    def test_no_pending_jobs_returns_zero_without_calling_batch(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(jobs, "settings", lambda: _fake_settings("cloud"))
        monkeypatch.setattr(jobs, "claim_next_job", lambda kinds, worker_id: None)

        def boom(pending):
            raise AssertionError("must not call investigate_batch_cloud with nothing claimed")

        import eoa.search.deep_search as ds_mod

        monkeypatch.setattr(ds_mod, "investigate_batch_cloud", boom)
        rs = SimpleNamespace(time_left_min=lambda: None)
        result = jobs.run_deep_searches(rs)
        assert result == {"investigations": 0, "outcomes": ""}

    def test_missing_job_in_results_marked_failed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(jobs, "settings", lambda: _fake_settings("cloud", max_per_night=2))
        jobs_queue = [_job(1), _job(2)]
        monkeypatch.setattr(jobs, "claim_next_job", lambda kinds, worker_id: jobs_queue.pop(0) if jobs_queue else None)
        finished = []
        monkeypatch.setattr(jobs, "finish_job", lambda job_id, status, **k: finished.append((job_id, status)))

        import eoa.search.deep_search as ds_mod

        # Only job 1 gets an answer back -- job 2 is missing from the cloud response.
        monkeypatch.setattr(
            ds_mod,
            "investigate_batch_cloud",
            lambda pending: (
                {1: SimpleNamespace(outcome="found", result=SimpleNamespace(outcome="found", answer_he="a", sources=[]))},
                "",
            ),
        )
        monkeypatch.setattr(jobs, "_investigation_result_payload", lambda inv: {})
        rs = SimpleNamespace(time_left_min=lambda: None)
        result = jobs.run_deep_searches(rs)
        assert ("2", "failed") not in finished  # job ids are ints, not strings
        assert (2, "failed") in finished
        assert (1, "done") in finished
        assert "failed" in result["outcomes"]

    def test_batch_failure_falls_back_to_local_per_job(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(jobs, "settings", lambda: _fake_settings("cloud", max_per_night=2))
        jobs_queue = [_job(1), _job(2)]
        monkeypatch.setattr(jobs, "claim_next_job", lambda kinds, worker_id: jobs_queue.pop(0) if jobs_queue else None)
        finished = []
        monkeypatch.setattr(jobs, "finish_job", lambda job_id, status, **k: finished.append((job_id, status)))

        import eoa.search.deep_search as ds_mod

        def fail_batch(pending):
            raise LLMOutputError("no tool-capable CLI available")

        monkeypatch.setattr(ds_mod, "investigate_batch_cloud", fail_batch)
        local_calls = []

        def fake_investigate(question, **kw):
            local_calls.append(kw.get("job_id"))
            return SimpleNamespace(outcome="not_found", result=SimpleNamespace(outcome="not_found", answer_he="x"))

        monkeypatch.setattr(ds_mod, "investigate", fake_investigate)
        rs = SimpleNamespace(time_left_min=lambda: None)
        result = jobs.run_deep_searches(rs)
        assert set(local_calls) == {1, 2}  # both jobs handled locally after the cloud call failed
        assert result["investigations"] == 2
