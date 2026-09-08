"""Tests for eoa.tenders.scan (section 5.2 / FR-5.2) -- pure logic + parsing, no DB/LLM/network.

Every DB- or network-touching function is monkeypatched at the module level (mirroring
tests/unit/test_conferences.py's stubbing style).
"""

from __future__ import annotations

import datetime as dt
import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.llm.schemas.tenders import TenderExtract
from eoa.tenders.scan import (
    DEFAULT_DEFENCE_CONTEXT_SIGNALS,
    DEFAULT_NEGATIVE_KEYWORDS,
    NOTICE_MAX_AGE_DAYS,
    RELEVANCE_MIN_ACCEPT,
    NoticeRaw,
    TenderSource,
    TenderStats,
    _apply_domain_country_fallback,
    _apply_extraction_to_notice,
    _archive_stale_closed,
    _collect_source_notices,
    _country_from_domain,
    _fetch_api_json,
    _fetch_notice_text,
    _gate_reject_reason,
    _has_defence_context,
    _has_procurement_signal,
    _initial_status,
    _is_denylisted_domain,
    _matches_keywords,
    _negative_keyword_penalty,
    _parse_contracts_finder,
    _parse_generic_json_list,
    _parse_generic_ocds,
    _parse_search_hits,
    _parse_ted_notices,
    _passes_gate,
    _relevance_score_for,
    _transition_closed,
    _within_window,
    load_defence_context_signals,
    load_deny_domains,
    load_negative_keywords,
    load_procurement_signals,
    load_tender_sources,
    scan_tenders,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tenders"

DOMAIN_KEYWORDS = [
    "electro-optical",
    "infrared",
    "targeting pod",
    "thermal imaging",
    "counter-uas",
    "surveillance camera",
]
PROCUREMENT_SIGNALS = ["tender", "RFP", "RFI", "sources sought", "request for information", "מכרז"]


def _ted_source() -> TenderSource:
    return TenderSource(
        id="ted_eu",
        name="TED",
        kind="api_json",
        country="EU",
        url="https://api.ted.europa.eu/v3/notices/search",
        method="POST",
        keywords=DOMAIN_KEYWORDS,
        verified=True,
    )


def _cf_source() -> TenderSource:
    return TenderSource(
        id="uk_contracts_finder",
        name="UK Contracts Finder",
        kind="api_json",
        country="UK",
        url="https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search",
        keywords=DOMAIN_KEYWORDS,
        verified=True,
    )


def _search_source(**overrides) -> TenderSource:
    base = dict(
        id="rfi_rfp_news",
        name="RFI/RFP news",
        kind="search",
        country="other",
        queries=["request for information electro-optical defense"],
        keywords=DOMAIN_KEYWORDS,
        verified=True,
    )
    base.update(overrides)
    return TenderSource(**base)


def _extract(**overrides) -> TenderExtract:
    # F24: relevance=6 (the new accept floor) and notice_type="tender" (a valid type) by default,
    # so a test that overrides neither exercises the "clears the gate, gets inserted" path; a test
    # of a specific rejection reason overrides just that one field.
    base = dict(
        relevant=True,
        relevance=6,
        summary_he="סיכום",
        matched_terms=["infrared"],
        entities=[],
        confidence=0.8,
        notice_type="tender",
    )
    base.update(overrides)
    return TenderExtract(**base)


# --------------------------------------------------------------------------
# config loading
# --------------------------------------------------------------------------


class TestLoadTenderSources:
    def test_loads_real_config(self):
        """config/tenders.yaml itself must parse into valid TenderSource rows."""
        sources = load_tender_sources()
        assert len(sources) >= 10
        ids = {s.id for s in sources}
        assert "ted_eu" in ids
        assert "uk_contracts_finder" in ids
        for s in sources:
            assert s.kind in ("api_json", "rss", "html", "search")
            assert s.keywords, f"{s.id} has no keywords"

    def test_verified_sources_flagged_correctly(self):
        sources = {s.id: s for s in load_tender_sources()}
        assert sources["ted_eu"].verified is True
        assert sources["uk_contracts_finder"].verified is True
        assert sources["sam_gov_api"].verified is False
        assert sources["il_mod"].verified is False

    def test_a15_new_verified_keyless_sources_present(self):
        """A15 (docs/TENDER_PORTALS.md): the global portal survey's new keyless, machine-readable
        integrations must actually be loaded, verified, and (for api_json) carry a resolvable
        generic parser format."""
        sources = {s.id: s for s in load_tender_sources()}
        for source_id in (
            "ted_eu_cpv",
            "uk_find_tender",
            "fr_boamp",
            "nl_tenderned",
            "es_placsp_atom",
            "us_grants_gov",
        ):
            assert source_id in sources, f"{source_id} missing from config/tenders.yaml"
            assert sources[source_id].verified is True

    def test_a15_keyed_or_blocked_sources_documented_not_verified(self):
        """Portals needing a key, or blocked/out-of-scope on live probe, must stay verified: false
        so eoa.tenders.scan never actually polls them (F24/A15 invariant: verified gates polling
        for kind: api_json)."""
        sources = {s.id: s for s in load_tender_sources()}
        for source_id in (
            "us_sbir_gov",
            "no_doffin_api",
            "pl_ezamowienia_api",
            "eu_sedia_funding_tenders",
            "us_usaspending",
        ):
            assert source_id in sources, f"{source_id} missing from config/tenders.yaml"
            assert sources[source_id].verified is False

    def test_a15_ted_cpv_source_uses_domain_keywords_for_gate_not_cpv_codes(self):
        """The critical correctness requirement behind api_query_keywords: ted_eu_cpv's `keywords`
        (used by the two-signal gate) must stay the normal domain vocabulary, never the raw CPV
        codes used to build the API query -- a CPV code string never appears in a notice's actual
        title/summary text, so using it as the gate's keyword list would silently reject every
        notice this source ever returns."""
        sources = {s.id: s for s in load_tender_sources()}
        src = sources["ted_eu_cpv"]
        assert "electro-optical" in src.keywords
        assert src.api_query_keywords, "ted_eu_cpv must set api_query_keywords"
        for code in src.api_query_keywords:
            assert code.isdigit() and len(code) == 8, f"expected an 8-digit CPV code, got {code!r}"


class TestLoadProcurementSignalsAndDenyDomains:
    def test_loads_real_procurement_signals(self):
        signals = load_procurement_signals()
        assert len(signals) >= 5
        assert "RFP" in signals
        assert "RFI" in signals
        assert "מכרז" in signals

    def test_loads_real_deny_domains(self):
        domains = load_deny_domains()
        assert "wikipedia.org" in domains
        assert "reddit.com" in domains
        assert "nasa.gov" in domains
        assert "marketsandmarkets.com" in domains

    def test_no_bare_generic_words_in_real_domain_keywords(self):
        """Coordinator requirement: plain words like 'windows'/'camera' must never appear alone."""
        sources = load_tender_sources()
        for src in sources:
            for kw in src.keywords:
                assert kw.strip().lower() not in {"windows", "camera", "sensor", "window", "door"}


# --------------------------------------------------------------------------
# TED parsing
# --------------------------------------------------------------------------


class TestParseTed:
    def _payload(self):
        return json.loads((FIXTURES / "ted_sample.json").read_text(encoding="utf-8"))

    def test_parses_all_notices(self):
        notices = _parse_ted_notices(self._payload(), _ted_source())
        assert len(notices) == 3
        assert notices[0].external_ref == "ted_eu:555001-2026"
        assert "electro-optical" in notices[0].title.lower()
        assert notices[0].url == "https://ted.europa.eu/en/notice/555001-2026/html"
        assert notices[0].published_at == dt.date(2026, 8, 20)

    def test_english_title_preferred(self):
        notices = _parse_ted_notices(self._payload(), _ted_source())
        assert notices[0].title.startswith("Germany-Koblenz")


# --------------------------------------------------------------------------
# Contracts Finder parsing
# --------------------------------------------------------------------------


class TestParseContractsFinder:
    def _payload(self):
        return json.loads((FIXTURES / "contracts_finder_sample.json").read_text(encoding="utf-8"))

    def test_parses_all_releases(self):
        notices = _parse_contracts_finder(self._payload(), _cf_source())
        assert len(notices) == 2

    def test_fields_extracted(self):
        notices = _parse_contracts_finder(self._payload(), _cf_source())
        n = notices[0]
        assert n.external_ref == "uk_contracts_finder:ocds-b5fd17-a1b2c3d4"
        assert "counter-UAS" in n.title
        assert n.agency == "Ministry of Defence"
        assert n.deadline == dt.date(2026, 10, 15)
        assert n.published_at == dt.date(2026, 8, 25)

    def test_uuid_extracted_into_notice_url(self):
        notices = _parse_contracts_finder(self._payload(), _cf_source())
        assert (
            notices[0].url
            == "https://www.contractsfinder.service.gov.uk/Notice/a1b2c3d4-1111-2222-3333-444455556666"
        )

    def test_award_tag_maps_to_awarded_status_hint(self):
        notices = _parse_contracts_finder(self._payload(), _cf_source())
        assert notices[1].status_hint == "awarded"
        assert notices[0].status_hint is None


# --------------------------------------------------------------------------
# A15: generic OCDS / generic JSON-list parsers (config-driven, no bespoke per-source function)
# --------------------------------------------------------------------------


def _uk_find_tender_source(**overrides) -> TenderSource:
    base = dict(
        id="uk_find_tender",
        name="UK Find a Tender Service",
        kind="api_json",
        country="UK",
        url="https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages",
        keywords=DOMAIN_KEYWORDS,
        parse_hints={
            "format": "ocds",
            "notice_path": "releases",
            "id_field": "id",
            "title_field": "tender.title",
            "summary_field": "tender.description",
            "agency_field": "buyer.name",
            "date_field": "date",
            "deadline_field": "tender.tenderPeriod.endDate",
            "url_pattern": "https://www.find-tender.service.gov.uk/Notice/{id}",
        },
        verified=True,
    )
    base.update(overrides)
    return TenderSource(**base)


class TestParseGenericOcds:
    def _payload(self):
        return json.loads((FIXTURES / "generic_ocds_sample.json").read_text(encoding="utf-8"))

    def test_parses_all_releases(self):
        notices = _parse_generic_ocds(self._payload(), _uk_find_tender_source())
        assert len(notices) == 2

    def test_fields_extracted_via_parse_hints(self):
        notices = _parse_generic_ocds(self._payload(), _uk_find_tender_source())
        n = notices[0]
        assert n.external_ref == "uk_find_tender:084056-2026"
        assert "electro-optical" in n.title.lower()
        assert n.agency == "Ministry of Defence"
        assert n.deadline == dt.date(2026, 10, 5)
        assert n.published_at == dt.date(2026, 9, 4)
        assert n.url == "https://www.find-tender.service.gov.uk/Notice/084056-2026"

    def test_award_tag_maps_to_awarded_status_hint(self):
        notices = _parse_generic_ocds(self._payload(), _uk_find_tender_source())
        assert notices[1].status_hint == "awarded"
        assert notices[0].status_hint is None

    def test_defaults_used_when_parse_hints_omitted(self):
        """Every parse_hints key is optional -- the plain-OCDS defaults (ocid/tender.title/...)
        must still work, matching uk_contracts_finder's own shape."""
        payload = json.loads((FIXTURES / "contracts_finder_sample.json").read_text(encoding="utf-8"))
        src = TenderSource(
            id="x", name="x", kind="api_json", country="UK", keywords=DOMAIN_KEYWORDS, verified=True
        )
        notices = _parse_generic_ocds(payload, src)
        assert len(notices) == 2
        assert notices[0].external_ref == "x:ocds-b5fd17-a1b2c3d4"
        assert notices[0].agency == "Ministry of Defence"

    def test_missing_id_skips_record(self):
        src = _uk_find_tender_source()
        notices = _parse_generic_ocds({"releases": [{"tag": [], "tender": {"title": "no id"}}]}, src)
        assert notices == []


def _grants_gov_source(**overrides) -> TenderSource:
    base = dict(
        id="us_grants_gov",
        name="US Grants.gov",
        kind="api_json",
        country="US",
        url="https://api.grants.gov/v1/api/search2",
        method="POST",
        keywords=DOMAIN_KEYWORDS,
        parse_hints={
            "format": "json_list",
            "notice_path": "data.oppHits",
            "id_field": "id",
            "title_field": "title",
            "agency_field": "agency",
            "date_field": "openDate",
            "deadline_field": "closeDate",
            "url_pattern": "https://www.grants.gov/search-results-detail/{id}",
        },
        verified=True,
    )
    base.update(overrides)
    return TenderSource(**base)


class TestParseGenericJsonList:
    def _payload(self):
        return json.loads((FIXTURES / "generic_json_list_sample.json").read_text(encoding="utf-8"))

    def test_parses_all_hits(self):
        notices = _parse_generic_json_list(self._payload(), _grants_gov_source())
        assert len(notices) == 2

    def test_fields_extracted_via_parse_hints(self):
        notices = _parse_generic_json_list(self._payload(), _grants_gov_source())
        n = notices[0]
        assert n.external_ref == "us_grants_gov:352741"
        assert "electro-optical" in n.title.lower()
        assert n.agency == "Naval Research Laboratory"
        assert n.published_at == dt.date(2024, 3, 1)
        assert n.deadline == dt.date(2026, 9, 30)
        assert n.url == "https://www.grants.gov/search-results-detail/352741"

    def test_empty_deadline_string_parses_to_none(self):
        notices = _parse_generic_json_list(self._payload(), _grants_gov_source())
        assert notices[1].deadline is None

    def test_flat_records_with_nested_fields_dict(self):
        """BOAMP's shape: {"records": [{"fields": {...}}]} -- notice_path='records',
        every other field a dotted 'fields.X' path."""
        payload = json.loads((FIXTURES / "generic_json_list_flat_sample.json").read_text(encoding="utf-8"))
        src = TenderSource(
            id="fr_boamp",
            name="BOAMP",
            kind="api_json",
            country="FR",
            keywords=DOMAIN_KEYWORDS,
            parse_hints={
                "format": "json_list",
                "notice_path": "records",
                "id_field": "fields.id",
                "title_field": "fields.objet",
                "summary_field": "fields.type_marche_facette",
                "agency_field": "fields.nomacheteur",
                "date_field": "fields.dateparution",
                "deadline_field": "fields.datelimitereponse",
                "url_field": "fields.url_avis",
            },
            verified=True,
        )
        notices = _parse_generic_json_list(payload, src)
        assert len(notices) == 1
        n = notices[0]
        assert n.external_ref == "fr_boamp:24_30807"
        assert "electro-optiques" in n.title.lower()
        assert n.agency == "MINARM/SCA/PFC BREST"
        assert n.published_at == dt.date(2026, 8, 15)
        assert n.deadline == dt.date(2026, 10, 30)
        assert n.url == "https://www.boamp.fr/pages/avis/?q=idweb:24-30807"

    def test_bare_top_level_list_wrapped_as_root(self):
        """_fetch_api_json wraps a bare-array JSON response as {"_root": [...]} before calling any
        parser -- notice_path="" reads that wrapper key."""
        src = TenderSource(
            id="x",
            name="x",
            kind="api_json",
            country="US",
            keywords=DOMAIN_KEYWORDS,
            parse_hints={"format": "json_list", "notice_path": "", "id_field": "id", "title_field": "title"},
            verified=True,
        )
        payload = {"_root": [{"id": "1", "title": "electro-optical sensor RFI"}]}
        notices = _parse_generic_json_list(payload, src)
        assert len(notices) == 1
        assert notices[0].external_ref == "x:1"

    def test_missing_id_skips_record(self):
        src = _grants_gov_source()
        notices = _parse_generic_json_list({"data": {"oppHits": [{"title": "no id"}]}}, src)
        assert notices == []

    def test_non_dict_records_skipped(self):
        src = _grants_gov_source()
        notices = _parse_generic_json_list({"data": {"oppHits": ["not-a-dict", None]}}, src)
        assert notices == []


class TestCollectSourceNoticesApiQueryKeywordsOverride:
    """A15: api_query_keywords (ted_eu_cpv) must drive the API-fetch loop while src.keywords (the
    gate vocabulary) is left untouched."""

    def test_api_query_keywords_used_for_fetch_loop(self):
        src = TenderSource(
            id="ted_eu_cpv",
            name="TED CPV",
            kind="api_json",
            country="EU",
            url="https://api.ted.europa.eu/v3/notices/search",
            method="POST",
            query_template='{"query":"classification-cpv={keyword}","fields":["ND","TI"],"limit":5}',
            keywords=DOMAIN_KEYWORDS,
            api_query_keywords=["38620000", "35120000"],
            verified=True,
        )
        seen_keywords: list[str] = []

        def fake_fetch(src_arg, keyword):
            seen_keywords.append(keyword)
            return []

        with patch("eoa.tenders.scan._fetch_api_json", side_effect=fake_fetch):
            _collect_source_notices(src, [])
        assert seen_keywords == ["38620000", "35120000"]

    def test_no_override_falls_back_to_keywords(self):
        src = _ted_source()
        seen_keywords: list[str] = []

        def fake_fetch(src_arg, keyword):
            seen_keywords.append(keyword)
            return []

        with patch("eoa.tenders.scan._fetch_api_json", side_effect=fake_fetch):
            _collect_source_notices(src, [])
        assert seen_keywords == DOMAIN_KEYWORDS[:5]


# --------------------------------------------------------------------------
# denylist
# --------------------------------------------------------------------------


class TestIsDenylistedDomain:
    def test_exact_domain_denied(self):
        assert _is_denylisted_domain("https://wikipedia.org/wiki/Infrared", ["wikipedia.org"]) is True

    def test_subdomain_denied(self):
        assert _is_denylisted_domain("https://en.wikipedia.org/wiki/Infrared", ["wikipedia.org"]) is True

    def test_www_prefix_stripped(self):
        assert _is_denylisted_domain("https://www.reddit.com/r/foo", ["reddit.com"]) is True

    def test_unrelated_domain_not_denied(self):
        assert (
            _is_denylisted_domain("https://ted.europa.eu/en/notice/1", ["wikipedia.org", "reddit.com"])
            is False
        )

    def test_empty_url_not_denied(self):
        assert _is_denylisted_domain("", ["wikipedia.org"]) is False

    def test_lookalike_domain_not_falsely_denied(self):
        """'notwikipedia.org' must not be treated as a subdomain of 'wikipedia.org'."""
        assert _is_denylisted_domain("https://notwikipedia.org/page", ["wikipedia.org"]) is False


class TestParseSearchHitsDenylist:
    def test_denylisted_hits_dropped(self):
        from eoa.search.searxng_client import SearchHit

        hits = [
            SearchHit(
                url="https://en.wikipedia.org/wiki/Infrared",
                title="Infrared",
                snippet="What is infrared",
                engine="google",
            ),
            SearchHit(
                url="https://sam.gov/opp/1",
                title="RFI electro-optical",
                snippet="sources sought",
                engine="google",
            ),
        ]
        notices = _parse_search_hits(hits, _search_source(), ["wikipedia.org"])
        assert len(notices) == 1
        assert notices[0].url == "https://sam.gov/opp/1"


# --------------------------------------------------------------------------
# domain-signal / procurement-signal / two-signal gate
# --------------------------------------------------------------------------


class TestMatchesKeywords:
    def test_matches_case_insensitive(self):
        notices = _parse_ted_notices(json.loads((FIXTURES / "ted_sample.json").read_text()), _ted_source())
        terms = _matches_keywords(notices[0], ["electro-optical", "targeting pod"])
        assert "electro-optical" in terms

    def test_no_match_for_irrelevant_notice(self):
        notices = _parse_ted_notices(json.loads((FIXTURES / "ted_sample.json").read_text()), _ted_source())
        terms = _matches_keywords(notices[1], ["electro-optical", "infrared", "targeting pod"])
        assert terms == []

    def test_bare_generic_word_does_not_match_multi_word_phrase(self):
        """'windows'/'camera' alone must never satisfy a domain-signal phrase like 'surveillance camera'."""
        notice = NoticeRaw(
            source_id="x",
            external_ref="x:1",
            title="HMP Onley: Windows and New Cell Doors",
            summary="New camera",
        )
        assert _matches_keywords(notice, ["surveillance camera", "electro-optical"]) == []


class TestHasProcurementSignal:
    def test_api_json_source_always_true(self):
        notice = NoticeRaw(source_id="ted_eu", external_ref="x:1", title="Supply of electro-optical pods")
        assert _has_procurement_signal(notice, "api_json", PROCUREMENT_SIGNALS) is True

    def test_search_source_requires_explicit_signal(self):
        notice = NoticeRaw(
            source_id="x", external_ref="x:1", title="What is Infrared Light?", summary="A physics primer"
        )
        assert _has_procurement_signal(notice, "search", PROCUREMENT_SIGNALS) is False

    def test_search_source_with_signal_present(self):
        notice = NoticeRaw(
            source_id="x", external_ref="x:1", title="RFI: electro-optical sensor sources sought"
        )
        assert _has_procurement_signal(notice, "search", PROCUREMENT_SIGNALS) is True

    def test_hebrew_signal_recognized(self):
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="מכרז למערכת אלקטרו-אופטית")
        assert _has_procurement_signal(notice, "search", PROCUREMENT_SIGNALS) is True


