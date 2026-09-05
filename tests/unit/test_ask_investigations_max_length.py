"""Q2-9: `AskRequest.question` (max 4000) and `NewInvestigationRequest.question` (max 2000) are
bounded so an oversized question can't be used to force an unreasonably large retrieval/LLM-
context or deep-search payload. `fastapi.testclient.TestClient` per `tests/unit/test_api_smoke.py`.
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


class TestAskRequestMaxLength:
    def test_question_over_4000_chars_rejected(self, client: TestClient) -> None:
        r = client.post("/api/ask", json={"question": "x" * 4001})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_error"

    def test_question_at_4000_chars_accepted(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.api import services
        from eoa.llm import ollama_client

        monkeypatch.setattr(services, "ask_retrieve", lambda *a, **k: [])
        monkeypatch.setattr(services, "ask_build_messages", lambda *a, **k: ([], []))
        monkeypatch.setattr(ollama_client, "resolve_provider_info", lambda provider: ("ollama", "resident"))
        monkeypatch.setattr(ollama_client, "chat_stream", lambda *a, **k: iter([]))

        r = client.post("/api/ask", json={"question": "x" * 4000})
        assert r.status_code == 200


class TestNewInvestigationRequestMaxLength:
    def test_question_over_2000_chars_rejected(self, client: TestClient) -> None:
        r = client.post("/api/investigations", json={"question": "x" * 2001})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_error"

    def test_question_at_2000_chars_accepted(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.api import services

        monkeypatch.setattr(services, "start_investigation", lambda question, item_id: 7)

        r = client.post("/api/investigations", json={"question": "x" * 2000})
        assert r.status_code == 200
        assert r.json() == {"job_id": 7}
