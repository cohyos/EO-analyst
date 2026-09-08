"""Tests for eoa.mcp_servers.patents -- EPO OPS OAuth2 token handling + USPTO ODP search (+ the
retired PatentsView shim). All HTTP calls mocked; no real network access. The ODP fixtures below
follow the field names of the ODP OpenAPI spec (``PatentDataResponse``/``ApplicationMetaData``/
``Assignment`` in ``odp-common-base.yaml``, read 2026-09-07) -- not a recorded live response,
since no ``USPTO_ODP_API_KEY`` exists yet.

The EPO OPS biblio fixtures below (``BIBLIO_SINGLE_APPLICANT``/``BIBLIO_TWO_APPLICANTS``) *are*
trimmed real live responses (2026-09-08, ``EPO_OPS_KEY``/``EPO_OPS_SECRET`` verified live against
``https://ops.epo.org/3.2/...`` -- see ``docs/qa/content_review/PATENTS-OPS.md`` for the full
session and every CQL query verified live) -- US2024220012A1 and US2015168730A1, both real Elbit
Systems patents, redundant repeated CPC entries and non-English name variants trimmed for fixture
size but every field shape kept exactly as OPS returned it."""

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
    pt._epo_throttle_state.clear()
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

        def fake_get(url, *, params=None, headers=None, timeout_s=20.0, include_headers=False):
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
        assert "epo_ops_biblio" in names
        assert "patentsview_search" not in names


# ---------------------------------------------------------------------------
# EPO OPS CQL query construction (build_epo_query / _cql_term) -- live-verified 2026-09-08, see
# the module's own docstring above _cql_term for the full session summary.
# ---------------------------------------------------------------------------


class TestCqlTerm:
    def test_single_word_unquoted(self):
        assert pt._cql_term("elbit") == "elbit"

    def test_multiword_no_hyphen_is_quoted(self):
        assert pt._cql_term("Elbit Systems") == '"Elbit Systems"'

    def test_hyphenated_value_never_quoted_even_if_multiword(self):
        assert pt._cql_term("electro-optical") == "electro-optical"
        assert pt._cql_term("night-vision goggles") == "night-vision goggles"

    def test_blank_value(self):
        assert pt._cql_term("") == ""
        assert pt._cql_term("   ") == ""

    def test_strips_surrounding_whitespace(self):
        assert pt._cql_term("  elbit  ") == "elbit"


class TestBuildEpoQuery:
    def test_applicant_only(self):
        assert pt.build_epo_query(applicant="elbit") == "pa=elbit"

    def test_applicant_phrase_quoted(self):
        assert pt.build_epo_query(applicant="Elbit Systems") == 'pa="Elbit Systems"'

    def test_single_keyword(self):
        assert pt.build_epo_query(applicant="elbit", keywords=["infrared"]) == "pa=elbit and ta=infrared"

    def test_hyphenated_keyword_left_unquoted(self):
        q = pt.build_epo_query(applicant="elbit", keywords=["electro-optical"])
        assert q == "pa=elbit and ta=electro-optical"
        assert '"electro-optical"' not in q

    def test_multiple_keywords_ored_and_parenthesised(self):
        q = pt.build_epo_query(applicant="elbit", keywords=["electro-optical", "gimbal", "payload"])
        assert q == "pa=elbit and ta=(electro-optical or gimbal or payload)"

    def test_cpc_prefix_normalized(self):
        q = pt.build_epo_query(applicant="elbit", cpc="g01j 5")
        assert q == "pa=elbit and cpc=G01J5"

    def test_custom_field(self):
        assert pt.build_epo_query(keywords=["infrared"], field="ti") == "ti=infrared"

    def test_empty_keywords_list_omits_clause(self):
        assert pt.build_epo_query(applicant="elbit", keywords=["", "   "]) == "pa=elbit"

    def test_nothing_supplied_returns_empty_string(self):
        assert pt.build_epo_query() == ""

    def test_all_parts_combined(self):
        q = pt.build_epo_query(applicant="Elbit Systems", keywords=["infrared"], cpc="G01J5")
        assert q == 'pa="Elbit Systems" and ta=infrared and cpc=G01J5'


# ---------------------------------------------------------------------------
# EPO OPS throttling (X-Throttling-Control)
# ---------------------------------------------------------------------------