class TestPassesGate:
    def test_api_json_domain_only_passes(self):
        """TED/Contracts Finder notices rarely say "tender" literally -- domain signal alone suffices."""
        notice = NoticeRaw(
            source_id="ted_eu", external_ref="x:1", title="Supply of electro-optical targeting pods"
        )
        assert _passes_gate(notice, "api_json", DOMAIN_KEYWORDS, PROCUREMENT_SIGNALS) == [
            "electro-optical",
            "targeting pod",
        ]

    def test_search_source_needs_both_signals(self):
        notice = NoticeRaw(
            source_id="x", external_ref="x:1", title="Infrared: How It Works", summary="A NASA explainer"
        )
        assert _passes_gate(notice, "search", DOMAIN_KEYWORDS, PROCUREMENT_SIGNALS) == []

    def test_search_source_with_both_signals_passes(self):
        notice = NoticeRaw(
            source_id="x", external_ref="x:1", title="RFI for infrared targeting pod sources sought"
        )
        terms = _passes_gate(notice, "search", DOMAIN_KEYWORDS, PROCUREMENT_SIGNALS)
        assert "infrared" in terms
        assert "targeting pod" in terms

    def test_no_domain_signal_fails_regardless_of_procurement_signal(self):
        notice = NoticeRaw(source_id="ted_eu", external_ref="x:1", title="Tender for office furniture supply")
        assert _passes_gate(notice, "api_json", DOMAIN_KEYWORDS, PROCUREMENT_SIGNALS) == []

    def test_incidental_domain_mention_in_real_tender_still_passes_gate_one(self):
        """The HMP Onley case: a real UK Contracts Finder notice about windows/doors that
        incidentally mentions "surveillance camera" still passes THIS gate (api_json => implicit
        procurement signal, domain term present) -- the LLM relevance gate (scan_tenders) is what
        is expected to catch it, not this deterministic gate alone. See coordinator feedback."""
        notice = NoticeRaw(
            source_id="uk_contracts_finder",
            external_ref="x:1",
            title="HMP Onley: Windows and New Cell Doors",
            summary="Refurbishment works including a surveillance camera at the gatehouse.",
        )
        assert _passes_gate(notice, "api_json", DOMAIN_KEYWORDS, PROCUREMENT_SIGNALS) == [
            "surveillance camera"
        ]


