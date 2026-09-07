"""Tests for eoa.mcp_servers.patents -- EPO OPS OAuth2 token handling + USPTO ODP search (+ the
retired PatentsView shim). All HTTP calls mocked; no real network access. The ODP fixtures below
follow the field names of the ODP OpenAPI spec (``PatentDataResponse``/``ApplicationMetaData``/
``Assignment`` in ``odp-common-base.yaml``, read 2026-09-07) -- not a recorded live response,
since no ``USPTO_ODP_API_KEY`` exists yet."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from eoa.mcp_servers import patents as pt


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    for name in (
        "EPO_OPS_KEY",
        "EPO_OPS_SECRET",
        "USPTO_ODP_API_KEY",
        "PATENTSVIEW_API_KEY",
        "PATENTSVIEW_API_BASE",
    ):
        monkeypatch.delenv(name, raising=False)
    pt._epo_token = None
    pt._epo_token_expiry = 0.0
    pt._odp_last_call_at = 0.0
    pt._patentsview_deprecation_logged = False
    monkeypatch.setattr(pt, "_sleep", lambda s: None)


class TestPing:
    def test_reports_all_unconfigured(self):
        out = json.loads(pt.ping())
        assert out == {
            "epo_ops_configured": False,
            "uspto_odp_configured": False,
            "patentsview_configured": False,
        }

    def test_odp_key_reported(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("USPTO_ODP_API_KEY", "k")
        assert json.loads(pt.ping())["uspto_odp_configured"] is True

    def test_patentsview_key_never_counts_as_configured(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("PATENTSVIEW_API_KEY", "old")
        out = json.loads(pt.ping())
        assert out["patentsview_configured"] is False
        assert out["uspto_odp_configured"] is False


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
        # shared row keys with uspto_odp_search
        assert out["results"][0]["pub_number"] == "US123456A1"
        assert out["results"][0]["url"] == "https://patents.google.com/patent/US123456A1/en"

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


# ---------------------------------------------------------------------------
# USPTO Open Data Portal
# ---------------------------------------------------------------------------

#: One ``patentFileWrapperDataBag`` entry shaped after the ODP spec examples (granted case).
ODP_GRANTED = {
    "applicationNumberText": "14104993",
    "applicationMetaData": {
        "inventionTitle": "HETEROJUNCTION BIPOLAR TRANSISTOR",
        "filingDate": "2012-12-19",
        "grantDate": "2016-06-07",
        "patentNumber": "9362380",
        "earliestPublicationNumber": "US 2014-0167116 A1",
        "earliestPublicationDate": "2014-06-19",
        "publicationDateBag": ["2014-06-19"],
        "publicationCategoryBag": ["Granted/Issued", "Pre-Grant Publications - PGPub"],
        "applicationStatusDescriptionText": "Patented Case",
        "cpcClassificationBag": ["H01L29/66325", "H01L27/0623"],
        "firstApplicantName": "STMicroelectronics S.A.",
        "applicantBag": [{"applicantNameText": "STMicroelectronics S.A."}],
        "inventorBag": [{"inventorNameText": "Pascal Chevalier"}],
    },
    "assignmentBag": [
        {
            "reelAndFrameNumber": "60620/769",
            "assigneeBag": [
                {"assigneeNameText": "STMICROELECTRONICS SA"},
                {"assigneeNameText": "STMicroelectronics S.A."},
            ],
        }
    ],
}

#: Pending application: no patent number yet, publication number only.
ODP_PENDING = {
    "applicationNumberText": "18123456",
    "applicationMetaData": {
        "inventionTitle": "Digital pixel readout circuit for an infrared focal plane array",
        "filingDate": "2025-02-03",
        "earliestPublicationNumber": "US 2026-0012345 A1",
        "earliestPublicationDate": "2026-01-15",
        "cpcClassificationBag": ["H04N25/771"],
        "firstApplicantName": "Acme Sensors Inc.",
    },
}

#: Unpublished application: application number is the only identifier.
ODP_UNPUBLISHED = {
    "applicationNumberText": "19000001",
    "applicationMetaData": {"inventionTitle": "Secret", "filingDate": "2026-08-01"},
}


class TestBuildOdpSearchBody:
    def test_free_text_only(self):
        body = pt.build_odp_search_body("night vision", limit=20)
        assert body["q"] == "(night vision)"
        assert body["pagination"] == {"offset": 0, "limit": 20}
        assert body["sort"] == [{"field": "applicationMetaData.filingDate", "order": "desc"}]
        assert body["fields"] == ["applicationNumberText", "applicationMetaData", "assignmentBag"]

    def test_assignee_cpc_and_date_clauses(self):
        body = pt.build_odp_search_body(
            "infrared",
            assignee='Elbit "Systems"',
            cpc="g01s 7/48",
            date_from="2025-01-01",
            today=dt.date(2026, 9, 7),
        )
        q = body["q"]
        assert q.startswith("(infrared) AND ")
        assert 'applicationMetaData.firstApplicantName:"Elbit Systems"' in q  # quotes stripped, phrase kept
        assert 'applicationMetaData.applicantBag.applicantNameText:"Elbit Systems"' in q
        assert 'assignmentBag.assigneeBag.assigneeNameText:"Elbit Systems"' in q
        assert "applicationMetaData.cpcClassificationBag:G01S7/48*" in q
        assert "applicationMetaData.earliestPublicationDate:[2025-01-01 TO 2026-09-07]" in q
        assert "applicationMetaData.grantDate:[2025-01-01 TO 2026-09-07]" in q

    def test_limit_is_clamped_to_odp_max(self):
        assert pt.build_odp_search_body("x", limit=500)["pagination"]["limit"] == pt.USPTO_ODP_MAX_LIMIT
        assert pt.build_odp_search_body("x", limit=0)["pagination"]["limit"] == 1

    def test_bad_date_raises_value_error(self):
        with pytest.raises(ValueError):
            pt.build_odp_search_body("x", date_from="last year")


class TestNormalizeOdpRecord:
    def test_granted_record_prefers_patent_number(self):
        row = pt.normalize_odp_record(ODP_GRANTED)
        assert row["pub_number"] == "US9362380"
        assert row["kind"] is None
        assert row["title"] == "HETEROJUNCTION BIPOLAR TRANSISTOR"
        assert row["cpc"] == ["H01L29/66325", "H01L27/0623"]
        assert row["publication_date"] == "2014-06-19"
        assert row["grant_date"] == "2016-06-07"
        assert row["filing_date"] == "2012-12-19"
        assert row["url"] == "https://patents.google.com/patent/US9362380/en"
        assert row["earliest_publication_number"] == "US20140167116A1"
        assert row["status"] == "Patented Case"
        assert row["inventors"] == ["Pascal Chevalier"]
        # applicant first, recorded assignees deduped case-insensitively
        assert row["assignees"] == ["STMicroelectronics S.A.", "STMICROELECTRONICS SA"]

    def test_pending_record_uses_compacted_publication_number(self):
        row = pt.normalize_odp_record(ODP_PENDING)
        assert row["pub_number"] == "US20260012345A1"
        assert row["kind"] == "A1"
        assert row["assignees"] == ["Acme Sensors Inc."]  # firstApplicantName fallback
        assert row["grant_date"] is None
        assert row["publication_date"] == "2026-01-15"

    def test_unpublished_record_falls_back_to_application_number(self):
        row = pt.normalize_odp_record(ODP_UNPUBLISHED)
        assert row["pub_number"] == "US19000001"
        assert row["url"] == "https://patentcenter.uspto.gov/applications/19000001"
        assert row["publication_date"] is None

    def test_record_without_any_identifier_is_dropped(self):
        assert pt.normalize_odp_record({"applicationMetaData": {"inventionTitle": "x"}}) is None
        assert (
            pt.normalize_odp_record({"applicationNumberText": "1", "applicationMetaData": "junk"})[
                "pub_number"
            ]
            == "US1"
        )


class TestUsptoOdpSearch:
    def test_not_configured_without_key(self):
        out = json.loads(pt.uspto_odp_search("night vision"))
        assert out["error"] == "not_configured"
        assert "USPTO_ODP_API_KEY" in out["message"]

    def test_patentsview_key_alone_is_not_configured_and_logs_once(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("PATENTSVIEW_API_KEY", "old-key")
        warnings: list[str] = []
        monkeypatch.setattr(pt.log, "warning", lambda event, **kw: warnings.append(event))
        assert json.loads(pt.uspto_odp_search("x"))["error"] == "not_configured"
        assert json.loads(pt.uspto_odp_search("y"))["error"] == "not_configured"
        json.loads(pt.ping())
        assert warnings == ["patentsview_deprecated"]  # exactly once per process

    def test_success_posts_json_with_api_key_header(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("USPTO_ODP_API_KEY", "odp-key")
        captured: dict = {}

        def fake_post(url, *, json_body=None, headers=None, timeout_s=20.0):
            captured["url"], captured["body"], captured["headers"] = url, json_body, headers
            return {
                "status": 200,
                "json": {"count": 2, "patentFileWrapperDataBag": [ODP_GRANTED, ODP_PENDING]},
                "text": None,
            }

        monkeypatch.setattr(pt, "http_post_json", fake_post)
        out = json.loads(pt.uspto_odp_search("transistor", assignee="STMicroelectronics", limit=5))
        assert captured["url"] == pt.USPTO_ODP_SEARCH_URL
        assert captured["headers"] == {"X-API-KEY": "odp-key"}
        assert captured["body"]["pagination"]["limit"] == 5
        assert '"STMicroelectronics"' in captured["body"]["q"]
        assert out["total_count"] == 2
        assert [r["pub_number"] for r in out["results"]] == ["US9362380", "US20260012345A1"]
        assert out["query"] == captured["body"]["q"]

    def test_404_is_an_empty_result_not_an_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("USPTO_ODP_API_KEY", "odp-key")
        monkeypatch.setattr(
            pt,
            "http_post_json",
            lambda *a, **kw: {"status": 404, "json": {"code": 404, "error": "Not Found"}, "text": None},
        )
        out = json.loads(pt.uspto_odp_search("nothing-matches"))
        assert out == {"total_count": 0, "results": [], "query": "(nothing-matches)"}

    def test_401_reports_rejected_key(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("USPTO_ODP_API_KEY", "bad")
        monkeypatch.setattr(
            pt,
            "http_post_json",
            lambda *a, **kw: {"status": 401, "json": {"message": "Unauthorized"}, "text": None},
        )
        out = json.loads(pt.uspto_odp_search("x"))
        assert "rejected the API key" in out["error"]
        assert "bad" not in json.dumps(out)  # never echo the key

    def test_429_retried_once_after_five_seconds(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("USPTO_ODP_API_KEY", "odp-key")
        sleeps: list[float] = []
        monkeypatch.setattr(pt, "_sleep", sleeps.append)
        statuses = iter([429, 200])
        monkeypatch.setattr(
            pt,
            "http_post_json",
            lambda *a, **kw: {
                "status": next(statuses),
                "json": {"count": 0, "patentFileWrapperDataBag": []},
                "text": None,
            },
        )
        out = json.loads(pt.uspto_odp_search("x"))
        assert out["results"] == []
        assert pt._ODP_429_RETRY_DELAY_S in sleeps

    def test_persistent_429_surfaces_as_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("USPTO_ODP_API_KEY", "odp-key")
        calls = {"n": 0}

        def always_429(*a, **kw):
            calls["n"] += 1
            return {"status": 429, "json": None, "text": "Too Many Requests"}

        monkeypatch.setattr(pt, "http_post_json", always_429)
        out = json.loads(pt.uspto_odp_search("x"))
        assert "429" in out["error"]
        assert calls["n"] == 2  # one retry, never a storm

    def test_other_http_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("USPTO_ODP_API_KEY", "odp-key")
        monkeypatch.setattr(
            pt, "http_post_json", lambda *a, **kw: {"status": 500, "json": None, "text": "err"}
        )
        out = json.loads(pt.uspto_odp_search("x"))
        assert out["error"] == "USPTO ODP returned HTTP 500"

    def test_invalid_date_from_is_reported_not_raised(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("USPTO_ODP_API_KEY", "odp-key")
        out = json.loads(pt.uspto_odp_search("x", date_from="yesterday"))
        assert out["error"].startswith("invalid date_from")

    def test_calls_are_spaced_by_min_interval(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("USPTO_ODP_API_KEY", "odp-key")
        sleeps: list[float] = []
        monkeypatch.setattr(pt, "_sleep", sleeps.append)
        monkeypatch.setattr(
            pt, "http_post_json", lambda *a, **kw: {"status": 200, "json": {"count": 0}, "text": None}
        )
        pt.uspto_odp_search("a")
        pt.uspto_odp_search("b")  # immediately after -> throttled
        assert sleeps and 0 < sleeps[-1] <= pt._ODP_MIN_INTERVAL_S


class TestPatentsviewShim:
    def test_always_not_configured_pointing_at_odp(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("PATENTSVIEW_API_KEY", "pvkey")
        out = json.loads(pt.patentsview_search("infrared", assignee="Acme"))
        assert out["error"] == "not_configured"
        assert "USPTO_ODP_API_KEY" in out["message"]
        assert "retired" in out["message"]

    def test_not_registered_as_an_mcp_tool(self):
        import asyncio

        names = {t.name for t in asyncio.run(pt.mcp.list_tools())}
        assert "uspto_odp_search" in names and "epo_ops_search" in names and "ping" in names
        assert "patentsview_search" not in names