class TestThrottlingControl:
    def test_parses_multiple_services(self):
        header = "busy (images=green:100, inpadoc=green:45, other=green:1000, retrieval=amber:12, search=red:2)"
        parsed = pt._parse_throttling_control(header)
        assert parsed == {
            "images": "green",
            "inpadoc": "green",
            "other": "green",
            "retrieval": "amber",
            "search": "red",
        }

    def test_malformed_header_returns_empty(self):
        assert pt._parse_throttling_control("") == {}
        assert pt._parse_throttling_control("nonsense") == {}

    def test_record_and_wait_green_no_delay(self, monkeypatch: pytest.MonkeyPatch):
        sleeps: list[float] = []
        monkeypatch.setattr(pt, "_sleep", sleeps.append)
        pt._record_throttle_state({"x-throttling-control": "busy (search=green:15)"})
        pt._epo_throttle_wait("search")
        assert sleeps == []

    def test_record_and_wait_backs_off_when_not_green(self, monkeypatch: pytest.MonkeyPatch):
        sleeps: list[float] = []
        monkeypatch.setattr(pt, "_sleep", sleeps.append)
        pt._record_throttle_state({"x-throttling-control": "busy (search=red:2)"})
        pt._epo_throttle_wait("search")
        assert sleeps == [pt._THROTTLE_BACKOFF_S["red"]]

    def test_unknown_service_defaults_to_green(self, monkeypatch: pytest.MonkeyPatch):
        sleeps: list[float] = []
        monkeypatch.setattr(pt, "_sleep", sleeps.append)
        pt._epo_throttle_wait("some_new_service")
        assert sleeps == []

    def test_missing_headers_is_a_noop(self):
        pt._record_throttle_state(None)
        pt._record_throttle_state({})
        assert pt._epo_throttle_state == {}

    def test_epo_ops_search_records_throttle_state_from_response(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("EPO_OPS_KEY", "key")
        monkeypatch.setenv("EPO_OPS_SECRET", "secret")
        monkeypatch.setattr(
            pt, "http_post_form", lambda *a, **kw: {"status": 200, "json": {"access_token": "t", "expires_in": 1200}}
        )
        monkeypatch.setattr(
            pt,
            "http_get_json",
            lambda *a, **kw: {
                "status": 200,
                "json": {"ops:world-patent-data": {}},
                "headers": {"x-throttling-control": "busy (search=amber:9)"},
            },
        )
        pt.epo_ops_search("pa=elbit")
        assert pt._epo_throttle_state["search"] == "amber"

    def test_epo_ops_search_404_is_empty_result_not_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("EPO_OPS_KEY", "key")
        monkeypatch.setenv("EPO_OPS_SECRET", "secret")
        monkeypatch.setattr(
            pt, "http_post_form", lambda *a, **kw: {"status": 200, "json": {"access_token": "t", "expires_in": 1200}}
        )
        monkeypatch.setattr(
            pt, "http_get_json", lambda *a, **kw: {"status": 404, "json": None, "text": "no results"}
        )
        out = json.loads(pt.epo_ops_search("pa=elbit and ti=\"electro-optical\""))
        assert out == {"total_result_count": 0, "results": []}
        assert "error" not in out


# ---------------------------------------------------------------------------
# EPO OPS biblio (epo_ops_biblio / _extract_epo_biblio) -- fixtures are trimmed real live
# responses, see the module docstring at the top of this file.
# ---------------------------------------------------------------------------

BIBLIO_SINGLE_APPLICANT = {
    "ops:world-patent-data": {
        "exchange-documents": {
            "exchange-document": {
                "@system": "ops.epo.org",
                "@family-id": "83846750",
                "@country": "US",
                "@doc-number": "2024220012",
                "@kind": "A1",
                "bibliographic-data": {
                    "publication-reference": {
                        "document-id": [
                            {
                                "@document-id-type": "docdb",
                                "country": {"$": "US"},
                                "doc-number": {"$": "2024220012"},
                                "kind": {"$": "A1"},
                                "date": {"$": "20240704"},
                            },
                            {
                                "@document-id-type": "epodoc",
                                "doc-number": {"$": "US2024220012"},
                                "date": {"$": "20240704"},
                            },
                        ]
                    },
                    "classifications-ipcr": {
                        "classification-ipcr": [
                            {"@sequence": "1", "text": {"$": "G02B  27/    01            A I"}},
                            {"@sequence": "2", "text": {"$": "G06F   3/    01            A I"}},
                        ]
                    },
                    "patent-classifications": {
                        "patent-classification": [
                            {
                                "@sequence": "2",
                                "section": {"$": "G"},
                                "class": {"$": "02"},
                                "subclass": {"$": "B"},
                                "main-group": {"$": "27"},
                                "subgroup": {"$": "0093"},
                            },
                            {
                                "@sequence": "5",
                                "section": {"$": "G"},
                                "class": {"$": "06"},
                                "subclass": {"$": "F"},
                                "main-group": {"$": "3"},
                                "subgroup": {"$": "011"},
                            },
                        ]
                    },
                    "application-reference": {
                        "document-id": [
                            {
                                "@document-id-type": "docdb",
                                "country": {"$": "US"},
                                "doc-number": {"$": "202118557618"},
                                "kind": {"$": "A"},
                            },
                            {
                                "@document-id-type": "epodoc",
                                "doc-number": {"$": "US202118557618"},
                                "date": {"$": "20210427"},
                            },
                        ]
                    },
                    "priority-claims": {
                        "priority-claim": {
                            "@sequence": "1",
                            "@kind": "national",
                            "document-id": [
                                {
                                    "@document-id-type": "docdb",
                                    "country": {"$": "IL"},
                                    "doc-number": {"$": "2021050485"},
                                    "kind": {"$": "W"},
                                    "date": {"$": "20210427"},
                                },
                            ],
                        }
                    },
                    "parties": {
                        "applicants": {
                            "applicant": [
                                {
                                    "@sequence": "1",
                                    "@data-format": "epodoc",
                                    "applicant-name": {"name": {"$": "ELBIT SYSTEMS LTD [IL]"}},
                                },
                                {
                                    "@sequence": "1",
                                    "@data-format": "original",
                                    "applicant-name": {"name": {"$": "ELBIT SYSTEMS LTD"}},
                                },
                            ]
                        },
                        "inventors": {
                            "inventor": [
                                {
                                    "@sequence": "1",
                                    "@data-format": "epodoc",
                                    "inventor-name": {"name": {"$": "BEN-YISHAI RANI [IL]"}},
                                },
                                {
                                    "@sequence": "2",
                                    "@data-format": "epodoc",
                                    "inventor-name": {"name": {"$": "BENESH GIL [IL]"}},
                                },
                            ]
                        },
                    },
                    "invention-title": {
                        "$": "Optical see through (OST) head mounted display (HMD) system and method",
                        "@lang": "en",
                    },
                },
                "abstract": {
                    "@lang": "en",
                    "p": {"$": "A method for irradiating an image in an optical see-through (OST) HMD."},
                },
            }
        }
    }
}

#: Two applicants (Elbit Systems + Everysight) -- real live shape from US2015168730A1.
BIBLIO_TWO_APPLICANTS_APPLICANT_BLOCK = {
    "applicant": [
        {"@sequence": "1", "@data-format": "epodoc", "applicant-name": {"name": {"$": "ELBIT SYSTEMS LTD [IL]"}}},
        {"@sequence": "2", "@data-format": "epodoc", "applicant-name": {"name": {"$": "EVERYSIGHT LTD [IL]"}}},
        {"@sequence": "1", "@data-format": "original", "applicant-name": {"name": {"$": "Elbit Systems Ltd."}}},
    ]
}


class TestPubNumberToDocdb:
    def test_with_kind(self):
        assert pt._pub_number_to_docdb("US2024220012A1") == ("US", "2024220012", "A1")

    def test_without_kind_defaults_to_a(self):
        assert pt._pub_number_to_docdb("US2024220012") == ("US", "2024220012", "A")

    def test_lowercase_country_normalized(self):
        assert pt._pub_number_to_docdb("us2024220012a1") == ("US", "2024220012", "A1")

    def test_unrecognized_format_returns_none(self):
        assert pt._pub_number_to_docdb("not-a-pub-number") is None
        assert pt._pub_number_to_docdb("") is None


class TestExtractEpoBiblio:
    def test_full_extraction(self):
        row = pt._extract_epo_biblio(BIBLIO_SINGLE_APPLICANT)
        assert row["pub_number"] == "US2024220012A1"
        assert row["kind"] == "A1"
        assert row["country"] == "US"
        assert row["title"] == "Optical see through (OST) head mounted display (HMD) system and method"
        assert row["abstract"].startswith("A method for irradiating")
        assert row["applicants"] == ["ELBIT SYSTEMS LTD [IL]"]
        assert row["assignees"] == row["applicants"]
        assert row["inventors"] == ["BEN-YISHAI RANI [IL]", "BENESH GIL [IL]"]
        assert row["cpc"] == ["G02B27/0093", "G06F3/011"]
        assert row["ipc"] == ["G02B27/01", "G06F3/01"]
        assert row["family_id"] == "83846750"
        assert row["application_number"] == "US202118557618"
        assert row["priority_date"] == dt.date(2021, 4, 27)
        assert row["filing_date"] == dt.date(2021, 4, 27)
        assert row["publication_date"] == dt.date(2024, 7, 4)
        assert row["url"] == "https://patents.google.com/patent/US2024220012A1/en"

    def test_two_applicants_deduped_and_epodoc_preferred(self):
        names = pt._extract_party_names(
            BIBLIO_TWO_APPLICANTS_APPLICANT_BLOCK, "applicant", "applicant-name"
        )
        assert names == ["ELBIT SYSTEMS LTD [IL]", "EVERYSIGHT LTD [IL]"]

    def test_missing_branch_returns_empty_shape_not_raises(self):
        row = pt._extract_epo_biblio({"unexpected": "shape"}, fallback_pub_number="US123A1")
        assert row["pub_number"] == "US123A1"
        assert row["title"] == ""
        assert row["applicants"] == []
        assert row["cpc"] == []

    def test_no_documents_at_all(self):
        row = pt._extract_epo_biblio({"ops:world-patent-data": {"exchange-documents": {}}})
        assert row["pub_number"] is None
        assert row["title"] == ""


class TestEpoOpsBiblio:
    def test_not_configured_without_credentials(self):
        out = json.loads(pt.epo_ops_biblio("US2024220012A1"))
        assert out["error"] == "not_configured"

    def test_unrecognized_pub_number_is_reported_not_raised(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("EPO_OPS_KEY", "key")
        monkeypatch.setenv("EPO_OPS_SECRET", "secret")
        monkeypatch.setattr(
            pt, "http_post_form", lambda *a, **kw: {"status": 200, "json": {"access_token": "t", "expires_in": 1200}}
        )
        out = json.loads(pt.epo_ops_biblio("!!!not-valid!!!"))
        assert "unrecognized pub_number" in out["error"]

    def test_success_fetches_docdb_url_and_returns_parsed_row(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("EPO_OPS_KEY", "key")
        monkeypatch.setenv("EPO_OPS_SECRET", "secret")
        monkeypatch.setattr(
            pt, "http_post_form", lambda *a, **kw: {"status": 200, "json": {"access_token": "tok", "expires_in": 1200}}
        )
        captured: dict = {}

        def fake_get(url, *, headers=None, timeout_s=20.0, include_headers=False):
            captured["url"] = url
            captured["headers"] = headers
            return {"status": 200, "json": BIBLIO_SINGLE_APPLICANT, "headers": {}}

        monkeypatch.setattr(pt, "http_get_json", fake_get)
        out = json.loads(pt.epo_ops_biblio("US2024220012A1"))
        assert captured["url"] == "https://ops.epo.org/3.2/rest-services/published-data/publication/docdb/US.2024220012.A1/biblio"
        assert captured["headers"]["Authorization"] == "Bearer tok"
        assert out["pub_number"] == "US2024220012A1"
        assert out["family_id"] == "83846750"

    def test_404_reports_not_found(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("EPO_OPS_KEY", "key")
        monkeypatch.setenv("EPO_OPS_SECRET", "secret")
        monkeypatch.setattr(
            pt, "http_post_form", lambda *a, **kw: {"status": 200, "json": {"access_token": "t", "expires_in": 1200}}
        )
        monkeypatch.setattr(pt, "http_get_json", lambda *a, **kw: {"status": 404, "json": None, "text": "nope"})
        out = json.loads(pt.epo_ops_biblio("US9999999A1"))
        assert out == {"error": "not_found", "pub_number": "US9999999A1"}

    def test_other_http_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("EPO_OPS_KEY", "key")
        monkeypatch.setenv("EPO_OPS_SECRET", "secret")
        monkeypatch.setattr(
            pt, "http_post_form", lambda *a, **kw: {"status": 200, "json": {"access_token": "t", "expires_in": 1200}}
        )
        monkeypatch.setattr(pt, "http_get_json", lambda *a, **kw: {"status": 500, "json": None, "text": "err"})
        out = json.loads(pt.epo_ops_biblio("US2024220012A1"))
        assert out["error"] == "EPO OPS returned HTTP 500"
