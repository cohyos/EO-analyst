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
    NOTICE_MAX_AGE_DAYS,
    RELEVANCE_MIN_ACCEPT,
    VALID_NOTICE_TYPES,
    NoticeRaw,
    TenderSource,
    TenderStats,
    _apply_domain_country_fallback,
    _apply_extraction_to_notice,
    _archive_stale_closed,
    _country_from_domain,
    _fetch_notice_text,
    _gate_reject_reason,
    _has_procurement_signal,
    _initial_status,
    _is_denylisted_domain,
    _matches_keywords,
    _parse_contracts_finder,
    _parse_search_hits,
    _parse_ted_notices,
    _passes_gate,
    _transition_closed,
    _within_window,
    load_deny_domains,
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
        assert notices[0].url == "https://www.contractsfinder.service.gov.uk/Notice/a1b2c3d4-1111-2222-3333-444455556666"

    def test_award_tag_maps_to_awarded_status_hint(self):
        notices = _parse_contracts_finder(self._payload(), _cf_source())
        assert notices[1].status_hint == "awarded"
        assert notices[0].status_hint is None


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
        assert _is_denylisted_domain("https://ted.europa.eu/en/notice/1", ["wikipedia.org", "reddit.com"]) is False

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
                url="https://sam.gov/opp/1", title="RFI electro-optical", snippet="sources sought", engine="google"
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
            source_id="x", external_ref="x:1", title="HMP Onley: Windows and New Cell Doors", summary="New camera"
        )
        assert _matches_keywords(notice, ["surveillance camera", "electro-optical"]) == []


class TestHasProcurementSignal:
    def test_api_json_source_always_true(self):
        notice = NoticeRaw(source_id="ted_eu", external_ref="x:1", title="Supply of electro-optical pods")
        assert _has_procurement_signal(notice, "api_json", PROCUREMENT_SIGNALS) is True

    def test_search_source_requires_explicit_signal(self):
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="What is Infrared Light?", summary="A physics primer")
        assert _has_procurement_signal(notice, "search", PROCUREMENT_SIGNALS) is False

    def test_search_source_with_signal_present(self):
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="RFI: electro-optical sensor sources sought")
        assert _has_procurement_signal(notice, "search", PROCUREMENT_SIGNALS) is True

    def test_hebrew_signal_recognized(self):
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="מכרז למערכת אלקטרו-אופטית")
        assert _has_procurement_signal(notice, "search", PROCUREMENT_SIGNALS) is True


class TestPassesGate:
    def test_api_json_domain_only_passes(self):
        """TED/Contracts Finder notices rarely say "tender" literally -- domain signal alone suffices."""
        notice = NoticeRaw(source_id="ted_eu", external_ref="x:1", title="Supply of electro-optical targeting pods")
        assert _passes_gate(notice, "api_json", DOMAIN_KEYWORDS, PROCUREMENT_SIGNALS) == ["electro-optical", "targeting pod"]

    def test_search_source_needs_both_signals(self):
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="Infrared: How It Works", summary="A NASA explainer")
        assert _passes_gate(notice, "search", DOMAIN_KEYWORDS, PROCUREMENT_SIGNALS) == []

    def test_search_source_with_both_signals_passes(self):
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="RFI for infrared targeting pod sources sought")
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
        assert _passes_gate(notice, "api_json", DOMAIN_KEYWORDS, PROCUREMENT_SIGNALS) == ["surveillance camera"]


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
            relevant=True, relevance=6, confidence=0.8,
            published_at=dt.date(2026, 8, 1), deadline=dt.date(2026, 10, 1),
        )
        _apply_extraction_to_notice(notice, extract)
        assert notice.published_at == dt.date(2026, 8, 1)
        assert notice.deadline == dt.date(2026, 10, 1)

    def test_never_overwrites_existing_dates(self):
        """TED/Contracts Finder's own structured parse stays authoritative over the LLM."""
        notice = NoticeRaw(
            source_id="x", external_ref="x:1", title="t",
            published_at=dt.date(2026, 1, 1), deadline=dt.date(2026, 2, 1),
        )
        extract = TenderExtract(
            relevant=True, relevance=6, confidence=0.8,
            published_at=dt.date(2099, 1, 1), deadline=dt.date(2099, 1, 1),
        )
        _apply_extraction_to_notice(notice, extract)
        assert notice.published_at == dt.date(2026, 1, 1)
        assert notice.deadline == dt.date(2026, 2, 1)

    def test_fills_agency_and_country_when_generic(self):
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="t", country="other")
        extract = TenderExtract(relevant=True, relevance=6, confidence=0.8, agency="US Air Force", country="US")
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
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="t", country="other", url="https://sam.gov/opp/1")
        _apply_domain_country_fallback(notice)
        assert notice.country == "US"

    def test_does_not_overwrite_real_country(self):
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="t", country="IL", url="https://sam.gov/opp/1")
        _apply_domain_country_fallback(notice)
        assert notice.country == "IL"

    def test_no_match_leaves_country_unchanged(self):
        notice = NoticeRaw(
            source_id="x", external_ref="x:1", title="t", country="other", url="https://www.rfpmart.com/x.html"
        )
        _apply_domain_country_fallback(notice)
        assert notice.country == "other"


