"""Tests for `GET/POST /api/mcp/*` (agent/eoa/api/routes/mcp.py + eoa.api.services.*_mcp_*).

Services are monkeypatched -- no real DB, no real subprocess/network calls.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eoa.api import services
from eoa.api.app import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


class TestGetMcpServers:
    def test_returns_service_payload(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            services,
            "list_mcp_servers",
            lambda: {"mcp_enabled": True, "servers": [{"id": "procurement", "ok": True, "tool_count": 6}]},
        )
        r = client.get("/api/mcp/servers")
        assert r.status_code == 200
        assert r.json()["mcp_enabled"] is True
        assert r.json()["servers"][0]["id"] == "procurement"


class TestPostMcpServerPing:
    def test_success(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            services, "ping_mcp_server", lambda server_id: {"id": server_id, "ok": True, "tool_count": 3}
        )
        r = client.post("/api/mcp/servers/procurement/ping")
        assert r.status_code == 200
        assert r.json() == {"id": "procurement", "ok": True, "tool_count": 3}

    def test_unknown_server_is_404(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        def raise_not_found(server_id):
            raise services.McpServerNotFound(server_id)

        monkeypatch.setattr(services, "ping_mcp_server", raise_not_found)
        r = client.post("/api/mcp/servers/bogus/ping")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"


class TestGetMcpCalls:
    def test_default_since(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        captured = {}

        def fake_summarize(since_hours):
            captured["since_hours"] = since_hours
            return {"since_hours": since_hours, "calls": [], "totals": {"calls": 0, "failures": 0, "flagged": 0}}

        monkeypatch.setattr(services, "summarize_mcp_calls", fake_summarize)
        r = client.get("/api/mcp/calls")
        assert r.status_code == 200
        assert captured["since_hours"] == 24

    def test_custom_since(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        captured = {}
        monkeypatch.setattr(
            services,
            "summarize_mcp_calls",
            lambda since_hours: captured.update(h=since_hours) or {"since_hours": since_hours, "calls": [], "totals": {}},
        )
        client.get("/api/mcp/calls?since=6h")
        assert captured["h"] == 6

    def test_invalid_since_falls_back_to_24(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        captured = {}
        monkeypatch.setattr(
            services,
            "summarize_mcp_calls",
            lambda since_hours: captured.update(h=since_hours) or {"since_hours": since_hours, "calls": [], "totals": {}},
        )
        client.get("/api/mcp/calls?since=garbage")
        assert captured["h"] == 24
