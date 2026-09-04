"""Tests for eoa.tenders.scan (section 5.2 / FR-5.2) -- pure logic + parsing, no DB/LLM/network.

Every DB- or network-touching function is monkeypatched at the module level (mirroring
tests/unit/test_conferences.py's stubbing style).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from unittest.mock import patch

from eoa.tenders.scan import (
    TenderSource,
    TenderStats,
    _initial_status,
    _matches_keywords,
    _parse_contracts_finder,
    _parse_ted_notices,
    _within_window,
    load_tender_sources,
    scan_tenders,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tenders"


def _ted_source() -> TenderSource:
    return TenderSource(
        id="ted_eu",
        name="TED",
        kind="api_json",
        country="EU",
        url="https://api.ted.europa.eu/v3/notices/search",
        method="POST",
        keywords=["electro-optical", "infrared", "targeting pod", "thermal imaging"],
        verified=True,
    )


def _cf_source() -> TenderSource:
    return TenderSource(
        id="uk_contracts_finder",
        name="UK Contracts Finder",
        kind="api_json",
        country="UK",
        url="https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search",
        keywords=["electro-optical", "counter-uas", "infrared"],
        verified=True,
    )


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
# keyword filter
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

    def test_matches_in_summary_too(self):
        notices = _parse_contracts_finder(
            json.loads((FIXTURES / "contracts_finder_sample.json").read_text()), _cf_source()
        )
        terms = _matches_keywords(notices[0], ["electro-optical", "counter-uas"])
        assert terms  # "electro-optical/infrared trackers" is in the description


# --------------------------------------------------------------------------
# date window / status
# --------------------------------------------------------------------------


class TestWithinWindow:
    def test_recent_notice_within_window(self):
        from eoa.tenders.scan import NoticeRaw

        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", published_at=dt.date(2026, 9, 1))
        assert _within_window(n, since_days=3, today=dt.date(2026, 9, 4)) is True

    def test_old_notice_outside_window(self):
        from eoa.tenders.scan import NoticeRaw

        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", published_at=dt.date(2016, 5, 10))
        assert _within_window(n, since_days=3, today=dt.date(2026, 9, 4)) is False

    def test_undated_notice_always_within_window(self):
        from eoa.tenders.scan import NoticeRaw

        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", published_at=None)
        assert _within_window(n, since_days=3, today=dt.date(2026, 9, 4)) is True


class TestInitialStatus:
    def test_status_hint_wins(self):
        from eoa.tenders.scan import NoticeRaw

        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", status_hint="awarded")
        assert _initial_status(n, dt.date(2026, 9, 4)) == "awarded"

    def test_past_deadline_is_closed(self):
        from eoa.tenders.scan import NoticeRaw

        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", deadline=dt.date(2026, 1, 1))
        assert _initial_status(n, dt.date(2026, 9, 4)) == "closed"

    def test_future_deadline_is_open(self):
        from eoa.tenders.scan import NoticeRaw

        n = NoticeRaw(source_id="x", external_ref="x:1", title="t", deadline=dt.date(2027, 1, 1))
        assert _initial_status(n, dt.date(2026, 9, 4)) == "open"

    def test_no_deadline_is_open(self):
        from eoa.tenders.scan import NoticeRaw

        n = NoticeRaw(source_id="x", external_ref="x:1", title="t")
        assert _initial_status(n, dt.date(2026, 9, 4)) == "open"


# --------------------------------------------------------------------------
# scan_tenders orchestration (fully mocked DB/LLM)
# --------------------------------------------------------------------------


class TestScanTendersOrchestration:
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
        assert stats.matched == 2  # notice 0 and notice 2 match keywords; notice 1 does not
        assert stats.duplicates == 1
        assert stats.inserted == 1
        assert stats.llm_deferred == 1  # llm_budget_s=0 -> deferred, not called

    def test_html_sources_are_skipped(self):
        src = TenderSource(
            id="il_mod", name="x", kind="html", country="IL", keywords=["electro-optical"], verified=False
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
            keywords=["electro-optical"],
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

            def side_effect(src):
                if src.id == "broken":
                    raise RuntimeError("boom")
                return []

            mock_collect.side_effect = side_effect
            stats = scan_tenders(sources=[bad, good])

        assert stats.sources_failed == 1
        assert stats.sources_scanned == 1