# --------------------------------------------------------------------------
# date window / status
# --------------------------------------------------------------------------


class TestWithinWindow:
    def test_recent_notice_within_window(self):
        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", published_at=dt.date(2026, 9, 1))
        assert _within_window(n, since_days=3, today=dt.date(2026, 9, 4)) is True

    def test_old_notice_outside_window(self):
        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", published_at=dt.date(2016, 5, 10))
        assert _within_window(n, since_days=3, today=dt.date(2026, 9, 4)) is False

    def test_undated_notice_always_within_window(self):
        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", published_at=None)
        assert _within_window(n, since_days=3, today=dt.date(2026, 9, 4)) is True


class TestInitialStatus:
    def test_status_hint_wins(self):
        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", status_hint="awarded")
        assert _initial_status(n, dt.date(2026, 9, 4)) == "awarded"

    def test_status_hint_wins_over_notice_type(self):
        """A structured source's own explicit tag is more authoritative than the LLM's notice_type."""
        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", status_hint="closed")
        assert _initial_status(n, dt.date(2026, 9, 4), notice_type="award") == "closed"

    def test_notice_type_award_forces_awarded_status(self):
        """F2: a notice whose own text reports an already-signed contract (LLM notice_type=='award')
        is 'awarded', not left 'open' just because no deadline/status_hint exists."""
        n = NoticeRaw(source_id="x", external_ref="x:1", title="t")
        assert _initial_status(n, dt.date(2026, 9, 4), notice_type="award") == "awarded"

    def test_past_deadline_is_closed(self):
        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", deadline=dt.date(2026, 1, 1))
        assert _initial_status(n, dt.date(2026, 9, 4)) == "closed"

    def test_future_deadline_is_open(self):
        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", deadline=dt.date(2027, 1, 1))
        assert _initial_status(n, dt.date(2026, 9, 4)) == "open"

    def test_no_deadline_and_no_published_at_is_unknown(self):
        """F2 (the bug this fix addresses): an undated notice with no deadline AND no published_at
        must never be assumed 'open' -- it's 'unknown' until dates can be established."""
        n = NoticeRaw(source_id="x", external_ref="x:1", title="t")
        assert _initial_status(n, dt.date(2026, 9, 4)) == "unknown"

    def test_no_deadline_recent_published_is_open(self):
        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", published_at=dt.date(2026, 8, 1))
        assert _initial_status(n, dt.date(2026, 9, 4)) == "open"

    def test_no_deadline_stale_published_is_closed(self):
        """F2: a notice with no deadline but a published_at older than 365 days is stale, not
        indefinitely open (the TED-2016 / HigherGov-FY2023 cases from the review)."""
        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", published_at=dt.date(2016, 12, 17))
        assert _initial_status(n, dt.date(2026, 9, 4)) == "closed"

    def test_no_deadline_published_exactly_at_boundary_is_open(self):
        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", published_at=dt.date(2025, 9, 5))
        assert _initial_status(n, dt.date(2026, 9, 4)) == "open"


