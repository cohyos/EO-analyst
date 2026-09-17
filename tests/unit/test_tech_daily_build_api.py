"""Unit tests for "בנה דוח טכנולוגיה עכשיו" (user request 2026-09-17): the on-demand
``tech_daily_report`` build button's backend wiring --
``POST /api/reports/tech-daily/build`` -> ``eoa.api.services.enqueue_tech_daily_report`` ->
job kind ``tech_daily_report`` (``eoa.orchestrator.jobs.run_tech_daily_report_job``), and
``GET /api/reports/tech-daily/status`` -> ``eoa.api.services.tech_daily_status``.

Same ``TestClient`` + monkeypatch convention as ``tests/unit/test_investigate_item_idempotent.py``/
``tests/unit/test_reports_api_tech_daily.py`` (no live DB/Ollama/network). Does not touch
``eoa.report.tech_daily`` itself (owned by a concurrently-running agent) -- ``build_tech_daily`` is
monkeypatched wherever the job handler is exercised.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_tech_daily_build_api.py -q``
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from eoa.api import services


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from eoa import db

    monkeypatch.setattr(db, "get_pool", lambda: object())
    monkeypatch.setattr(db, "close_pool", lambda: None)

    from eoa.api.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------
# route -> service wiring
# --------------------------------------------------------------------------


class TestBuildRoute:
    def test_default_body_forwards_lookback_1_and_force_false(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        def fake_enqueue(lookback_days: int = 1, *, force: bool = False) -> dict[str, Any]:
            captured["lookback_days"] = lookback_days
            captured["force"] = force
            return {"job_id": 101}

        monkeypatch.setattr(services, "enqueue_tech_daily_report", fake_enqueue)

        r = client.post("/api/reports/tech-daily/build", json={})
        assert r.status_code == 200
        assert r.json() == {"job_id": 101}
        assert captured == {"lookback_days": 1, "force": False}

    def test_custom_lookback_and_force_forwarded(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            services,
            "enqueue_tech_daily_report",
            lambda lookback_days=1, *, force=False: captured.update(
                lookback_days=lookback_days, force=force
            )
            or {"job_id": 202},
        )

        r = client.post("/api/reports/tech-daily/build", json={"lookback_days": 7, "force": True})
        assert r.status_code == 200
        assert captured == {"lookback_days": 7, "force": True}

    def test_lookback_out_of_range_is_rejected_by_pydantic(self, client: TestClient) -> None:
        r = client.post("/api/reports/tech-daily/build", json={"lookback_days": 91})
        assert r.status_code == 422

    def test_service_value_error_maps_to_400(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(lookback_days: int = 1, *, force: bool = False):
            raise ValueError("lookback_days must be between 1 and 90")

        monkeypatch.setattr(services, "enqueue_tech_daily_report", boom)
        r = client.post("/api/reports/tech-daily/build", json={"lookback_days": 1})
        assert r.status_code == 400


class TestStatusRoute:
    def test_status_forwards_service_result(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = {
            "pending_job": {"id": 5, "state": "running", "created_at": "2026-09-17T04:00:00+03:00"},
            "latest": None,
        }
        monkeypatch.setattr(services, "tech_daily_status", lambda: payload)
        r = client.get("/api/reports/tech-daily/status")
        assert r.status_code == 200
        assert r.json() == payload


# --------------------------------------------------------------------------
# eoa.api.services
# --------------------------------------------------------------------------


class TestEnqueueTechDailyReport:
    def test_rejects_out_of_range_lookback(self) -> None:
        with pytest.raises(ValueError):
            services.enqueue_tech_daily_report(0)
        with pytest.raises(ValueError):
            services.enqueue_tech_daily_report(91)

    def test_no_pending_job_enqueues_a_new_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "_fetchone", lambda *a, **k: None)
        enqueue_calls: list[Any] = []
        monkeypatch.setattr(
            services.relational,
            "enqueue_job",
            lambda kind, payload, *, priority: enqueue_calls.append((kind, payload, priority)) or 77,
        )

        result = services.enqueue_tech_daily_report(7, force=True)
        assert result == {"job_id": 77}
        assert enqueue_calls == [("tech_daily_report", {"lookback_days": 7, "force": True}, 4)]

    def test_pending_job_is_reused_instead_of_enqueuing_a_second_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pending_row = {"id": 55, "state": "queued", "created_at": "2026-09-17T04:00:00+03:00"}
        monkeypatch.setattr(services, "_fetchone", lambda *a, **k: pending_row)
        enqueue_calls: list[Any] = []
        monkeypatch.setattr(
            services.relational, "enqueue_job", lambda *a, **k: enqueue_calls.append(1) or 999
        )

        result = services.enqueue_tech_daily_report(1)
        assert result == {"job_id": 55}
        assert enqueue_calls == []


class TestTechDailyStatus:
    def test_no_pending_and_no_report_yet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "_fetchone", lambda *a, **k: None)
        assert services.tech_daily_status() == {"pending_job": None, "latest": None}

    def test_pending_job_and_latest_report_both_surfaced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        def fake_fetchone(query: str, params: Any = None) -> Any:
            if "FROM jobs" in query:
                calls.append("jobs")
                return {"id": 5, "state": "running", "created_at": "2026-09-17T04:00:00+03:00"}
            calls.append("reports")
            return {"id": 42, "created_at": "2026-09-16T04:00:00+03:00", "period_end": "2026-09-16"}

        monkeypatch.setattr(services, "_fetchone", fake_fetchone)
        result = services.tech_daily_status()
        assert result == {
            "pending_job": {"id": 5, "state": "running", "created_at": "2026-09-17T04:00:00+03:00"},
            "latest": {"report_id": 42, "created_at": "2026-09-16T04:00:00+03:00", "period_end": "2026-09-16"},
        }
        assert calls == ["reports", "jobs"] or calls == ["jobs", "reports"]


# --------------------------------------------------------------------------
# orchestrator job handler
# --------------------------------------------------------------------------


class TestRunTechDailyReportJob:
    def test_calls_build_tech_daily_with_payload_and_returns_report_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from eoa.orchestrator import jobs as jobs_mod
        from eoa.report import tech_daily as tech_daily_mod

        captured: dict[str, Any] = {}

        def fake_build_tech_daily(*, period_end=None, lookback_days=1, force=False, **_kw):
            captured["period_end"] = period_end
            captured["lookback_days"] = lookback_days
            captured["force"] = force
            return type("Paths", (), {"report_id": 123})()

        monkeypatch.setattr(tech_daily_mod, "build_tech_daily", fake_build_tech_daily)

        job = {"payload": {"lookback_days": 30, "force": True}}
        result = jobs_mod.run_tech_daily_report_job(job)

        assert result == {"report_id": 123}
        assert captured["lookback_days"] == 30
        assert captured["force"] is True

    def test_defaults_when_payload_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.orchestrator import jobs as jobs_mod
        from eoa.report import tech_daily as tech_daily_mod

        captured: dict[str, Any] = {}

        def fake_build_tech_daily(*, period_end=None, lookback_days=1, force=False, **_kw):
            captured["lookback_days"] = lookback_days
            captured["force"] = force
            return type("Paths", (), {"report_id": 1})()

        monkeypatch.setattr(tech_daily_mod, "build_tech_daily", fake_build_tech_daily)

        result = jobs_mod.run_tech_daily_report_job({})
        assert result == {"report_id": 1}
        assert captured == {"lookback_days": 1, "force": False}

    def test_is_registered_in_handlers(self) -> None:
        from eoa.orchestrator import jobs as jobs_mod

        assert jobs_mod.HANDLERS["tech_daily_report"] is jobs_mod.run_tech_daily_report_job
