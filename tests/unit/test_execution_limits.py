"""Regressions for long jobs, shared resources and honest status."""

import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eoa import execution
from eoa.api import services
from eoa.errors import LeaseLost, ResourceUnavailable
from eoa.orchestrator import jobs, lease
from eoa.resources.gate import ResourceGate
from eoa.resources.gpu import GpuStatus, HostStatus, LoadedModel


def test_stage_stops_at_deadline_and_next_report_has_its_own_budget(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(execution.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(jobs, "heartbeat", Mock())
    monkeypatch.setattr(jobs, "settings", lambda: SimpleNamespace(stages={"tenders": 1, "report": 2}))
    rs = jobs.RunState(job_id=1)
    completed = []

    def tender_work():
        for i in range(100):
            execution.checkpoint()
            completed.append(i)
            clock[0] += 20

    jobs._run_stage(rs, "tenders", tender_work)
    assert completed == [0, 1, 2]
    assert rs.stats["tenders"]["partial"] == "deadline"
    jobs._run_stage(rs, "report", lambda: {"docx": "partial.docx"}, mandatory=True)
    assert rs.stats["report"]["docx"] == "partial.docx"
    assert jobs._compute_run_status(rs.stats) == "partial"


def test_partial_report_is_not_success():
    assert jobs._compute_run_status({"report": {"partial": "deadline"}}) == "failed"


def test_nested_timeout_never_extends_parent(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(execution.time, "monotonic", lambda: clock[0])
    with execution.deadline_scope(5):
        clock[0] = 12
        with execution.deadline_scope(100):
            assert execution.timeout_seconds(60) == 3
    assert execution.timeout_seconds(60) == 60


def test_lease_renews_while_handler_is_busy_and_stops_on_exit(monkeypatch):
    renewed = threading.Event()
    owners = []
    def renew(owner):
        owners.append(owner)
        renewed.set()
        return {7, 8}
    monkeypatch.setattr(lease, "renew_worker_leases", renew)
    with lease.keep_job_lease(7, "owned:1", interval=0.01):
        assert renewed.wait(1)
        execution.checkpoint()
        assert execution.worker_owner.get() == "owned:1"
    assert owners and set(owners) == {"owned:1"}
    assert execution.worker_owner.get() is None
    assert not any(t.name == "eoa-lease" and t.is_alive() for t in threading.enumerate())


def test_lost_lease_stops_work(monkeypatch):
    monkeypatch.setattr(lease, "renew_worker_leases", lambda owner: set())
    with lease.keep_job_lease(7, "old:1", interval=0.01):
        assert execution.lease_lost.get().wait(1)
        with pytest.raises(LeaseLost):
            execution.checkpoint()


@pytest.mark.parametrize("state, expected", [("failed", "failed"), ("partial", "skipped"), ("done", "skipped"), ("running", "running")])
def test_interrupted_stage_is_never_invented_as_done(monkeypatch, state, expected):
    monkeypatch.setattr(services, "_fetchall", lambda *args: [{"stage": "tenders", "event": "start", "detail": {}}])
    assert services._stage_timeline_from_log(1, state)["tenders"]["status"] == expected


def test_service_probes_are_shared_and_cache_expires(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(services.time, "monotonic", lambda: clock[0])
    probe = Mock(return_value={"postgres": True})
    monkeypatch.setattr(services, "_probe_services", probe)
    first = services.services_status()
    first["postgres"] = False
    assert services.services_status() == {"postgres": True}
    assert probe.call_count == 1
    clock[0] = 16
    services.services_status()
    assert probe.call_count == 2


@pytest.mark.parametrize("interactive,night", [(True, False), (False, True), (True, True)])
def test_busy_gpu_blocks_even_loaded_model_at_night_or_in_chat(monkeypatch, interactive, night):
    from eoa.config import settings
    gate = ResourceGate()
    gate.force_night_mode = night
    model = settings().model("resident").ollama
    host = HostStatus(datetime.now(UTC), GpuStatus(12000, 5000, 95, 50), 32000, 64000, 100,
                      [LoadedModel(model, 4000, 4000)])
    monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda *args: host)
    monkeypatch.setattr(gate, "_record", lambda *args: None)
    with pytest.raises(ResourceUnavailable, match="GPU busy"):
        gate.acquire("resident", interactive=interactive)


def test_ddgs_readiness_never_runs_search(monkeypatch):
    import ddgs

    from eoa.search import provider
    forbidden = Mock(side_effect=AssertionError("no readiness query"))
    monkeypatch.setattr(ddgs, "DDGS", forbidden)
    monkeypatch.setattr(provider, "settings", lambda: SimpleNamespace(search=SimpleNamespace(provider="ddgs")))
    assert provider.ping(probe=False)
    forbidden.assert_not_called()


def test_completed_local_call_cannot_return_after_losing_lease():
    from eoa.resources.inference import local_inference_lock

    lost = threading.Event()
    token = execution.lease_lost.set(lost)
    try:
        with pytest.raises(LeaseLost):
            with local_inference_lock():
                lost.set()
    finally:
        execution.lease_lost.reset(token)