class TestCountryFromDomain:
    def test_sam_gov_maps_to_us(self):
        assert _country_from_domain("https://sam.gov/opp/1") == "US"

    def test_highergov_maps_to_us(self):
        assert _country_from_domain("https://www.highergov.com/contract-opportunity/x/") == "US"

    def test_usarfp_maps_to_us(self):
        assert _country_from_domain("https://www.usarfp.com/tender/x.php") == "US"

    def test_ted_europa_maps_to_eu(self):
        assert _country_from_domain("https://ted.europa.eu/en/notice/1") == "EU"

    def test_subdomain_matches(self):
        assert _country_from_domain("https://online.mod.gov.il/x") == "IL"

    def test_unmapped_domain_returns_none(self):
        assert _country_from_domain("https://www.rfpmart.com/x.html") is None

    def test_empty_url_returns_none(self):
        assert _country_from_domain("") is None
        assert _country_from_domain(None) is None


class TestApplyExtractionToNotice:
    def test_fills_missing_published_at_and_deadline(self):
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="t")
        extract = TenderExtract(
            relevant=True,
            relevance=6,
            confidence=0.8,
            published_at=dt.date(2026, 8, 1),
            deadline=dt.date(2026, 10, 1),
        )
        _apply_extraction_to_notice(notice, extract)
        assert notice.published_at == dt.date(2026, 8, 1)
        assert notice.deadline == dt.date(2026, 10, 1)

    def test_never_overwrites_existing_dates(self):
        """TED/Contracts Finder's own structured parse stays authoritative over the LLM."""
        notice = NoticeRaw(
            source_id="x",
            external_ref="x:1",
            title="t",
            published_at=dt.date(2026, 1, 1),
            deadline=dt.date(2026, 2, 1),
        )
        extract = TenderExtract(
            relevant=True,
            relevance=6,
            confidence=0.8,
            published_at=dt.date(2099, 1, 1),
            deadline=dt.date(2099, 1, 1),
        )
        _apply_extraction_to_notice(notice, extract)
        assert notice.published_at == dt.date(2026, 1, 1)
        assert notice.deadline == dt.date(2026, 2, 1)

    def test_fills_agency_and_country_when_generic(self):
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="t", country="other")
        extract = TenderExtract(
            relevant=True, relevance=6, confidence=0.8, agency="US Air Force", country="US"
        )
        _apply_extraction_to_notice(notice, extract)
        assert notice.agency == "US Air Force"
        assert notice.country == "US"

    def test_does_not_overwrite_real_country(self):
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="t", country="UK")
        extract = TenderExtract(relevant=True, relevance=6, confidence=0.8, country="US")
        _apply_extraction_to_notice(notice, extract)
        assert notice.country == "UK"


class TestApplyDomainCountryFallback:
    def test_fills_country_from_url_when_generic(self):
        notice = NoticeRaw(
            source_id="x", external_ref="x:1", title="t", country="other", url="https://sam.gov/opp/1"
        )
        _apply_domain_country_fallback(notice)
        assert notice.country == "US"

    def test_does_not_overwrite_real_country(self):
        notice = NoticeRaw(
            source_id="x", external_ref="x:1", title="t", country="IL", url="https://sam.gov/opp/1"
        )
        _apply_domain_country_fallback(notice)
        assert notice.country == "IL"

    def test_no_match_leaves_country_unchanged(self):
        notice = NoticeRaw(
            source_id="x",
            external_ref="x:1",
            title="t",
            country="other",
            url="https://www.rfpmart.com/x.html",
        )
        _apply_domain_country_fallback(notice)
        assert notice.country == "other"


class TestFetchNoticeText:
    """F24: _fetch_notice_text now also reports whether the notice was actually verified (a real
    page fetched, or a structured api_json record) vs. only a search snippet/RSS blurb -- that
    second value feeds the "unverified + undated -> reject" gate rule."""

    def test_api_json_source_never_fetches(self):
        notice = NoticeRaw(
            source_id="x", external_ref="x:1", title="t", summary="s", url="https://ted.europa.eu/x"
        )
        with patch("eoa.tenders.scan.fetch_remote") as mock_fetch:
            text, verified = _fetch_notice_text(notice, "api_json")
        mock_fetch.assert_not_called()
        assert text == "t\n\ns"
        assert verified is True

    def test_search_source_fetches_and_uses_page_text(self):
        notice = NoticeRaw(
            source_id="x", external_ref="x:1", title="t", summary="s", url="https://example.gov/n/1"
        )
        with patch(
            "eoa.tenders.scan.fetch_remote", return_value={"text": "full notice body with a deadline"}
        ):
            text, verified = _fetch_notice_text(notice, "search")
        assert text == "full notice body with a deadline"
        assert verified is True

    def test_fetch_failure_falls_back_to_title_and_summary_unverified(self):
        notice = NoticeRaw(
            source_id="x", external_ref="x:1", title="t", summary="s", url="https://example.gov/n/1"
        )
        with patch("eoa.tenders.scan.fetch_remote", side_effect=RuntimeError("blocked")):
            text, verified = _fetch_notice_text(notice, "search")
        assert text == "t\n\ns"
        assert verified is False

    def test_no_url_falls_back_without_fetching_unverified(self):
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="t", summary="s")
        with patch("eoa.tenders.scan.fetch_remote") as mock_fetch:
            text, verified = _fetch_notice_text(notice, "search")
        mock_fetch.assert_not_called()
        assert text == "t\n\ns"
        assert verified is False

    def test_empty_page_text_falls_back_unverified(self):
        notice = NoticeRaw(
            source_id="x", external_ref="x:1", title="t", summary="s", url="https://example.gov/n/1"
        )
        with patch("eoa.tenders.scan.fetch_remote", return_value={"text": ""}):
            text, verified = _fetch_notice_text(notice, "search")
        assert text == "t\n\ns"
        assert verified is False


