"""Tests for eoa.mcp_servers.procurement -- all HTTP calls mocked via `_common.http_get_json`/
`http_post_json`; nothing here touches the network. See scripts-level live checks (this task's
report) for the actually-confirmed-live SAM.gov/USAspending/Federal Register/DSCA calls.
"""

from __future__ import annotations

import json

import pytest

from eoa.mcp_servers import procurement as p


@pytest.fixture(autouse=True)
def _clear_keys(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SAM_GOV_API_KEY", raising=False)
    monkeypatch.delenv("CONGRESS_GOV_API_KEY", raising=False)


class TestPing:
    def test_ping(self):
        assert p.ping() == "pong"


class TestSamGovSearch:
    def test_not_configured_without_key(self):
        out = json.loads(p.sam_gov_search(keyword="night vision"))
        assert out["error"] == "not_configured"
        assert "SAM_GOV_API_KEY" in out["message"]

    def test_success(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SAM_GOV_API_KEY", "test-key")
        captured = {}

        def fake_get(url, *, params=None, headers=None, timeout_s=20.0):
            captured["url"] = url
            captured["params"] = params
            return {
                "status": 200,
                "json": {
                    "totalRecords": 1,
                    "opportunitiesData": [
                        {
                            "noticeId": "N1",
                            "title": "Night Vision Goggles",
                            "type": "Solicitation",
                            "postedDate": "2026-01-01",
                            "responseDeadLine": "2026-02-01",
                            "naicsCode": "333999",
                            "classificationCode": "5855",
                            "fullParentPathName": "DEPT OF DEFENSE",
                            "uiLink": "https://sam.gov/opp/N1",
                        }
                    ],
                },
            }

        monkeypatch.setattr(p, "http_get_json", fake_get)
        out = json.loads(p.sam_gov_search(keyword="night vision", limit=5))
        assert out["total_records"] == 1
        assert out["opportunities"][0]["notice_id"] == "N1"
        assert captured["params"]["api_key"] == "test-key"
        assert captured["params"]["title"] == "night vision"

    def test_non_200_returns_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SAM_GOV_API_KEY", "test-key")
        monkeypatch.setattr(p, "http_get_json", lambda *a, **kw: {"status": 500, "json": None, "text": "boom"})
        out = json.loads(p.sam_gov_search(keyword="x"))
        assert "error" in out


class TestUsaspendingAwardsByPsc:
    def test_uses_config_default_psc_codes(self, monkeypatch: pytest.MonkeyPatch):
        captured = {}

        def fake_post(url, *, json_body=None, headers=None, timeout_s=20.0):
            captured["body"] = json_body
            return {"status": 200, "json": {"results": [{"Award ID": "W1"}]}, "text": None}

        monkeypatch.setattr(p, "http_post_json", fake_post)
        out = json.loads(p.usaspending_awards_by_psc())
        assert out["awards"] == [{"Award ID": "W1"}]
        assert captured["body"]["filters"]["psc_codes"] == ["5855", "6650", "1270", "5840", "5841"]

    def test_explicit_psc_codes_override_default(self, monkeypatch: pytest.MonkeyPatch):
        captured = {}
        monkeypatch.setattr(
            p, "http_post_json", lambda url, **kw: captured.update(body=kw["json_body"]) or {"status": 200, "json": {"results": []}, "text": None}
        )
        p.usaspending_awards_by_psc(psc_codes=["9999"])
        assert captured["body"]["filters"]["psc_codes"] == ["9999"]

    def test_error_status(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(p, "http_post_json", lambda *a, **kw: {"status": 422, "json": None, "text": "bad filter"})
        out = json.loads(p.usaspending_awards_by_psc())
        assert "error" in out


class TestFederalRegisterSearch:
    def test_success(self, monkeypatch: pytest.MonkeyPatch):
        def fake_get(url, *, params=None, headers=None, timeout_s=20.0):
            assert params["conditions[term]"] == "infrared seeker"
            return {
                "status": 200,
                "json": {
                    "count": 3,
                    "results": [
                        {
                            "title": "Arms Sales Notification",
                            "type": "Notice",
                            "abstract": "x" * 900,
                            "publication_date": "2026-01-26",
                            "agencies": [{"name": "Defense Department"}],
                            "html_url": "https://federalregister.gov/d/1",
                        }
                    ],
                },
            }

        monkeypatch.setattr(p, "http_get_json", fake_get)
        out = json.loads(p.federal_register_search("infrared seeker", per_page=1))
        assert out["count"] == 3
        assert len(out["results"][0]["abstract"]) == 500
        assert out["results"][0]["agencies"] == ["Defense Department"]


class TestCongressGovSearch:
    def test_not_configured_without_key(self):
        out = json.loads(p.congress_gov_search("NDAA"))
        assert out["error"] == "not_configured"

    def test_success(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CONGRESS_GOV_API_KEY", "k")
        monkeypatch.setattr(
            p,
            "http_get_json",
            lambda *a, **kw: {
                "status": 200,
                "json": {"bills": [{"title": "NDAA FY26", "number": "1234", "type": "HR", "congress": 119}]},
            },
        )
        out = json.loads(p.congress_gov_search("NDAA"))
        assert out["bills"][0]["number"] == "1234"


class TestDscaMajorArmsSalesParsing:
    def test_parses_matching_links(self):
        html = (
            '<a href="/press-media/major-arms-sales/foo">Country X - Widget Sale</a>'
            '<a href="/other/page">Unrelated Link Text Here</a>'
        )
        items = p._parse_dsca_listing(html, keyword="", limit=10)
        assert len(items) == 1
        assert items[0]["title"] == "Country X - Widget Sale"
        assert items[0]["url"].startswith("https://www.dsca.mil/")

    def test_keyword_filter(self):
        html = '<a href="/press-media/major-arms-sales/foo">Country X - Widget Sale</a>'
        assert p._parse_dsca_listing(html, keyword="nomatch", limit=10) == []

    def test_non_200_returns_error_with_hint(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(p, "http_get_json", lambda *a, **kw: {"status": 403, "json": None, "text": "denied"})
        out = json.loads(p.dsca_major_arms_sales())
        assert out["error"]
        assert "hint" in out
