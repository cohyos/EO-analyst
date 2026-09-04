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
    RELEVANCE_REJECT_MAX,
    RELEVANCE_UNKNOWN,
    NoticeRaw,
    TenderSource,
    TenderStats,
    _has_procurement_signal,
    _initial_status,
    _is_denylisted_domain,
    _matches_keywords,
    _parse_contracts_finder,
    _parse_search_hits,
    _parse_ted_notices,
    _passes_gate,
    _within_window,
    load_deny_domains,
    load_procurement_signals,
    load_tender_sources,
    scan_tenders,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tenders"

DOMAIN_KEYWORDS = ["electro-optical", "infrared", "targeting pod", "thermal imaging", "counter-uas"]
PROCUREMENT_SIGNALS = ["tender", "RFP", "RFI", "sources sought", "request for information"]


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
    base = dict(relevant=True, relevance=6, summary_he="סיכום", matched_terms=["infrared"], entities=[], confidence=0.8)
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
            SearchHit(url="https://en.wikipedia.org/wiki/Infrared", title="Infrared", snippet="What is infrared"),
            SearchHit(url="https://sam.gov/opp/1", title="RFI electro-optical", snippet="sources sought"),
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

    def test_past_deadline_is_closed(self):
        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", deadline=dt.date(2026, 1, 1))
        assert _initial_status(n, dt.date(2026, 9, 4)) == "closed"

    def test_future_deadline_is_open(self):
        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", deadline=dt.date(2027, 1, 1))
        assert _initial_status(n, dt.date(2026, 9, 4)) == "open"

    def test_no_deadline_is_open(self):
        n = NoticeRaw(source_id="x", external_ref="x:1", title="t")
        assert _initial_status(n, dt.date(2026, 9, 4)) == "open"


# --------------------------------------------------------------------------
# scan_tenders orchestration (fully mocked DB/LLM)
# --------------------------------------------------------------------------


class TestScanTendersGateAndDedup:
    def test_matched_inserted_and_duplicate_counted(self):
        src = _ted_source()

        with (
            patch("eoa.tenders.scan._collect_source_notices") as mock_collect,
            patch("eoa.tenders.scan._tender_exists") as mock_exists,
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
            patch("eoa.tenders.scan._transition_closed", return_value=0),
        ):
            notices = _parse_ted_notices(json.loads((FIXTURES / "ted_sample.json").read_text()), src)
            mock_collect.return_value = notices
            # notice[0] relevant+new, notice[1] irrelevant (no keyword match), notice[2] relevant+dup
            mock_exists.side_effect = lambda ref: ref == "ted_eu:555003-2016"
            mock_insert.return_value = (42, 99)

            stats = scan_tenders(since_days=4000, sources=[src], llm_budget_s=0)

        assert isinstance(stats, TenderStats)
        assert stats.sources_scanned == 1
        assert stats.notices_fetched == 3
        assert stats.matched == 2  # notice 0 and notice 2 match the gate; notice 1 does not
        assert stats.duplicates == 1
        assert stats.inserted == 1
        assert stats.llm_deferred == 1  # llm_budget_s=0 -> deferred, not called

    def test_html_sources_are_skipped(self):
        src = TenderSource(
            id="il_mod", name="x", kind="html", country="IL", keywords=DOMAIN_KEYWORDS, verified=False
        )
        with patch("eoa.tenders.scan._transition_closed", return_value=0):
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
        with patch("eoa.tenders.scan._transition_closed", return_value=0):
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
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.matched == 0
        assert stats.inserted == 0


class TestScanTendersLlmRelevanceGate:
    def _one_notice_setup(self, notice: NoticeRaw, src: TenderSource):
        return (
            patch("eoa.tenders.scan._collect_source_notices", return_value=[notice]),
            patch("eoa.tenders.scan._tender_exists", return_value=False),
            patch("eoa.tenders.scan._transition_closed", return_value=0),
        )

    def test_relevance_le_2_rejected_not_stored(self):
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
        with (
            *self._one_notice_setup(notice, src),
            patch("eoa.tenders.scan._llm_classify", return_value=_extract(relevant=False, relevance=1)),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.llm_rejected == 1
        assert stats.inserted == 0
        assert stats.matched == 1

    def test_relevance_exactly_reject_max_rejected(self):
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
        with (
            *self._one_notice_setup(notice, src),
            patch(
                "eoa.tenders.scan._llm_classify",
                return_value=_extract(relevant=True, relevance=RELEVANCE_REJECT_MAX),
            ),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.llm_rejected == 1

    def test_relevance_3_stored_with_unknown_status(self):
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
        with (
            *self._one_notice_setup(notice, src),
            patch(
                "eoa.tenders.scan._llm_classify",
                return_value=_extract(relevant=True, relevance=RELEVANCE_UNKNOWN),
            ),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_called_once()
        _, kwargs = mock_insert.call_args
        assert kwargs["status_override"] == "unknown"
        assert kwargs["relevance"] == RELEVANCE_UNKNOWN
        assert stats.inserted == 1
        assert stats.llm_rejected == 0

    def test_relevance_4_stored_normally(self):
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
        with (
            *self._one_notice_setup(notice, src),
            patch("eoa.tenders.scan._llm_classify", return_value=_extract(relevant=True, relevance=4)),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_called_once()
        _, kwargs = mock_insert.call_args
        assert kwargs["status_override"] is None
        assert kwargs["relevance"] == 4
        assert stats.inserted == 1

    def test_llm_unavailable_still_inserts_deterministically(self):
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
        with (
            *self._one_notice_setup(notice, src),
            patch("eoa.tenders.scan._llm_classify", side_effect=ResourceUnavailable("no vram")),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_called_once()
        args, kwargs = mock_insert.call_args
        assert "relevance" not in kwargs  # deterministic path -- no LLM kwargs passed at all
        assert stats.llm_deferred == 1
        assert stats.inserted == 1
        assert stats.llm_rejected == 0

    def test_llm_output_error_still_inserts_deterministically(self):
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
        with (
            *self._one_notice_setup(notice, src),
            patch("eoa.tenders.scan._llm_classify", side_effect=LLMOutputError("bad json")),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_called_once()
        assert stats.llm_failed == 1
        assert stats.inserted == 1

    def test_unexpected_llm_exception_still_inserts_deterministically(self):
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
        with (
            *self._one_notice_setup(notice, src),
            patch("eoa.tenders.scan._llm_classify", side_effect=UnicodeEncodeError("cp1252", "x", 0, 1, "boom")),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_called_once()
        assert stats.llm_failed == 1
        assert stats.inserted == 1

    def test_llm_matched_terms_override_deterministic_ones_when_present(self):
        src = _ted_source()
        notice = NoticeRaw(source_id=src.id, external_ref="ted_eu:1", title="Supply of electro-optical widgets")
        with (
            *self._one_notice_setup(notice, src),
            patch(
                "eoa.tenders.scan._llm_classify",
                return_value=_extract(relevant=True, relevance=5, matched_terms=["FLIR", "gimbal"]),
            ),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            scan_tenders(sources=[src])
        args, _ = mock_insert.call_args
        assert args[1] == ["FLIR", "gimbal"]