class _FakeCursor:
    def __init__(self, fetchall_result=None):
        self.executed: list[tuple[str, dict | None]] = []
        self._fetchall_result = [] if fetchall_result is None else fetchall_result

    def execute(self, query, params=None):
        self.executed.append((query, params))
        return self

    def fetchall(self):
        return self._fetchall_result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestTransitionClosedSql:
    def test_closes_past_deadline_and_stale_undated_rows(self, monkeypatch):
        """F2: the nightly transition must close BOTH the original case (deadline passed) AND the
        newly-added stale-undated case (no deadline, published_at > 365 days old)."""
        cur = _FakeCursor(fetchall_result=[{"id": 1}, {"id": 2}])
        conn = _FakeConnection(cur)
        monkeypatch.setattr("eoa.tenders.scan.connection", lambda: conn)

        count = _transition_closed()

        assert count == 2
        query, params = cur.executed[0]
        assert "deadline IS NOT NULL AND deadline <" in query
        assert "deadline IS NULL AND published_at IS NOT NULL" in query
        assert "status = 'open'" in query
        assert params["stale_before"] == params["today"] - dt.timedelta(days=365)


class TestArchiveStaleClosedSql:
    def test_archives_closed_rows_past_the_grace_period(self, monkeypatch):
        """F24: a 'closed' tender is archived (never deleted) once 30 days have passed since its
        deadline (falling back to published_at, then updated_at, when deadline is absent)."""
        cur = _FakeCursor(fetchall_result=[{"id": 5}, {"id": 6}, {"id": 7}])
        conn = _FakeConnection(cur)
        monkeypatch.setattr("eoa.tenders.scan.connection", lambda: conn)

        count = _archive_stale_closed()

        assert count == 3
        query, params = cur.executed[0]
        assert "status = 'archived'" in query
        assert "status = 'closed'" in query
        assert "COALESCE(deadline, published_at::date, updated_at::date)" in query
        assert params["archive_before"] == dt.date.today() - dt.timedelta(days=30)


# --------------------------------------------------------------------------
# scan_tenders orchestration (fully mocked DB/LLM)
# --------------------------------------------------------------------------