class TestFetchNoticeText:
    """F24: _fetch_notice_text now also reports whether the notice was actually verified (a real
    page fetched, or a structured api_json record) vs. only a search snippet/RSS blurb -- that
    second value feeds the "unverified + undated -> reject" gate rule."""

    def test_api_json_source_never_fetches(self):
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="t", summary="s", url="https://ted.europa.eu/x")
        with patch("eoa.tenders.scan.fetch_remote") as mock_fetch:
            text, verified = _fetch_notice_text(notice, "api_json")
        mock_fetch.assert_not_called()
        assert text == "t\n\ns"
        assert verified is True

    def test_search_source_fetches_and_uses_page_text(self):
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="t", summary="s", url="https://example.gov/n/1")
        with patch("eoa.tenders.scan.fetch_remote", return_value={"text": "full notice body with a deadline"}):
            text, verified = _fetch_notice_text(notice, "search")
        assert text == "full notice body with a deadline"
        assert verified is True

    def test_fetch_failure_falls_back_to_title_and_summary_unverified(self):
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="t", summary="s", url="https://example.gov/n/1")
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
        notice = NoticeRaw(source_id="x", external_ref="x:1", title="t", summary="s", url="https://example.gov/n/1")
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

    def cursor(self):
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
    def test_matched_but_llm_deferred_is_gate_rejected_not_inserted(self):
        """F24: with the LLM budget exhausted (llm_budget_s=0), notice 0 (the only one that both
        matches the two-signal gate AND is new) never gets a classification -- the strict gate
        rejects it outright (``no_llm_classification``) rather than falling back to inserting it on
        the deterministic gate's own strength, as it used to before this fix."""
        src = _ted_source()

        with (
            patch("eoa.tenders.scan._collect_source_notices") as mock_collect,
            patch("eoa.tenders.scan._tender_exists") as mock_exists,
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
            patch("eoa.tenders.scan._transition_closed", return_value=0),
            patch("eoa.tenders.scan._archive_stale_closed", return_value=0),
        ):
            notices = _parse_ted_notices(json.loads((FIXTURES / "ted_sample.json").read_text()), src)
            mock_collect.return_value = notices
            # notice[0] relevant+new, notice[1] irrelevant (no keyword match), notice[2] relevant+dup
            mock_exists.side_effect = lambda ref: ref == "ted_eu:555003-2016"
            mock_insert.return_value = (42, 99)

            stats = scan_tenders(since_days=4000, sources=[src], llm_budget_s=0)

        assert isinstance(stats, TenderStats)
        mock_insert.assert_not_called()
        assert stats.sources_scanned == 1
        assert stats.notices_fetched == 3
        assert stats.matched == 2  # notice 0 and notice 2 match the two-signal gate; notice 1 does not
        assert stats.duplicates == 1  # notice 2, already in the DB
        assert stats.inserted == 0
        assert stats.gate_rejected == 1  # notice 0: matched, but no LLM classification available
        assert stats.llm_deferred == 1  # llm_budget_s=0 -> deferred, not called

    def test_html_sources_are_skipped(self):
        src = TenderSource(
            id="il_mod", name="x", kind="html", country="IL", keywords=DOMAIN_KEYWORDS, verified=False
        )
        with (
            patch("eoa.tenders.scan._transition_closed", return_value=0),
            patch("eoa.tenders.scan._archive_stale_closed", return_value=0),
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
        ):
            stats = scan_tenders(sources=[src])
        assert stats.sources_scanned == 0

    def test_source_failure_does_not_stop_scan(self):
        good = _cf_source()
        bad = TenderSource(
            id="broken", name="x", kind="api_json", country="US", url="https://x", keywords=["x"], verified=True
        )
        with (
            patch("eoa.tenders.scan._collect_source_notices") as mock_collect,
            patch("eoa.tenders.scan._transition_closed", return_value=0),
            patch("eoa.tenders.scan._archive_stale_closed", return_value=0),
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
        """The Wikipedia/NASA-style junk case: domain term present, no procurement signal."""
        src = _search_source()
        notice = NoticeRaw(
            source_id=src.id, external_ref="rfi_rfp_news:https://x/infrared", title="What Is Infrared Light?"
        )
        with (
            patch("eoa.tenders.scan._collect_source_notices", return_value=[notice]),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
            patch("eoa.tenders.scan._transition_closed", return_value=0),
            patch("eoa.tenders.scan._archive_stale_closed", return_value=0),
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.matched == 0
        assert stats.inserted == 0


def _common_patches(notice: NoticeRaw) -> ExitStack:
    """A single combined context manager for the three DB stubs every LLM-relevance-gate test
    needs (avoids the parenthesized-`with` star-unpacking trick, which is not valid syntax when
    mixed with `as` clauses -- see CPython's PEG grammar for `with_stmt`)."""
    stack = ExitStack()
    stack.enter_context(patch("eoa.tenders.scan._collect_source_notices", return_value=[notice]))
    stack.enter_context(patch("eoa.tenders.scan._tender_exists", return_value=False))
    stack.enter_context(patch("eoa.tenders.scan._transition_closed", return_value=0))
    stack.enter_context(patch("eoa.tenders.scan._archive_stale_closed", return_value=0))
    return stack


class TestScanTendersLlmRelevanceGate:
    """F24: the strict post-classification gate (_gate_reject_reason) replaces the old three-tier
    relevance rubric (<=2 reject / ==3 unknown / >=4 store). Nothing is inserted without an actual
    LLM classification that clears every check -- see the module's own updated docstring."""

    def test_relevance_below_floor_rejected(self):
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", return_value=(_extract(relevant=False, relevance=1), True)),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.llm_rejected == 1
        assert stats.inserted == 0
        assert stats.matched == 1

    def test_relevance_one_below_floor_rejected(self):
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
        with (
            _common_patches(notice),
            patch(
                "eoa.tenders.scan._llm_classify",
                return_value=(_extract(relevant=True, relevance=RELEVANCE_MIN_ACCEPT - 1), True),
            ),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.llm_rejected == 1

    def test_relevance_at_floor_stored_normally(self):
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
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
        assert stats.inserted == 1
        assert stats.llm_rejected == 0
        assert stats.gate_rejected == 0

    def test_invalid_notice_type_gate_rejected(self):
        """F24: an 'award' notice_type is no longer stored as status='awarded' -- it's dropped
        outright, since the tenders board is for open solicitations, not already-decided
        contracts. Same for 'other' (the model couldn't place it as a real solicitation type)."""
        for bad_type in ("award", "other"):
            src = _ted_source()
            notice = NoticeRaw(source_id=src.id, external_ref=f"ted_eu:{bad_type}", title="Supply of electro-optical widgets")
            with (
                _common_patches(notice),
                patch("eoa.tenders.scan._llm_classify", return_value=(_extract(notice_type=bad_type), True)),
                patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
            ):
                stats = scan_tenders(sources=[src])
            mock_insert.assert_not_called()
            assert stats.gate_rejected == 1
            assert stats.llm_rejected == 0

    def test_expired_deadline_gate_rejected(self):
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", return_value=(_extract(deadline=dt.date(2020, 1, 1)), True)),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.gate_rejected == 1

    def test_stale_published_at_gate_rejected(self):
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
        stale = dt.date.today() - dt.timedelta(days=NOTICE_MAX_AGE_DAYS + 1)
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", return_value=(_extract(published_at=stale), True)),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.gate_rejected == 1

    def test_denylisted_domain_gate_rejected(self):
        """F24: a document-hosting/aggregator reupload (e.g. a Scribd PDF) is rejected even if the
        LLM itself would have scored it as relevant -- the review's "RFP for EO/IR Pods for Heron
        UAV | PDF | Infrared - Scribd" case."""
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

    def test_unverified_undated_snippet_gate_rejected(self):
        """F24: only a search snippet (the actual notice page was never fetched) and no date at
        all -- the 13-undated-'unknown'-rows case from the review -- is rejected outright, not
        stored as 'unknown'."""
        src = _search_source()
        notice = NoticeRaw(source_id=src.id, external_ref=f"{src.id}:1", title="RFI for infrared sensor")
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", return_value=(_extract(), False)),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.gate_rejected == 1

    def test_verified_undated_notice_stored_as_unknown(self):
        """F24: 'unknown' remains possible, but only once the gate has confirmed the page really
        was fetched (page_verified=True) and everything else about the notice checks out."""
        src = _search_source()
        notice = NoticeRaw(source_id=src.id, external_ref=f"{src.id}:1", title="RFI for infrared sensor")
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", return_value=(_extract(), True)),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_called_once()
        assert stats.inserted == 1
        assert stats.gate_rejected == 0

    def test_no_llm_classification_gate_rejected(self):
        """F24: a deferred/unavailable LLM call is itself a rejection now -- no more "insert on
        the deterministic two-signal gate's own strength alone" degrade path."""
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", side_effect=ResourceUnavailable("no vram")),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.llm_deferred == 1
        assert stats.gate_rejected == 1
        assert stats.inserted == 0
        assert stats.llm_rejected == 0

    def test_llm_output_error_gate_rejected(self):
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", side_effect=LLMOutputError("bad json")),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.llm_failed == 1
        assert stats.gate_rejected == 1
        assert stats.inserted == 0

    def test_unexpected_llm_exception_gate_rejected(self):
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
        with (
            _common_patches(notice),
            patch("eoa.tenders.scan._llm_classify", side_effect=UnicodeEncodeError("cp1252", "x", 0, 1, "boom")),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.llm_failed == 1
        assert stats.gate_rejected == 1
        assert stats.inserted == 0

    def test_llm_matched_terms_override_deterministic_ones_when_present(self):
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
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
            source_id=src.id, external_ref=f"{src.id}:1", title="RFI for infrared sensor", country="other"
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
    """Direct unit tests for _gate_reject_reason (F24) -- the exact matrix from
    docs/QA_PROGRAM.md section 4."""

    def _notice(self, **overrides) -> NoticeRaw:
        base = dict(source_id="x", external_ref="x:1", title="t")
        base.update(overrides)
        return NoticeRaw(**base)

    def test_clean_notice_passes(self):
        notice = self._notice(deadline=dt.date(2099, 1, 1))
        reason = _gate_reject_reason(notice, _extract(), page_verified=True, today=dt.date(2026, 9, 6), deny_domains=[])
        assert reason is None

    def test_no_extract_rejected(self):
        notice = self._notice()
        reason = _gate_reject_reason(notice, None, page_verified=True, today=dt.date(2026, 9, 6), deny_domains=[])
        assert reason == "no_llm_classification"

    def test_low_relevance_rejected(self):
        notice = self._notice()
        reason = _gate_reject_reason(
            notice,
            _extract(relevance=RELEVANCE_MIN_ACCEPT - 1),
            page_verified=True,
            today=dt.date(2026, 9, 6),
            deny_domains=[],
        )
        assert reason is not None and reason.startswith("relevance_")

    def test_not_relevant_flag_rejected_even_at_high_relevance(self):
        notice = self._notice()
        reason = _gate_reject_reason(
            notice, _extract(relevant=False, relevance=9), page_verified=True, today=dt.date(2026, 9, 6), deny_domains=[]
        )
        assert reason is not None and reason.startswith("relevance_")

    def test_invalid_notice_type_rejected(self):
        for bad_type in ("award", "other"):
            notice = self._notice()
            reason = _gate_reject_reason(
                notice, _extract(notice_type=bad_type), page_verified=True, today=dt.date(2026, 9, 6), deny_domains=[]
            )
            assert reason == f"notice_type_{bad_type}"

    def test_every_valid_notice_type_accepted(self):
        for good_type in VALID_NOTICE_TYPES:
            notice = self._notice()
            reason = _gate_reject_reason(
                notice, _extract(notice_type=good_type), page_verified=True, today=dt.date(2026, 9, 6), deny_domains=[]
            )
            assert reason is None

    def test_past_deadline_rejected(self):
        notice = self._notice(deadline=dt.date(2020, 1, 1))
        reason = _gate_reject_reason(notice, _extract(), page_verified=True, today=dt.date(2026, 9, 6), deny_domains=[])
        assert reason == "deadline_passed"

    def test_deadline_today_not_yet_passed(self):
        notice = self._notice(deadline=dt.date(2026, 9, 6))
        reason = _gate_reject_reason(notice, _extract(), page_verified=True, today=dt.date(2026, 9, 6), deny_domains=[])
        assert reason is None

    def test_published_over_90_days_rejected(self):
        notice = self._notice(published_at=dt.date(2026, 9, 6) - dt.timedelta(days=NOTICE_MAX_AGE_DAYS + 1))
        reason = _gate_reject_reason(notice, _extract(), page_verified=True, today=dt.date(2026, 9, 6), deny_domains=[])
        assert reason == "published_over_90_days"

    def test_published_exactly_90_days_accepted(self):
        notice = self._notice(published_at=dt.date(2026, 9, 6) - dt.timedelta(days=NOTICE_MAX_AGE_DAYS))
        reason = _gate_reject_reason(notice, _extract(), page_verified=True, today=dt.date(2026, 9, 6), deny_domains=[])
        assert reason is None

    def test_denylisted_domain_rejected(self):
        notice = self._notice(url="https://www.scribd.com/document/1/x")
        reason = _gate_reject_reason(
            notice, _extract(), page_verified=True, today=dt.date(2026, 9, 6), deny_domains=["scribd.com"]
        )
        assert reason == "denylisted_domain"

    def test_unverified_and_undated_rejected(self):
        notice = self._notice()
        reason = _gate_reject_reason(notice, _extract(), page_verified=False, today=dt.date(2026, 9, 6), deny_domains=[])
        assert reason == "unverified_undated"

    def test_unverified_but_dated_accepted(self):
        """A search-snippet-derived notice with no page fetch but a real deadline (e.g. the search
        snippet or an RSS pubDate already carried a date) is not penalized by 'unverified_undated'
        -- that rule only fires when there is no date at all to fall back on."""
        notice = self._notice(deadline=dt.date(2099, 1, 1))
        reason = _gate_reject_reason(notice, _extract(), page_verified=False, today=dt.date(2026, 9, 6), deny_domains=[])
        assert reason is None

    def test_verified_and_undated_accepted(self):
        notice = self._notice()
        reason = _gate_reject_reason(notice, _extract(), page_verified=True, today=dt.date(2026, 9, 6), deny_domains=[])
        assert reason is None

    def test_status_hint_awarded_rejected(self):
        notice = self._notice(status_hint="awarded")
        reason = _gate_reject_reason(notice, _extract(), page_verified=True, today=dt.date(2026, 9, 6), deny_domains=[])
        assert reason == "status_hint_awarded"

    def test_status_hint_closed_rejected(self):
        notice = self._notice(status_hint="closed")
        reason = _gate_reject_reason(notice, _extract(), page_verified=True, today=dt.date(2026, 9, 6), deny_domains=[])
        assert reason == "status_hint_closed"
