"""API tests for the product-dossier endpoints (PD-backend, user request 2026-09-08):
``GET/POST /api/dossiers``, ``GET /api/dossiers/{product_key}``,
``GET /api/dossiers/{product_key}/{id}``, ``POST /api/dossiers/{product_key}/rerun``.

Uses ``fastapi.testclient.TestClient`` with ``eoa.api.services`` functions monkeypatched (no real
DB, no network) -- mirrors ``tests/unit/test_api_smoke.py``'s own convention. The job enqueue itself
is mocked (``services.enqueue_product_dossier``/``rerun_product_dossier``) rather than
``eoa.memory.relational.enqueue_job`` directly, matching how ``eoa.api.routes.dossiers`` calls into
the services layer.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_product_dossier_api.py -q``
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from eoa import db

    monkeypatch.setattr(db, "get_pool", lambda: object())
    monkeypatch.setattr(db, "close_pool", lambda: None)

    from eoa.api.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


_LATEST_CARD = {
    "id": 1,
    "created_at": "2026-09-08T00:00:00",
    "outcome": "found",
    "confidence": 0.7,
    "report_id": 1,
}


def test_list_dossiers(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services

    monkeypatch.setattr(
        services,
        "list_dossiers",
        lambda: [
            {
                "product_key": "elbit-spectro-xr",
                "product_name": "SPECTRO XR",
                "vendor": "Elbit Systems",
                "latest": _LATEST_CARD,
                "count": 1,
            }
        ],
    )
    r = client.get("/api/dossiers")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["product_key"] == "elbit-spectro-xr"
    assert body[0]["count"] == 1


def test_create_dossier(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services

    captured = {}

    def fake_enqueue(
        product_name, vendor=None, aliases=None, product_line=None, budget_multiplier=None, llm_leg=None
    ):
        captured["args"] = (product_name, vendor, aliases, product_line, budget_multiplier, llm_leg)
        return {"job_id": 42, "product_key": "elbit-spectro-xr"}

    monkeypatch.setattr(services, "enqueue_product_dossier", fake_enqueue)
    r = client.post(
        "/api/dossiers",
        json={"product_name": "SPECTRO XR", "vendor": "Elbit Systems", "aliases": ["Spectro"]},
    )
    assert r.status_code == 200
    assert r.json() == {"job_id": 42, "product_key": "elbit-spectro-xr"}
    assert captured["args"][0] == "SPECTRO XR"


def test_create_dossier_missing_name_is_bad_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from eoa.api import services

    def fake_enqueue(
        product_name, vendor=None, aliases=None, product_line=None, budget_multiplier=None, llm_leg=None
    ):
        if not product_name:
            raise ValueError("product_name is required")
        return {"job_id": 1, "product_key": "x"}

    monkeypatch.setattr(services, "enqueue_product_dossier", fake_enqueue)
    r = client.post("/api/dossiers", json={"product_name": ""})
    assert r.status_code == 400


def test_dossier_detail_found(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services

    monkeypatch.setattr(
        services,
        "dossier_detail",
        lambda key: {
            "product_key": key,
            "product_name": "SPECTRO XR",
            "vendor": "Elbit Systems",
            "aliases": ["Spectro"],
            "dossiers": [_LATEST_CARD],
            "latest": {"identity": {"product_name": "SPECTRO XR"}, "sources": []},
            "pending_job": None,
        },
    )
    r = client.get("/api/dossiers/elbit-spectro-xr")
    assert r.status_code == 200
    assert r.json()["product_key"] == "elbit-spectro-xr"


def test_dossier_detail_not_found(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services

    monkeypatch.setattr(services, "dossier_detail", lambda key: None)
    r = client.get("/api/dossiers/unknown-product")
    assert r.status_code == 404


def test_dossier_run_detail(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services

    monkeypatch.setattr(
        services,
        "get_dossier",
        lambda key, dossier_id: {"id": dossier_id, "product_key": key, "data": {}, "sources": []},
    )
    r = client.get("/api/dossiers/elbit-spectro-xr/7")
    assert r.status_code == 200
    assert r.json()["id"] == 7


def test_dossier_run_not_found(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services

    monkeypatch.setattr(services, "get_dossier", lambda key, dossier_id: None)
    r = client.get("/api/dossiers/elbit-spectro-xr/999")
    assert r.status_code == 404


def test_rerun_dossier(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services

    monkeypatch.setattr(
        services,
        "rerun_product_dossier",
        lambda key, budget_multiplier=None, llm_leg=None: {"job_id": 99},
    )
    r = client.post("/api/dossiers/elbit-spectro-xr/rerun", json={})
    assert r.status_code == 200
    assert r.json() == {"job_id": 99}


def test_rerun_dossier_unknown_product(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services

    monkeypatch.setattr(
        services, "rerun_product_dossier", lambda key, budget_multiplier=None, llm_leg=None: None
    )
    r = client.post("/api/dossiers/unknown-product/rerun", json={})
    assert r.status_code == 404


def test_create_dossier_forwards_llm_leg(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """PD-cloud-tools (2026-09-09): the optional `llm_leg` request field reaches
    `services.enqueue_product_dossier` verbatim."""
    from eoa.api import services

    captured = {}

    def fake_enqueue(
        product_name, vendor=None, aliases=None, product_line=None, budget_multiplier=None, llm_leg=None
    ):
        captured["llm_leg"] = llm_leg
        return {"job_id": 42, "product_key": "elbit-spectro-xr"}

    monkeypatch.setattr(services, "enqueue_product_dossier", fake_enqueue)
    r = client.post(
        "/api/dossiers",
        json={"product_name": "SPECTRO XR", "llm_leg": "codex:gpt-6-astra"},
    )
    assert r.status_code == 200
    assert captured["llm_leg"] == "codex:gpt-6-astra"


def test_rerun_dossier_forwards_llm_leg(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services

    captured = {}

    def fake_rerun(key, budget_multiplier=None, llm_leg=None):
        captured["llm_leg"] = llm_leg
        return {"job_id": 99}

    monkeypatch.setattr(services, "rerun_product_dossier", fake_rerun)
    r = client.post("/api/dossiers/elbit-spectro-xr/rerun", json={"llm_leg": "claude:claude-sonnet-5"})
    assert r.status_code == 200
    assert captured["llm_leg"] == "claude:claude-sonnet-5"