class TestScanTendersGateAndDedup:
    def test_matched_but_llm_deferred_is_stored_as_candidate_with_neutral_score(self):
        """W2b (open intake): with the LLM budget exhausted (llm_budget_s=0), notice 0 (the only one
        that both matches the two-signal gate AND is new) never gets a classification -- unlike the
        old F24 gate (which rejected outright, ``no_llm_classification``), this is no longer a
        rejection: the notice is still stored, with the neutral 0.5 relevance_score and
        intake='candidate' (below the 0.6 threshold stubbed in this test)."""
        src = _ted_source()

        with (
            patch("eoa.tenders.scan._collect_source_notices") as mock_collect,
            patch("eoa.tenders.scan._tender_exists") as mock_exists,
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
            patch("eoa.tenders.scan._transition_closed", return_value=0),
            patch("eoa.tenders.scan._archive_stale_closed", return_value=0),
            patch("eoa.tenders.scan.redrive_all_tender_statuses", return_value=0),
            patch("eoa.tenders.scan.get_relevance_threshold", return_value=0.6),
            patch("eoa.tenders.scan.get_source_priorities", return_value={}),
        ):
            notices = _parse_ted_notices(json.loads((FIXTURES / "ted_sample.json").read_text()), src)
            mock_collect.return_value = notices
            # notice[0] relevant+new, notice[1] irrelevant (no keyword match), notice[2] relevant+dup
            mock_exists.side_effect = lambda ref: ref == "ted_eu:555003-2016"
            mock_insert.return_value = (42, 99)

            stats = scan_tenders(since_days=4000, sources=[src], llm_budget_s=0)

        assert isinstance(stats, TenderStats)
        mock_insert.assert_called_once()
        _, kwargs = mock_insert.call_args
        assert kwargs["relevance_score"] == 0.5
        assert kwargs["intake"] == "candidate"
        assert stats.sources_scanned == 1
        assert stats.notices_fetched == 3
        assert stats.matched == 2  # notice 0 and notice 2 match the two-signal gate; notice 1 does not
        assert stats.duplicates == 1  # notice 2, already in the DB
        assert stats.inserted == 1
        assert stats.candidates == 1
        assert stats.accepted == 0
        assert stats.gate_rejected == 0  # W2b: a deferred LLM is no longer a hard-reject reason
        assert stats.llm_deferred == 1  # llm_budget_s=0 -> deferred, not called

    def test_html_sources_are_skipped(self):
        src = TenderSource(
            id="il_mod", name="x", kind="html", country="IL", keywords=DOMAIN_KEYWORDS, verified=False
        )
        with (
            patch("eoa.tenders.scan._transition_closed", return_value=0),
            patch("eoa.tenders.scan._archive_stale_closed", return_value=0),
            patch("eoa.tenders.scan.redrive_all_tender_statuses", return_value=0),
            patch("eoa.tenders.scan.get_source_priorities", return_value={}),
        ):
            stats = scan_tenders(sources=[src])
        assert stats.sources_scanned == 0

    def test_unverified_api_json_sources_are_skipped(self):
        src = TenderSource(
            id="sam_gov_api",
            name="x",
            kind="api_json",
            country="US",
            url="https://api.sam.gov/x",
            keywords=DOMAIN_KEYWORDS,
            verified=False,
        )
        with (
            patch("eoa.tenders.scan._transition_closed", return_value=0),
            patch("eoa.tenders.scan._archive_stale_closed", return_value=0),
            patch("eoa.tenders.scan.redrive_all_tender_statuses", return_value=0),
            patch("eoa.tenders.scan.get_source_priorities", return_value={}),
        ):
            stats = scan_tenders(sources=[src])
        assert stats.sources_scanned == 0

    def test_source_failure_does_not_stop_scan(self):
        good = _cf_source()
        bad = TenderSource(
            id="broken",
            name="x",
            kind="api_json",
            country="US",
            url="https://x",
            keywords=["x"],
            verified=True,
        )
        with (
            patch("eoa.tenders.scan._collect_source_notices") as mock_collect,
            patch("eoa.tenders.scan._transition_closed", return_value=0),
            patch("eoa.tenders.scan._archive_stale_closed", return_value=0),
            patch("eoa.tenders.scan.redrive_all_tender_statuses", return_value=0),
            patch("eoa.tenders.scan.get_source_priorities", return_value={}),
        ):

            def side_effect(src, deny_domains):
                if src.id == "broken":
                    raise RuntimeError("boom")
                return []

            mock_collect.side_effect = side_effect
            stats = scan_tenders(sources=[bad, good])

        assert stats.sources_failed == 1
        assert stats.sources_scanned == 1

    def test_search_source_notice_without_procurement_signal_never_reaches_insert(self):
        """The Wikipedia/NASA-style junk case: domain term present, no procurement signal -- still
        dropped before the (now much narrower) hard-rejection gate ever runs, since it never
        clears the two-signal vocabulary gate in the first place."""
        src = _search_source()
        notice = NoticeRaw(
            source_id=src.id, external_ref="rfi_rfp_news:https://x/infrared", title="What Is Infrared Light?"
        )
        with (
            patch("eoa.tenders.scan._collect_source_notices", return_value=[notice]),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
            patch("eoa.tenders.scan._transition_closed", return_value=0),
            patch("eoa.tenders.scan._archive_stale_closed", return_value=0),
            patch("eoa.tenders.scan.redrive_all_tender_statuses", return_value=0),
            patch("eoa.tenders.scan.get_source_priorities", return_value={}),
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.matched == 0
        assert stats.inserted == 0


def _common_patches(notice: NoticeRaw) -> ExitStack:
    """A single combined context manager for the DB stubs every scan_tenders() test needs (avoids
    the parenthesized-`with` star-unpacking trick, which is not valid syntax when mixed with `as`
    clauses -- see CPython's PEG grammar for `with_stmt`). Includes the W2b self-tuning reads
    (threshold/source-priority/lessons) so no test here ever touches a real connection pool."""
    stack = ExitStack()
    stack.enter_context(patch("eoa.tenders.scan._collect_source_notices", return_value=[notice]))
    stack.enter_context(patch("eoa.tenders.scan._tender_exists", return_value=False))
    stack.enter_context(patch("eoa.tenders.scan._transition_closed", return_value=0))
    stack.enter_context(patch("eoa.tenders.scan._archive_stale_closed", return_value=0))
    stack.enter_context(patch("eoa.tenders.scan.redrive_all_tender_statuses", return_value=0))
    stack.enter_context(patch("eoa.tenders.scan.get_relevance_threshold", return_value=0.6))
    stack.enter_context(patch("eoa.tenders.scan.get_source_priorities", return_value={}))
    stack.enter_context(patch("eoa.tenders.scan.tender_lessons_text", return_value="אין עדיין משוב."))
    return stack


class TestScanTendersOpenIntake:
    """W2b (docs/REVIEW_2026-09-06_evening.md, user requirement 2026-09-06 18:55): a low/absent LLM
    relevance verdict, an unrecognised notice_type, a passed deadline, or a stale published_at are
    no longer rejection grounds -- everything that clears the two-signal vocabulary gate is stored,
    carrying a relevance_score/intake pair instead. Only the four hard reasons documented on
    _gate_reject_reason still drop a notice outright (covered by TestGateRejectReason below)."""

    def test_low_relevance_stored_as_candidate_not_rejected(self):
        src = _ted_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref="ted_eu:1",
            title="Supply of electro-optical widgets",
            url="https://api.ted.europa.eu/notice/1",
        )
        with (
            _common_patches(notice),
            patch(
                "eoa.tenders.scan._llm_classify", return_value=(_extract(relevant=False, relevance=1), True)
            ),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_called_once()
        _, kwargs = mock_insert.call_args
        assert kwargs["relevance_score"] == 0.1
        assert kwargs["intake"] == "candidate"
        assert stats.gate_rejected == 0
        assert stats.inserted == 1
        assert stats.candidates == 1
        assert stats.accepted == 0
        assert stats.matched == 1

    def test_relevance_one_below_floor_stored_as_candidate(self):
        src = _ted_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref="ted_eu:1",
            title="Supply of electro-optical widgets",
            url="https://api.ted.europa.eu/notice/1",
        )
        with (
            _common_patches(notice),
            patch(
                "eoa.tenders.scan._llm_classify",
                return_value=(_extract(relevant=True, relevance=RELEVANCE_MIN_ACCEPT - 1), True),
            ),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_called_once()
        assert stats.inserted == 1
        assert stats.candidates == 1

    def test_relevance_at_floor_stored_accepted(self):
        src = _ted_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref="ted_eu:1",
            title="Supply of electro-optical widgets",
            url="https://api.ted.europa.eu/notice/1",
        )
        with (
            _common_patches(notice),
            patch(
                "eoa.tenders.scan._llm_classify",
                return_value=(_extract(relevant=True, relevance=RELEVANCE_MIN_ACCEPT), True),
            ),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_called_once()
        _, kwargs = mock_insert.call_args
        assert kwargs["relevance"] == RELEVANCE_MIN_ACCEPT
        assert kwargs["intake"] == "accepted"  # 6/10 = 0.6, at the (stubbed) 0.6 threshold
        assert stats.inserted == 1
        assert stats.accepted == 1
        assert stats.gate_rejected == 0

    def test_invalid_notice_type_no_longer_rejected(self):
        """W2b: an 'award'/'other' notice_type is no longer a rejection reason -- it's still
        stored (as a candidate/accepted per its relevance_score); _initial_status still maps a
        genuine 'award' verdict to status='awarded' rather than 'open', so it simply never shows
        as an open opportunity."""
        for bad_type in ("award", "other"):
            src = _ted_source()
            notice = NoticeRaw(
                source_id=src.id,
                external_ref=f"ted_eu:{bad_type}",
                title="Supply of electro-optical widgets",
                url="https://api.ted.europa.eu/notice/1",
            )
            with (
                _common_patches(notice),
                patch("eoa.tenders.scan._llm_classify", return_value=(_extract(notice_type=bad_type), True)),
                patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
            ):
                stats = scan_tenders(sources=[src])
            mock_insert.assert_called_once()
            assert stats.gate_rejected == 0
            assert stats.inserted == 1

    def test_expired_deadline_no_longer_rejected(self):
        src = _ted_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref="ted_eu:1",
            title="Supply of electro-optical widgets",
            url="https://api.ted.europa.eu/notice/1",
        )
        with (
            _common_patches(notice),
            patch(
                "eoa.tenders.scan._llm_classify", return_value=(_extract(deadline=dt.date(2020, 1, 1)), True)
            ),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_called_once()
        assert stats.gate_rejected == 0
        assert stats.inserted == 1

    def test_stale_published_at_no_longer_rejected(self):
        src = _ted_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref="ted_eu:1",
            title="Supply of electro-optical widgets",
            url="https://api.ted.europa.eu/notice/1",
        )
        stale = dt.date.today() - dt.timedelta(days=NOTICE_MAX_AGE_DAYS + 1)
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", return_value=(_extract(published_at=stale), True)),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_called_once()
        assert stats.gate_rejected == 0
        assert stats.inserted == 1

    def test_denylisted_domain_gate_rejected(self):
        """A document-hosting/aggregator reupload (e.g. a Scribd PDF) is still a hard rejection
        even if the LLM itself would have scored it as relevant -- one of the four remaining hard
        cases (see _gate_reject_reason)."""
        src = _search_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref=f"{src.id}:1",
            title="RFP for infrared targeting pod for Heron UAV",
            url="https://www.scribd.com/document/12345/rfp-eo-ir-pods",
        )
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", return_value=(_extract(), True)),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.gate_rejected == 1

    def test_unverified_undated_snippet_now_stored_as_candidate(self):
        """W2b: only a search snippet (the actual notice page was never fetched) and no date at
        all is no longer a hard rejection (this was exactly the failure mode that discarded real
        GovTribe/SAM.gov Navy sources-sought notices per docs/MODULES.md 'Round 4 discovery' W2) --
        it's stored, with whatever relevance_score the LLM verdict implies."""
        src = _search_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref=f"{src.id}:1",
            title="RFI for infrared sensor",
            url="https://example.gov/rfi/1",
        )
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", return_value=(_extract(), False)),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_called_once()
        assert stats.gate_rejected == 0
        assert stats.inserted == 1

    def test_verified_undated_notice_stored_as_unknown(self):
        src = _search_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref=f"{src.id}:1",
            title="RFI for infrared sensor",
            url="https://example.gov/rfi/1",
        )
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", return_value=(_extract(), True)),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_called_once()
        assert stats.inserted == 1
        assert stats.gate_rejected == 0

    def test_no_llm_classification_stored_with_neutral_score(self):
        """W2b: a deferred/unavailable LLM call is no longer a rejection -- the notice is stored
        with the neutral 0.5 relevance_score (see _relevance_score_for)."""
        src = _ted_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref="ted_eu:1",
            title="Supply of electro-optical widgets",
            url="https://api.ted.europa.eu/notice/1",
        )
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", side_effect=ResourceUnavailable("no vram")),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_called_once()
        _, kwargs = mock_insert.call_args
        assert kwargs["relevance_score"] == 0.5
        assert stats.llm_deferred == 1
        assert stats.gate_rejected == 0
        assert stats.inserted == 1

    def test_llm_output_error_stored_with_neutral_score(self):
        src = _ted_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref="ted_eu:1",
            title="Supply of electro-optical widgets",
            url="https://api.ted.europa.eu/notice/1",
        )
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", side_effect=LLMOutputError("bad json")),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_called_once()
        assert stats.llm_failed == 1
        assert stats.gate_rejected == 0
        assert stats.inserted == 1

    def test_unexpected_llm_exception_stored_with_neutral_score(self):
        src = _ted_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref="ted_eu:1",
            title="Supply of electro-optical widgets",
            url="https://api.ted.europa.eu/notice/1",
        )
        with (
            _common_patches(notice),
            patch(
                "eoa.tenders.scan._llm_classify", side_effect=UnicodeEncodeError("cp1252", "x", 0, 1, "boom")
            ),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_called_once()
        assert stats.llm_failed == 1
        assert stats.gate_rejected == 0
        assert stats.inserted == 1

    def test_llm_matched_terms_override_deterministic_ones_when_present(self):
        src = _ted_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref="ted_eu:1",
            title="Supply of electro-optical widgets",
            url="https://api.ted.europa.eu/notice/1",
        )
        with (
            _common_patches(notice),
            patch(
                "eoa.tenders.scan._llm_classify",
                return_value=(_extract(matched_terms=["FLIR", "gimbal"]), True),
            ),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            scan_tenders(sources=[src])
        args, _ = mock_insert.call_args
        assert args[1] == ["FLIR", "gimbal"]

    def test_status_hint_awarded_gate_rejected_even_with_good_llm_verdict(self):
        """A structured source's own explicit tag (Contracts Finder's OCDS 'award' tag) wins
        outright -- an already-awarded notice is dropped regardless of what the LLM says."""
        src = _cf_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref="uk_contracts_finder:1",
            title="Supply of electro-optical widgets",
            status_hint="awarded",
        )
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", return_value=(_extract(), True)),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.gate_rejected == 1

    def test_extraction_dates_and_country_are_merged_into_notice_before_insert(self):
        """F2/F13: published_at/deadline/agency/country from the LLM extraction must land on the
        notice actually passed to _insert_tender_and_item (it reads them off notice.*)."""
        src = _search_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref=f"{src.id}:1",
            title="RFI for infrared sensor",
            country="other",
            url="https://example.gov/rfi/1",
        )
        extract = _extract(
            published_at=dt.date(2026, 8, 1),
            deadline=dt.date(2026, 10, 1),
            agency="US Navy",
            country="US",
        )
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", return_value=(extract, True)),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            scan_tenders(sources=[src])
        args, _ = mock_insert.call_args
        inserted_notice = args[0]
        assert inserted_notice.published_at == dt.date(2026, 8, 1)
        assert inserted_notice.deadline == dt.date(2026, 10, 1)
        assert inserted_notice.agency == "US Navy"
        assert inserted_notice.country == "US"

    def test_domain_country_fallback_applied_before_insert(self):
        """F13: a HigherGov/SAM.gov/usarfp URL gets its country filled from the domain table even
        when the LLM extraction didn't supply one."""
        src = _search_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref=f"{src.id}:1",
            title="RFI for infrared sensor",
            country="other",
            url="https://www.highergov.com/contract-opportunity/x/",
        )
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", return_value=(_extract(), True)),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            scan_tenders(sources=[src])
        args, _ = mock_insert.call_args
        assert args[0].country == "US"


