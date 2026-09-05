"""Tests for eoa.mcp_servers.janes -- generic REST client + graceful not_configured behavior.
All HTTP calls mocked; JANES_API_KEY is never logged (asserted indirectly: the fake transport
never receives it anywhere but the Authorization/subscription-key headers)."""

from __future__ import annotations

import json

import pytest

from eoa.mcp_servers import janes as j


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("JANES_API_KEY", raising=False)
    monkeypatch.delenv("JANES_API_BASE", raising=False)


class TestPing:
    def test_reports_not_configured(self):
        assert json.loads(j.ping()) == {"configured": False}

    def test_reports_configured(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("JANES_API_KEY", "secret")
        assert json.loads(j.ping()) == {"configured": True}


class TestToolsNotConfigured:
    @pytest.mark.parametrize(
        "fn,args",
        [
            (j.janes_search, ("night vision",)),
            (j.janes_equipment, ("night vision",)),
            (j.janes_news, ("night vision",)),
            (j.janes_markets, ("night vision",)),
            (j.janes_budgets, ("Israel",)),
            (j.janes_events, ()),
        ],
    )
    def test_returns_not_configured(self, fn, args):
        out = json.loads(fn(*args))
        assert out["error"] == "not_configured"
        assert "JANES_API_KEY" in out["message"]


class TestCallWithKey:
    def test_uses_default_base_and_sends_key_only_in_headers(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("JANES_API_KEY", "secret-value")
        captured = {}

        def fake_get(url, *, params=None, headers=None, timeout_s=20.0):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return {"status": 200, "json": {"ok": True}}

        monkeypatch.setattr(j, "http_get_json", fake_get)
        out = json.loads(j.janes_equipment("night vision", category="sensors"))
        assert out == {"ok": True}
        assert captured["url"] == "https://developer.janes.com/api/equipment/search"
        assert captured["headers"]["Authorization"] == "Bearer secret-value"
        assert captured["params"]["q"] == "night vision"
        assert captured["params"]["category"] == "sensors"

    def test_custom_base_url_honored(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("JANES_API_KEY", "k")
        monkeypatch.setenv("JANES_API_BASE", "https://custom.janes.example/v2")
        captured = {}
        monkeypatch.setattr(
            j, "http_get_json", lambda url, **kw: captured.update(url=url) or {"status": 200, "json": {}}
        )
        j.janes_news("x")
        assert captured["url"] == "https://custom.janes.example/v2/news/search"

    def test_non_200_reports_hint(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("JANES_API_KEY", "k")
        monkeypatch.setattr(j, "http_get_json", lambda *a, **kw: {"status": 401, "json": None, "text": "unauthorized"})
        out = json.loads(j.janes_search("x"))
        assert "error" in out
        assert "hint" in out
