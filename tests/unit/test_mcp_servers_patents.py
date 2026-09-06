"""Tests for eoa.mcp_servers.patents -- EPO OPS OAuth2 token handling + PatentsView search.
All HTTP calls mocked; no real network access."""

from __future__ import annotations

import json

import pytest

from eoa.mcp_servers import patents as pt


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("EPO_OPS_KEY", raising=False)
    monkeypatch.delenv("EPO_OPS_SECRET", raising=False)
    monkeypatch.delenv("PATENTSVIEW_API_KEY", raising=False)
    monkeypatch.delenv("PATENTSVIEW_API_BASE", raising=False)
    pt._epo_token = None
    pt._epo_token_expiry = 0.0


class TestPing:
    def test_reports_both_unconfigured(self):
        out = json.loads(pt.ping())
        assert out == {"epo_ops_configured": False, "patentsview_configured": False}


class TestEpoOpsSearch:
    def test_not_configured_without_credentials(self):
        out = json.loads(pt.epo_ops_search("ti=test"))
        assert out["error"] == "not_configured"
        assert "EPO_OPS_KEY" in out["message"]

    def test_token_fetch_and_search(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("EPO_OPS_KEY", "key")
        monkeypatch.setenv("EPO_OPS_SECRET", "secret")

        def fake_post_form(url, *, data=None, headers=None, timeout_s=20.0):
            assert url == pt.EPO_OPS_TOKEN_URL
            assert data == {"grant_type": "client_credentials"}
            assert headers["Authorization"].startswith("Basic ")
            return {"status": 200, "json": {"access_token": "tok123", "expires_in": 1200}, "text": None}

        def fake_get(url, *, params=None, headers=None, timeout_s=20.0):
            assert headers["Authorization"] == "Bearer tok123"
            return {
                "status": 200,
                "json": {
                    "ops:world-patent-data": {
                        "ops:biblio-search": {
                            "@total-result-count": "1",
                            "ops:search-result": {
                                "ops:publication-reference": {
                                    "document-id": {
                                        "country": {"$": "US"},
                                        "doc-number": {"$": "123456"},
                                        "kind": {"$": "A1"},
                                    }
                                }
                            },
                        }
                    }
                },
            }

        monkeypatch.setattr(pt, "http_post_form", fake_post_form)
        monkeypatch.setattr(pt, "http_get_json", fake_get)
        out = json.loads(pt.epo_ops_search('ti="night vision"'))
        assert out["total_result_count"] == 1
        assert out["results"][0]["doc_number"] == "123456"

    def test_token_cached_across_calls(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("EPO_OPS_KEY", "key")
        monkeypatch.setenv("EPO_OPS_SECRET", "secret")
        calls = {"token": 0}

        def fake_post_form(url, **kw):
            calls["token"] += 1
            return {"status": 200, "json": {"access_token": "tok", "expires_in": 1200}, "text": None}

        monkeypatch.setattr(pt, "http_post_form", fake_post_form)
        monkeypatch.setattr(
            pt, "http_get_json", lambda *a, **kw: {"status": 200, "json": {"ops:world-patent-data": {}}}
        )
        pt.epo_ops_search("q1")
        pt.epo_ops_search("q2")
        assert calls["token"] == 1  # second call reused the cached token

    def test_auth_failure_returns_not_configured(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("EPO_OPS_KEY", "key")
        monkeypatch.setenv("EPO_OPS_SECRET", "secret")
        monkeypatch.setattr(
            pt, "http_post_form", lambda *a, **kw: {"status": 401, "json": None, "text": "<error/>"}
        )
        out = json.loads(pt.epo_ops_search("q"))
        assert out["error"] == "not_configured"

    def test_malformed_response_does_not_raise(self):
        assert pt._extract_epo_results({"unexpected": "shape"}) == {
            "total_result_count": None,
            "results": [],
            "raw": {"unexpected": "shape"},
        }


class TestPatentsviewSearch:
    def test_not_configured_without_key(self):
        out = json.loads(pt.patentsview_search("night vision"))
        assert out["error"] == "not_configured"

    def test_success(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("PATENTSVIEW_API_KEY", "pvkey")
        captured = {}

        def fake_get(url, *, params=None, headers=None, timeout_s=20.0):
            captured["url"] = url
            captured["headers"] = headers
            return {"status": 200, "json": {"total_hits": 2, "patents": [{"patent_id": "123"}]}}

        monkeypatch.setattr(pt, "http_get_json", fake_get)
        out = json.loads(pt.patentsview_search("infrared", assignee="Acme"))
        assert out["total_hits"] == 2
        assert captured["headers"]["X-Api-Key"] == "pvkey"

    def test_non_200(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("PATENTSVIEW_API_KEY", "pvkey")
        monkeypatch.setattr(
            pt, "http_get_json", lambda *a, **kw: {"status": 500, "json": None, "text": "err"}
        )
        out = json.loads(pt.patentsview_search("x"))
        assert "error" in out