class TestGateRejectReason:
    """W2b (open intake): direct unit tests for _gate_reject_reason -- only the four HARD reasons
    documented on its own docstring remain (status_hint awarded/closed, a denylisted domain, a
    dead link/no URL). Everything the old F24 gate used to reject on (low/absent relevance, an
    invalid notice_type, a passed deadline, a stale published_at, an unverified+undated snippet)
    must now pass through untouched -- see TestScanTendersOpenIntake for the end-to-end coverage
    of what happens to those notices once they're stored instead."""

    def _notice(self, **overrides) -> NoticeRaw:
        base = dict(source_id="x", external_ref="x:1", title="t", url="https://example.gov/n/1")
        base.update(overrides)
        return NoticeRaw(**base)

    def test_clean_notice_passes(self):
        notice = self._notice(deadline=dt.date(2099, 1, 1))
        assert _gate_reject_reason(notice, deny_domains=[]) is None

    def test_low_relevance_no_longer_rejected(self):
        """Relevance is no longer this gate's concern at all -- _gate_reject_reason doesn't even
        take an `extract` any more (see TestScanTendersOpenIntake for where relevance now goes:
        relevance_score/intake)."""
        notice = self._notice()
        assert _gate_reject_reason(notice, deny_domains=[]) is None

    def test_invalid_notice_type_no_longer_rejected(self):
        """notice_type isn't a _gate_reject_reason input any more -- it only affects
        _initial_status (via scan_tenders), never whether the notice is stored at all."""
        notice = self._notice()
        assert _gate_reject_reason(notice, deny_domains=[]) is None

    def test_past_deadline_no_longer_rejected(self):
        notice = self._notice(deadline=dt.date(2020, 1, 1))
        assert _gate_reject_reason(notice, deny_domains=[]) is None

    def test_published_over_90_days_no_longer_rejected(self):
        notice = self._notice(published_at=dt.date(2026, 9, 6) - dt.timedelta(days=NOTICE_MAX_AGE_DAYS + 1))
        assert _gate_reject_reason(notice, deny_domains=[]) is None

    def test_denylisted_domain_rejected(self):
        notice = self._notice(url="https://www.scribd.com/document/1/x")
        reason = _gate_reject_reason(notice, deny_domains=["scribd.com"])
        assert reason == "denylisted_domain"

    def test_unverified_undated_no_longer_rejected(self):
        """The exact case that used to be 'unverified_undated' under F24 -- a search-snippet-only
        notice, no URL fetch, no date at all -- is no longer a hard rejection: it has a URL, isn't
        denylisted, and carries no awarded/closed status_hint, so it clears the gate and is stored
        (see TestScanTendersOpenIntake.test_unverified_undated_snippet_now_stored_as_candidate)."""
        notice = self._notice()
        assert _gate_reject_reason(notice, deny_domains=[]) is None

    def test_dead_link_no_url_rejected(self):
        """The one genuinely new hard case: a notice with no URL at all -- nothing for an analyst
        to open or verify -- is still rejected outright."""
        notice = self._notice(url=None)
        reason = _gate_reject_reason(notice, deny_domains=[])
        assert reason == "dead_link"

    def test_status_hint_awarded_rejected(self):
        notice = self._notice(status_hint="awarded")
        reason = _gate_reject_reason(notice, deny_domains=[])
        assert reason == "status_hint_awarded"

    def test_status_hint_closed_rejected(self):
        notice = self._notice(status_hint="closed")
        reason = _gate_reject_reason(notice, deny_domains=[])
        assert reason == "status_hint_closed"


# --------------------------------------------------------------------------
# TENDERS-SAM (2026-09-08, docs/qa/content_review/TENDERS-SAM.md): SAM.gov v2 query/parser fix +
# the negative-keyword/defence-context relevance-demotion signal.
# --------------------------------------------------------------------------


class TestSamGovApiConfig:
    """config/tenders.yaml's sam_gov_api / sam_gov_api_psc entries, post-fix: the real v2 query
    parameters (title/ccode + mandatory postedFrom/postedTo, never the non-existent `keyword`
    param) and a parser format that actually resolves to a real parser function."""

    def test_sam_gov_api_uses_title_not_keyword_param(self):
        sources = {s.id: s for s in load_tender_sources()}
        src = sources["sam_gov_api"]
        params = src.query_params or {}
        assert "keyword" not in params, "keyword is not a documented SAM.gov v2 parameter"
        assert params.get("title") == "{keyword}"

    def test_sam_gov_api_sends_mandatory_date_range(self):
        sources = {s.id: s for s in load_tender_sources()}
        params = sources["sam_gov_api"].query_params or {}
        assert params.get("postedFrom") == "{since_date_us}"
        assert params.get("postedTo") == "{today_us}"
        assert sources["sam_gov_api"].query_lookback_days > 0, (
            "postedFrom/postedTo are mandatory on SAM.gov v2 -- {since_date_us} must not render empty"
        )

    def test_sam_gov_api_parse_hints_resolve_to_a_real_parser(self):
        """The pre-fix bug: parse_hints.format='json' matched neither the per-id _API_PARSERS
        dict nor the generic-format _GENERIC_API_PARSERS dict (keyed 'ocds'/'json_list' only), so
        _fetch_api_json's parser lookup silently returned None and every response parsed to zero
        notices regardless of content. 'json_list' is the correct, resolvable key."""
        sources = {s.id: s for s in load_tender_sources()}
        for source_id in ("sam_gov_api", "sam_gov_api_psc"):
            hints = sources[source_id].parse_hints or {}
            assert hints.get("format") == "json_list"
            assert hints.get("notice_path") == "opportunitiesData"
            assert hints.get("id_field") == "noticeId"
            assert hints.get("date_field") == "postedDate"
            assert hints.get("deadline_field") == "responseDeadLine"
            assert hints.get("url_field") == "uiLink"

    def test_sam_gov_api_psc_uses_ccode_and_naics_psc_codes(self):
        sources = {s.id: s for s in load_tender_sources()}
        src = sources["sam_gov_api_psc"]
        params = src.query_params or {}
        assert params.get("ccode") == "{keyword}"
        assert src.api_query_keywords == ["1240", "5855"]
        # The DOMAIN-gate keyword list must stay the full EO/IR vocabulary, not the PSC codes
        # actually rotated into the query -- same rule test_a15_ted_cpv_source_uses_domain_keywords
        # _for_gate_not_cpv_codes enforces for ted_eu_cpv.
        assert "electro-optical" in src.keywords

    def test_sam_gov_sources_still_gated_by_needs_key_env_var_not_verified(self):
        """verified stays False for both -- _api_json_source_enabled gates purely on
        SAM_GOV_API_KEY being set, unchanged by this fix."""
        sources = {s.id: s for s in load_tender_sources()}
        assert sources["sam_gov_api"].verified is False
        assert sources["sam_gov_api"].verified is False
        assert sources["sam_gov_api"].needs_key_env_var == "SAM_GOV_API_KEY"
        assert sources["sam_gov_api_psc"].needs_key_env_var == "SAM_GOV_API_KEY"


