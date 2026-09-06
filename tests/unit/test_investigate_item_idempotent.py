"""Tests for Q5-3 (docs/qa/findings_Q5_r1.md): idempotent `POST /api/items/{id}/investigate`.

Repro: the feed's "I" shortcut fired the endpoint with no feedback and no dedup -- a double-press
(or a slow first click retried) queued two overlapping `deep_search` jobs for the same item, and an
investigation that had already finished for that item was silently ignored and re-run from scratch.
Fixed the same way as "הרץ עכשיו" (`RunAlreadyActive`, tests/unit/test_run_now_idempotent.py):
`services.investigate_item` refuses a second job while one is queued/running for the item (raising
`InvestigationAlreadyActive`, mapped to HTTP 409 by the route), and returns the existing job instead
of enqueueing a new one when a `done` investigation for the item finished in the last 24h.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_investigate_item_idempotent.py -q``
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from eoa.api import services


class TestInvestigateItemService:
    def test_missing_item_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "_fetchone", lambda *_a, **_kw: None)
        assert services.investigate_item(999, "שאלה") is None

    def test_active_job_for_item_raises_conflict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        active_job = {"id": 42, "state": "running"}
        calls: list[str] = []

        def fetchone(query: str, params: Any = None) -> Any:
            if "FROM items" in query:
                return {"id": 1}
            if "state IN ('queued', 'running')" in query:
                calls.append("active")
                assert params["item_id"] == 1
                return active_job
            raise AssertionError(query)

        monkeypatch.setattr(services, "_fetchone", fetchone)
        with pytest.raises(services.InvestigationAlreadyActive) as excinfo:
            services.investigate_item(1, "שאלה")
        assert excinfo.value.job["id"] == 42
        assert calls == ["active"]

    def test_recent_done_investigation_is_reused_not_reenqueued(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        done_job = {"id": 7, "state": "done"}
        enqueue_calls: list[Any] = []

        def fetchone(query: str, params: Any = None) -> Any:
            if "FROM items" in query:
                return {"id": 1}
            if "state IN ('queued', 'running')" in query:
                return None
            if "state = 'done'" in query:
                assert "interval '24 hours'" in query
                assert params["item_id"] == 1
                return done_job
            raise AssertionError(query)

        monkeypatch.setattr(services, "_fetchone", fetchone)
        monkeypatch.setattr(services.relational, "enqueue_job", lambda *_a, **_kw: enqueue_calls.append(1))

        result = services.investigate_item(1, "שאלה")
        assert result == {"job_id": 7, "existing": True}
        assert enqueue_calls == []  # no new job enqueued

    def test_no_existing_job_enqueues_normally(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fetchone(query: str, params: Any = None) -> Any:
            if "FROM items" in query:
                return {"id": 1}
            return None

        monkeypatch.setattr(services, "_fetchone", fetchone)
        monkeypatch.setattr(services.relational, "enqueue_job", lambda *_a, **_kw: 55)

        result = services.investigate_item(1, "שאלה חדשה")
        assert result == {"job_id": 55, "existing": False}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from eoa import db

    monkeypatch.setattr(db, "get_pool", lambda: object())
    monkeypatch.setattr(db, "close_pool", lambda: None)

    from eoa.api.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


class TestInvestigateItemRoute:
    def test_success_returns_job_id(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "investigate_item", lambda *_a, **_kw: {"job_id": 9, "existing": False})
        r = client.post("/api/items/1/investigate", json={"question": None})
        assert r.status_code == 200
        assert r.json() == {"job_id": 9, "existing": False}

    def test_existing_recent_investigation_returns_200_with_existing_true(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(services, "investigate_item", lambda *_a, **_kw: {"job_id": 7, "existing": True})
        r = client.post("/api/items/1/investigate", json={"question": None})
        assert r.status_code == 200
        assert r.json() == {"job_id": 7, "existing": True}

    def test_missing_item_returns_404(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "investigate_item", lambda *_a, **_kw: None)
        r = client.post("/api/items/999/investigate", json={"question": None})
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"

    def test_active_job_returns_409_with_job_detail(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_active(*_a: Any, **_kw: Any) -> Any:
            raise services.InvestigationAlreadyActive({"id": 42, "state": "running"})

        monkeypatch.setattr(services, "investigate_item", raise_active)
        r = client.post("/api/items/1/investigate", json={"question": None})
        assert r.status_code == 409
        body = r.json()
        assert body["error"]["code"] == "conflict"
        assert body["error"]["detail"] == {"job_id": 42, "state": "running"}
