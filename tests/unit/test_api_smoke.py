"""Smoke tests for the FastAPI web API.

Uses `fastapi.testclient.TestClient` with `eoa.db.get_pool`/`close_pool`
monkeypatched (no real Postgres) and `eoa.api.services` functions
monkeypatched per-test with fixtures (no real DB queries, no Ollama). Covers
`/api/status`, `/api/items`, `/api/items/{id}/feedback`,
`/api/morning`, `/api/settings/taxonomy` GET, and the `{"error": {...}}`
error shape, per the task spec.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_api_smoke.py -q``
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from eoa import db

    # No real Postgres in this test: the app's lifespan opens/closes the
    # pool, so replace both with no-ops.
    monkeypatch.setattr(db, "get_pool", lambda: object())
    monkeypatch.setattr(db, "close_pool", lambda: None)

    from eoa.api.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_status(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services

    monkeypatch.setattr(
        services,
        "services_status",
        lambda: {"postgres": True, "ollama": False, "searxng": True, "ntfy": True},
    )
    monkeypatch.setattr(
        services,
        "pipeline_status",
        lambda: {
            "current_job": None,
            "queue_depth": 0,
            "stage": None,
            "night_window": False,
            "next_run_at": None,
            "last_run": None,
        },
    )

    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["services"] == {"postgres": True, "ollama": False, "searxng": True, "ntfy": True}
    assert "gate" in body and isinstance(body["gate"], dict)
    assert body["pipeline"]["queue_depth"] == 0
    assert "at" in body


def test_items_list(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services

    fixture_items = [
        {
            "id": 1,
            "title": "פוד כיוון חדש נחשף",
            "url": "https://example.com/a",
            "source_name": "מקור לדוגמה",
            "published_at": None,
            "lang": "he",
            "domain": "airborne_pods",
            "subdomain": "targeting_pods",
            "report_kind": None,
            "trl": None,
            "geography": "US",
            "score": 9,
            "level": "red",
            "triage_reason": "חברה מובילה + הכרזה ראשונית",
            "summary_he": "תקציר בעברית.",
            "so_what_he": "למה זה חשוב.",
            "entities_mentioned": ["RTX"],
            "tags": ["contract"],
            "security_status": "clean",
            "dedup_of": None,
            "key_facts": [],
        }
    ]
    captured: dict = {}

    def fake_list_items(**kwargs):
        captured.update(kwargs)
        return 1, fixture_items

    monkeypatch.setattr(services, "list_items", fake_list_items)

    r = client.get("/api/items", params={"level": "red,orange", "page": 1, "page_size": 50})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"] == fixture_items
    assert captured["level"] == "red,orange"


def test_item_feedback(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services

    def fake_item_feedback(item_id: int, user_level: str, comment: str | None):
        if item_id != 1:
            return None
        return {"id": item_id, "level": user_level, "triage_reason": comment}

    monkeypatch.setattr(services, "item_feedback", fake_item_feedback)

    r = client.post("/api/items/1/feedback", json={"user_level": "orange", "comment": "בדיקה"})
    assert r.status_code == 200
    assert r.json()["level"] == "orange"

    r_missing = client.post("/api/items/999/feedback", json={"user_level": "orange"})
    assert r_missing.status_code == 404
    assert r_missing.json()["error"]["code"] == "not_found"

    r_bad_level = client.post("/api/items/1/feedback", json={"user_level": "purple"})
    assert r_bad_level.status_code == 400
    assert r_bad_level.json()["error"]["code"] == "bad_request"


def test_morning(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services

    monkeypatch.setattr(
        services,
        "morning",
        lambda: {
            "report": None,
            "headlines": [],
            "open_points": [],
            "night_summary": None,
        },
    )

    r = client.get("/api/morning")
    assert r.status_code == 200
    body = r.json()
    assert body["headlines"] == []
    assert body["report"] is None


def test_settings_taxonomy_get(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services

    fake_yaml = "domains:\n  airborne_pods:\n    label: test\n"
    fake_revision = "deadbeef" * 8
    monkeypatch.setattr(
        services,
        "read_settings_yaml",
        lambda name: fake_yaml if name == "taxonomy" else "",
    )
    monkeypatch.setattr(services, "settings_revision", lambda name: fake_revision)

    r = client.get("/api/settings/taxonomy")
    assert r.status_code == 200
    assert r.json() == {"yaml": fake_yaml, "revision": fake_revision}


def test_settings_unknown_name_error_shape(client: TestClient) -> None:
    r = client.get("/api/settings/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"
    assert "message_he" in body["error"]


def test_error_shape_unknown_route(client: TestClient) -> None:
    r = client.get("/api/this-route-does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert "error" in body
    assert "code" in body["error"]
    assert "message_he" in body["error"]


def test_error_shape_validation(client: TestClient) -> None:
    # `entity_id` is a required int query param for /api/graph -> 422.
    r = client.get("/api/graph")
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation_error"
    assert isinstance(body["error"]["detail"], list)