class TestFetchApiJsonUsDateParamSubstitution:
    """{since_date_us}/{today_us} placeholder substitution in _fetch_api_json's query_params
    branch (SAM.gov's own MM/dd/yyyy date-range requirement, alongside the pre-existing
    {since_date} YYYYMMDD placeholder TED uses)."""

    def test_since_date_us_and_today_us_rendered_mm_dd_yyyy(self, monkeypatch):
        captured: dict[str, str] = {}

        def fake_fetch_raw_remote(url, method="GET", json_body=None):
            captured["url"] = url
            return {"json": {"opportunitiesData": []}}

        monkeypatch.setattr("eoa.tenders.scan.fetch_raw_remote", fake_fetch_raw_remote)
        monkeypatch.setenv("SAM_GOV_API_KEY", "test-key")
        src = TenderSource(
            id="sam_gov_api",
            name="x",
            kind="api_json",
            country="US",
            url="https://api.sam.gov/opportunities/v2/search",
            query_params={
                "api_key": "{api_key}",
                "title": "{keyword}",
                "postedFrom": "{since_date_us}",
                "postedTo": "{today_us}",
            },
            keywords=["infrared"],
            needs_key_env_var="SAM_GOV_API_KEY",
            query_lookback_days=29,
        )
        _fetch_api_json(src, "infrared")
        url = captured["url"]
        today_us = dt.date.today().strftime("%m/%d/%Y")
        since_us = (dt.date.today() - dt.timedelta(days=29)).strftime("%m/%d/%Y")
        assert f"postedTo={today_us.replace('/', '%2F')}" in url
        assert f"postedFrom={since_us.replace('/', '%2F')}" in url
        assert "title=infrared" in url

    def test_since_date_us_empty_when_no_lookback_configured(self, monkeypatch):
        captured: dict[str, str] = {}

        def fake_fetch_raw_remote(url, method="GET", json_body=None):
            captured["url"] = url
            return {"json": {"opportunitiesData": []}}

        monkeypatch.setattr("eoa.tenders.scan.fetch_raw_remote", fake_fetch_raw_remote)
        src = TenderSource(
            id="x",
            name="x",
            kind="api_json",
            country="US",
            url="https://example.gov/search",
            query_params={"postedFrom": "{since_date_us}"},
            keywords=["infrared"],
        )
        _fetch_api_json(src, "infrared")
        assert "postedFrom=" in captured["url"]
        # empty value -- no digits, no slashes
        assert captured["url"].split("postedFrom=")[1] in ("", "&") or captured["url"].endswith(
            "postedFrom="
        )


class TestLoadNegativeKeywordsAndDefenceContextSignals:
    def test_loads_real_negative_keywords(self):
        terms = load_negative_keywords()
        assert len(terms) >= 5
        assert "spectroscopy" in [t.casefold() for t in terms]

    def test_loads_real_defence_context_signals(self):
        terms = load_defence_context_signals()
        assert len(terms) >= 5
        assert "military" in [t.casefold() for t in terms]


class TestHasDefenceContext:
    def _notice(self, **overrides) -> NoticeRaw:
        base = dict(source_id="x", external_ref="x:1", title="t", url="https://example.gov/n/1")
        base.update(overrides)
        return NoticeRaw(**base)

    def test_true_when_title_mentions_military(self):
        notice = self._notice(title="Military infrared spectroscopy sensor RFP")
        assert _has_defence_context(notice, DEFAULT_DEFENCE_CONTEXT_SIGNALS) is True

    def test_true_when_only_agency_carries_defence_signal(self):
        notice = self._notice(title="Infrared detector procurement", agency="Department of the Navy")
        assert _has_defence_context(notice, DEFAULT_DEFENCE_CONTEXT_SIGNALS) is True

    def test_false_when_no_defence_signal_anywhere(self):
        notice = self._notice(title="Infrared spectroscopy lab equipment", summary="For a university lab")
        assert _has_defence_context(notice, DEFAULT_DEFENCE_CONTEXT_SIGNALS) is False


class TestNegativeKeywordPenalty:
    def _notice(self, **overrides) -> NoticeRaw:
        base = dict(source_id="x", external_ref="x:1", title="t", url="https://example.gov/n/1")
        base.update(overrides)
        return NoticeRaw(**base)

    def test_negative_term_without_defence_context_returns_term(self):
        notice = self._notice(title="Infrared spectroscopy analyzer for university lab RFQ")
        term = _negative_keyword_penalty(notice, DEFAULT_NEGATIVE_KEYWORDS, DEFAULT_DEFENCE_CONTEXT_SIGNALS)
        assert term == "spectroscopy"

    def test_negative_term_with_defence_context_is_overridden(self):
        notice = self._notice(
            title="Military infrared spectroscopy sensor for battlefield chemical detection RFI"
        )
        term = _negative_keyword_penalty(notice, DEFAULT_NEGATIVE_KEYWORDS, DEFAULT_DEFENCE_CONTEXT_SIGNALS)
        assert term is None

    def test_no_negative_term_returns_none(self):
        notice = self._notice(title="Infrared targeting pod for fighter aircraft RFP")
        term = _negative_keyword_penalty(notice, DEFAULT_NEGATIVE_KEYWORDS, DEFAULT_DEFENCE_CONTEXT_SIGNALS)
        assert term is None


class TestRelevanceScoreForNegativeKeywordCap:
    def _notice(self, **overrides) -> NoticeRaw:
        base = dict(source_id="x", external_ref="x:1", title="t", url="https://example.gov/n/1")
        base.update(overrides)
        return NoticeRaw(**base)

    def test_high_llm_score_capped_when_negative_term_present(self):
        extract = _extract(relevance=9)
        notice = self._notice(title="Infrared spectroscopy reagent kit RFQ")
        score = _relevance_score_for(
            extract, notice, DEFAULT_NEGATIVE_KEYWORDS, DEFAULT_DEFENCE_CONTEXT_SIGNALS
        )
        assert score <= 0.3
        assert score < 0.9  # never raised, only ever lowered

    def test_score_unaffected_when_defence_context_present(self):
        extract = _extract(relevance=9)
        notice = self._notice(title="Military infrared spectroscopy sensor for battlefield use RFI")
        score = _relevance_score_for(
            extract, notice, DEFAULT_NEGATIVE_KEYWORDS, DEFAULT_DEFENCE_CONTEXT_SIGNALS
        )
        assert score == 0.9

    def test_notice_none_is_backward_compatible(self):
        """Every pre-existing caller passes only `extract` -- this must be unaffected."""
        extract = _extract(relevance=9)
        assert _relevance_score_for(extract) == 0.9
        assert _relevance_score_for(None) == 0.5

    def test_no_negative_term_leaves_score_unchanged(self):
        extract = _extract(relevance=7)
        notice = self._notice(title="Infrared targeting pod tender for fighter jets")
        score = _relevance_score_for(
            extract, notice, DEFAULT_NEGATIVE_KEYWORDS, DEFAULT_DEFENCE_CONTEXT_SIGNALS
        )
        assert score == 0.7
