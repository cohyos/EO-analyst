"""Round 4 (2026-09-06, docs/REVIEW_2026-09-06_evening.md W1/W5/W6/W7/W9) unit tests -- pure logic
and pure-function/mocked-DB paths only, no live DB/Ollama/network (respx mocks the one live-HTTP
surface, ``eoa.report.link_check``).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_reports_round4.py -q``
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

import httpx
import respx

from eoa.report import clustering, daily, weekly
from eoa.report.clustering import cluster_extra_ns, cluster_items, extra_sources_note_he
from eoa.report.link_check import LinkCheckResult, check_urls
from eoa.tenders import report_section

# --------------------------------------------------------------------------
# shared fakes (same convention as tests/unit/test_report_daily.py)
# --------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.queries: list[tuple[str, dict]] = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params or {}))

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --------------------------------------------------------------------------
# W1: dedupe_forecasts_by_topic
# --------------------------------------------------------------------------


def _forecast(**overrides):
    base = dict(
        platform='כטב"ם MALE',
        buyer_country="US",
        payload_need="מטע\"ד ג'ימבלי EO/IR",
        likelihood=0.5,
        window_from=dt.date(2026, 12, 1),
        window_to=dt.date(2028, 3, 1),
        rationale_he="נימוק",
        sources=["item:1"],
    )
    base.update(overrides)
    return base


class TestDedupeForecastsByTopic:
    def test_collapses_same_topic_different_country(self):
        rows = [
            _forecast(buyer_country="US", likelihood=0.6, sources=["item:1"]),
            _forecast(buyer_country="IL", likelihood=0.8, sources=["item:2"]),
        ]
        out = report_section.dedupe_forecasts_by_topic(rows)
        assert len(out) == 1
        assert out[0]["likelihood"] == 0.8  # highest-likelihood row's own fields win
        assert out[0]["buyer_country"] == "IL"

    def test_merges_sources_from_every_group_member(self):
        rows = [
            _forecast(buyer_country="US", likelihood=0.6, sources=["item:1", "item:2"]),
            _forecast(buyer_country="IL", likelihood=0.8, sources=["item:2", "item:3"]),
        ]
        out = report_section.dedupe_forecasts_by_topic(rows)
        assert out[0]["sources"] == ["item:1", "item:2", "item:3"]  # order-preserving, deduped

    def test_distinct_topics_stay_separate(self):
        rows = [
            _forecast(platform="A", payload_need="x", likelihood=0.5),
            _forecast(platform="B", payload_need="y", likelihood=0.9),
        ]
        out = report_section.dedupe_forecasts_by_topic(rows)
        assert len(out) == 2
        assert out[0]["platform"] == "B"  # sorted likelihood desc

    def test_topic_match_is_case_and_whitespace_insensitive(self):
        rows = [
            _forecast(platform="Male UAV", payload_need=" Gimbal ", likelihood=0.4),
            _forecast(platform="male uav", payload_need="gimbal", likelihood=0.7),
        ]
        out = report_section.dedupe_forecasts_by_topic(rows)
        assert len(out) == 1
        assert out[0]["likelihood"] == 0.7

    def test_cap_limits_result_count(self):
        rows = [_forecast(platform=f"P{i}", payload_need="x", likelihood=i / 10) for i in range(5)]
        out = report_section.dedupe_forecasts_by_topic(rows, cap=2)
        assert len(out) == 2
        assert out[0]["platform"] == "P4"  # highest likelihood first

    def test_empty_input(self):
        assert report_section.dedupe_forecasts_by_topic([]) == []


# --------------------------------------------------------------------------
# W6: attach_forecast_citations / _tenders_forecast_table sources column
# --------------------------------------------------------------------------


class TestAttachForecastCitations:
    def test_registers_trigger_items_and_stamps_citation_ns(self, monkeypatch):
        fake_cursor = _FakeCursor(
            [
                {
                    "id": 10,
                    "url": "https://example.gov/n1",
                    "title": "t10",
                    "published_at": None,
                    "source_name": "s10",
                },
                {
                    "id": 11,
                    "url": "https://example.gov/n2",
                    "title": "t11",
                    "published_at": None,
                    "source_name": "s11",
                },
            ]
        )
        monkeypatch.setattr(report_section, "connection", lambda: _FakeConn(fake_cursor))

        citation_items: list[dict[str, Any]] = [{"id": 1, "n": 1, "title": "existing"}]
        forecasts = [_forecast(sources=["item:10", "item:11"])]
        report_section.attach_forecast_citations(citation_items, forecasts)

        assert forecasts[0]["_citation_ns"] == [2, 3]
        assert [it["id"] for it in citation_items] == [1, 10, 11]

    def test_already_registered_item_is_not_refetched(self, monkeypatch):
        def _boom():
            raise AssertionError("should not query the DB when nothing is missing")

        monkeypatch.setattr(report_section, "connection", _boom)
        citation_items = [{"id": 10, "n": 5, "title": "already there"}]
        forecasts = [_forecast(sources=["item:10"])]
        report_section.attach_forecast_citations(citation_items, forecasts)
        assert forecasts[0]["_citation_ns"] == [5]

    def test_no_sources_gives_empty_citation_ns(self):
        citation_items: list[dict[str, Any]] = []
        forecasts = [_forecast(sources=None)]
        report_section.attach_forecast_citations(citation_items, forecasts)
        assert forecasts[0]["_citation_ns"] == []

    def test_db_failure_degrades_to_empty_citation_ns(self, monkeypatch):
        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(report_section, "connection", _boom)
        citation_items: list[dict[str, Any]] = []
        forecasts = [_forecast(sources=["item:99"])]
        report_section.attach_forecast_citations(citation_items, forecasts)
        assert forecasts[0]["_citation_ns"] == []


class TestTendersForecastTableCitations:
    def test_sources_column_lists_citation_markers(self):
        citation_items: list[dict[str, Any]] = [{"id": 10, "n": 3}]
        data = {"new_forecasts": [_forecast(sources=["item:10"])]}
        tbl = daily._tenders_forecast_table(data, citation_items)
        assert tbl["headers"][-1] == "מקורות"
        assert tbl["rows"][0][-1] == "[3]"

    def test_no_matching_registry_entry_renders_dash(self):
        citation_items: list[dict[str, Any]] = []
        data = {"new_forecasts": [_forecast(sources=None)]}
        tbl = daily._tenders_forecast_table(data, citation_items)
        assert tbl["rows"][0][-1] == "—"


# --------------------------------------------------------------------------
# W5: event kind sanity check + strict test/other anchor + window (date/published_at only)
# --------------------------------------------------------------------------


class TestSanitizeEventKind:
    def test_test_kind_without_vocabulary_is_rewritten_to_other(self):
        ev = {"id": 1, "kind": "test", "title": "Funding allocation announced", "summary_he": ""}
        out = daily._sanitize_event_kind(ev)
        assert out["kind"] == "other"
        assert out is not ev  # never mutates the caller's dict in place

    def test_test_kind_with_hebrew_vocabulary_is_kept(self):
        ev = {"id": 2, "kind": "test", "title": "בוצע ניסוי הדגמה מוצלח", "summary_he": ""}
        assert daily._sanitize_event_kind(ev)["kind"] == "test"

    def test_test_kind_with_english_vocabulary_is_kept(self):
        ev = {"id": 3, "kind": "test", "title": "Live-fire trial completed", "summary_he": ""}
        assert daily._sanitize_event_kind(ev)["kind"] == "test"

    def test_non_test_kind_is_untouched(self):
        ev = {"id": 4, "kind": "contract_award", "title": "no vocabulary here"}
        assert daily._sanitize_event_kind(ev) is ev

    def test_academic_paper_reclassified(self):
        ev = {"id": 5, "kind": "test", "title": "פרסום מאמר מחקר", "summary_he": ""}
        assert daily._sanitize_event_kind(ev)["kind"] == "other"


class TestEventHasSignalStrictAnchor:
    def test_test_kind_with_only_program_is_dropped(self):
        ev = {
            "kind": "test",
            "parties": [],
            "customer": None,
            "program": "MMA",
            "amount_usd": None,
            "date": None,
        }
        assert not daily._event_has_signal(ev)

    def test_other_kind_with_only_program_is_dropped(self):
        ev = {
            "kind": "other",
            "parties": [],
            "customer": None,
            "program": "MMA",
            "amount_usd": None,
            "date": None,
        }
        assert not daily._event_has_signal(ev)

    def test_test_kind_with_a_date_survives(self):
        ev = {
            "kind": "test",
            "parties": [],
            "customer": None,
            "program": "MMA",
            "amount_usd": None,
            "date": dt.date(2026, 9, 1),
        }
        assert daily._event_has_signal(ev)

    def test_contract_award_with_only_program_still_survives(self):
        """Non-test/other kinds keep the original (looser) signal rule."""
        ev = {
            "kind": "contract_award",
            "parties": [],
            "customer": None,
            "program": "MMA",
            "amount_usd": None,
            "date": None,
        }
        assert daily._event_has_signal(ev)


class TestCollectEventsWindowSql:
    def test_sql_drops_fetched_at_created_at_fallback(self, monkeypatch):
        fake_cursor = _FakeCursor([])
        monkeypatch.setattr(daily, "connection", lambda: _FakeConn(fake_cursor))

        daily.collect_events(dt.date(2026, 9, 1), dt.date(2026, 9, 3))

        executed_sql = fake_cursor.queries[0][0]
        assert "fetched_at" not in executed_sql
        assert "created_at" not in executed_sql
        assert "COALESCE(e.date, i.published_at::date)" in executed_sql

    def test_default_window_applies_a_small_start_grace(self, monkeypatch):
        fake_cursor = _FakeCursor([])
        monkeypatch.setattr(daily, "connection", lambda: _FakeConn(fake_cursor))

        no_grace_start, end_ts, _label = daily._period(None, None)
        daily.collect_events()
        params = fake_cursor.queries[0][1]

        graced_start = (no_grace_start - daily._EVENT_DAILY_GRACE).astimezone(daily.JERUSALEM).date()
        assert params["start"] <= graced_start
        assert params["end"] == end_ts.astimezone(daily.JERUSALEM).date()

    def test_explicit_period_gets_no_extra_grace(self, monkeypatch):
        fake_cursor = _FakeCursor([])
        monkeypatch.setattr(daily, "connection", lambda: _FakeConn(fake_cursor))

        daily.collect_events(dt.date(2026, 9, 1), dt.date(2026, 9, 3))
        params = fake_cursor.queries[0][1]
        assert params["start"] == dt.date(2026, 9, 1)
        assert params["end"] == dt.date(2026, 9, 3)


# --------------------------------------------------------------------------
# W7: link_check
# --------------------------------------------------------------------------

_ALIVE_URL = "https://93.184.216.34/notice/alive"
_DEAD_URL = "https://93.184.216.34/notice/dead"
_STALE_URL = "https://93.184.216.34/notice/stale"
_BLOCKED_URL = "http://127.0.0.1/notice/blocked"


class TestLinkCheck:
    def test_alive_url_is_marked_checked_and_alive(self):
        with respx.mock:
            respx.get(_ALIVE_URL).mock(
                return_value=httpx.Response(
                    200, headers={"content-type": "text/html"}, text="<title>Open Tender 2026</title>"
                )
            )
            results = check_urls([_ALIVE_URL])
        result = results[_ALIVE_URL]
        assert result.checked and result.alive
        assert not result.stale

    def test_404_is_marked_dead(self):
        with respx.mock:
            respx.get(_DEAD_URL).mock(return_value=httpx.Response(404))
            results = check_urls([_DEAD_URL])
        result = results[_DEAD_URL]
        assert result.checked
        assert result.alive is False

    def test_old_lone_year_in_title_marks_stale(self):
        with respx.mock:
            respx.get(_STALE_URL).mock(
                return_value=httpx.Response(
                    200,
                    headers={"content-type": "text/html"},
                    text="<title>LITENING Targeting Pod Notice 2015</title>",
                )
            )
            results = check_urls([_STALE_URL], today=dt.date(2026, 9, 6))
        result = results[_STALE_URL]
        assert result.checked and result.alive
        assert result.stale

    def test_recent_year_in_title_is_not_stale(self):
        with respx.mock:
            respx.get(_ALIVE_URL).mock(
                return_value=httpx.Response(
                    200, headers={"content-type": "text/html"}, text="<title>Notice 2026</title>"
                )
            )
            results = check_urls([_ALIVE_URL], today=dt.date(2026, 9, 6))
        assert results[_ALIVE_URL].stale is False

    def test_ssrf_blocked_url_is_checked_and_not_alive(self):
        results = check_urls([_BLOCKED_URL])
        result = results[_BLOCKED_URL]
        assert result.checked
        assert result.alive is False
        assert "blocked" in (result.reason or "")

    def test_budget_exhausted_marks_unchecked(self):
        async def _slow_response(request):
            await asyncio.sleep(1.0)
            return httpx.Response(200)

        with respx.mock:
            respx.get(_ALIVE_URL).mock(side_effect=_slow_response)
            results = check_urls([_ALIVE_URL], total_budget=0.05)
        result = results[_ALIVE_URL]
        assert result.checked is False

    def test_cache_is_reused_and_not_rechecked(self):
        cache = {_ALIVE_URL: LinkCheckResult(url=_ALIVE_URL, checked=True, alive=True)}
        with respx.mock:
            route = respx.get(_ALIVE_URL).mock(return_value=httpx.Response(500))
            out = check_urls([_ALIVE_URL], cache=cache)
            assert route.call_count == 0
        assert out[_ALIVE_URL].alive is True  # cached result wins, no new (failing) request made

    def test_network_error_marks_not_alive(self):
        with respx.mock:
            respx.get(_ALIVE_URL).mock(side_effect=httpx.ConnectError("boom"))
            results = check_urls([_ALIVE_URL])
        assert results[_ALIVE_URL].checked
        assert results[_ALIVE_URL].alive is False


class TestTendersTableLinkFiltering:
    def _data(self, **overrides):
        base = dict(
            id=1,
            title="EO/IR notice",
            agency="MoD",
            country="US",
            deadline=dt.date(2026, 10, 1),
            url=_ALIVE_URL,
            status="open",
            relevance=5,
        )
        base.update(overrides)
        return {"open_tenders": [base], "new_forecasts": []}

    def test_no_cache_renders_unchanged(self):
        tbl = report_section.tenders_table(self._data())
        assert tbl["rows"][0][4] == "פתוח"

    def test_dead_link_row_is_dropped(self):
        cache = {_ALIVE_URL: LinkCheckResult(url=_ALIVE_URL, checked=True, alive=False)}
        tbl = report_section.tenders_table(self._data(), link_cache=cache)
        assert tbl is None  # the only row was dropped -> nothing to show

    def test_stale_link_row_is_relabeled_archived(self):
        cache = {_ALIVE_URL: LinkCheckResult(url=_ALIVE_URL, checked=True, alive=True, stale=True)}
        tbl = report_section.tenders_table(self._data(), link_cache=cache)
        assert tbl["rows"][0][4] == "ארכיון"

    def test_unchecked_link_row_kept_and_marked(self):
        cache = {_ALIVE_URL: LinkCheckResult(url=_ALIVE_URL, checked=False)}
        tbl = report_section.tenders_table(self._data(), link_cache=cache)
        assert "לא אומת" in tbl["rows"][0][4]

    def test_alive_link_row_unchanged(self):
        cache = {_ALIVE_URL: LinkCheckResult(url=_ALIVE_URL, checked=True, alive=True)}
        tbl = report_section.tenders_table(self._data(), link_cache=cache)
        assert tbl["rows"][0][4] == "פתוח"


# --------------------------------------------------------------------------
# W9: clustering
# --------------------------------------------------------------------------


class TestClusterItems:
    def test_shared_dedup_of_clusters_together(self):
        items = [
            {"id": 1, "title": "Elbit wins Army contract", "score": 8, "summary_he": "s"},
            {"id": 2, "title": "Elbit lands US Army deal", "dedup_of": 1, "score": 3},
        ]
        clusters = cluster_items(items)
        assert len(clusters) == 1
        assert clusters[0].primary["id"] == 1
        assert [it["id"] for it in clusters[0].extra] == [2]

    def test_near_identical_titles_cluster_without_dedup_of(self):
        items = [
            {"id": 1, "title": "Rafael delivers first Iron Beam system to IDF", "score": 5},
            {
                "id": 2,
                "title": "Rafael delivers first Iron Beam system to IDF!",
                "score": 9,
                "summary_he": "s",
                "so_what_he": "sw",
                "url": "u",
                "published_at": "2026-09-01",
            },
        ]
        clusters = cluster_items(items)
        assert len(clusters) == 1
        assert clusters[0].primary["id"] == 2  # richer item (more populated fields) wins primary

    def test_distinct_titles_stay_separate(self):
        items = [
            {"id": 1, "title": "Completely unrelated headline about lasers"},
            {"id": 2, "title": "A different story entirely about submarines"},
        ]
        clusters = cluster_items(items)
        assert len(clusters) == 2

    def test_empty_input(self):
        assert cluster_items([]) == []

    def test_order_preserving_anchor_position(self):
        items = [
            {"id": 1, "title": "Elbit wins radar upgrade contract", "score": 1},
            {"id": 2, "title": "Completely unrelated submarine procurement news", "score": 1},
            {"id": 3, "title": "Elbit wins radar upgrade contract", "dedup_of": 1, "score": 5},
        ]
        clusters = cluster_items(items)
        assert len(clusters) == 2
        assert clusters[0].primary["id"] == 3  # richer duplicate wins its group's primary slot
        assert clusters[1].primary["id"] == 2

    # -- 2026-09-17 (story-clustering task): story_id takes precedence over dedup_of/title -------

    def test_story_id_groups_items_with_unrelated_titles_and_no_dedup_of(self):
        """The whole point of `eoa.pipeline.story_clustering`: items with completely different
        titles and no `dedup_of` link, but the SAME `story_id` (computed upstream from embedding
        similarity / corroboration / cross-language entity overlap), must still cluster together --
        this is exactly the SPICE-1000/F-35 case (6 items, 4 separate dedup_of clusters) the task
        brief measured."""
        items = [
            {"id": 10, "title": "Rafael Integrates SPICE 1000 Precision Weapon With F-35", "story_id": 5, "score": 8},
            {"id": 11, "title": "Israel's SPICE 1000 bombs can soon drop from F-35 fighters", "story_id": 5, "score": 4},
            {"id": 12, "title": "Completely unrelated submarine procurement news", "story_id": 99, "score": 6},
        ]
        clusters = cluster_items(items)
        assert len(clusters) == 2
        spice_cluster = next(c for c in clusters if c.primary["id"] in (10, 11))
        assert {it["id"] for it in spice_cluster.all_items} == {10, 11}

    def test_story_id_present_but_none_falls_back_to_dedup_of(self):
        """An item without a computed `story_id` yet (schema behind head, or not yet swept by the
        `stories` stage) must still use the original `dedup_of`/title-similarity fallback -- the
        story-clustering task must not regress pre-existing grouping for un-backfilled items."""
        items = [
            {"id": 1, "title": "Elbit wins Army contract", "score": 8, "summary_he": "s"},
            {"id": 2, "title": "Elbit lands US Army deal", "dedup_of": 1, "score": 3},
        ]
        clusters = cluster_items(items)
        assert len(clusters) == 1
        assert clusters[0].primary["id"] == 1

    def test_hebrew_item_preferred_as_primary_on_richness_tie(self):
        """Design point 2: "Hebrew-language item preferred as primary when richness ties"."""
        items = [
            {
                "id": 1,
                "title": "Rafael Integrates SPICE 1000 With F-35",
                "lang": "en",
                "score": 8,
                "summary_he": "s",
                "so_what_he": "sw",
                "url": "u",
                "published_at": "2026-09-16",
                "story_id": 1,
            },
            {
                "id": 2,
                "title": "SPICE 1000 של רפאל משולב במטוסי F-35",
                "lang": "he",
                "score": 8,
                "summary_he": "s",
                "so_what_he": "sw",
                "url": "u2",
                "published_at": "2026-09-15",
                "story_id": 1,
            },
        ]
        clusters = cluster_items(items)
        assert len(clusters) == 1
        assert clusters[0].primary["id"] == 2  # equal richness -- Hebrew wins the tie

    def test_richness_still_wins_over_hebrew_when_not_tied(self):
        """The Hebrew tie-break only applies on an actual tie -- a genuinely richer English item
        still wins over a thinner Hebrew one."""
        items = [
            {
                "id": 1,
                "title": "Rafael Integrates SPICE 1000 With F-35",
                "lang": "en",
                "score": 8,
                "summary_he": "s",
                "so_what_he": "sw",
                "url": "u",
                "published_at": "2026-09-16",
                "story_id": 1,
            },
            {"id": 2, "title": "SPICE 1000 של רפאל", "lang": "he", "score": 2, "story_id": 1},
        ]
        clusters = cluster_items(items)
        assert clusters[0].primary["id"] == 1


class TestExtraSourcesNote:
    def test_no_extra_returns_empty_string(self):
        cluster = clustering.ItemCluster(primary={"id": 1})
        assert extra_sources_note_he(cluster) == ""

    def test_singular_wording_for_one_extra(self):
        cluster = clustering.ItemCluster(primary={"id": 1}, extra=[{"id": 2}])
        note = extra_sources_note_he(cluster)
        assert "מקור נוסף" in note and "מקורות נוספים" not in note
        assert "1" in note

    def test_plural_wording_for_multiple_extra(self):
        cluster = clustering.ItemCluster(primary={"id": 1}, extra=[{"id": 2}, {"id": 3}])
        note = extra_sources_note_he(cluster)
        assert "2" in note and "מקורות נוספים" in note

    def test_cluster_extra_ns_skips_unnumbered(self):
        cluster = clustering.ItemCluster(primary={"id": 1, "n": 1}, extra=[{"id": 2, "n": 5}, {"id": 3}])
        assert cluster_extra_ns(cluster) == [5]


class TestFallbackTopItemSentencesClustering:
    def test_daily_folds_duplicate_story_into_one_sentence_with_extra_cites(self):
        items = [
            {
                "id": 1,
                "n": 1,
                "title": "Elbit wins Army contract",
                "level": "red",
                "score": 8,
                "so_what_he": "חשוב.",
            },
            {"id": 2, "n": 2, "title": "Elbit lands US Army deal", "dedup_of": 1, "level": "red", "score": 3},
        ]
        sentences = daily._fallback_top_item_sentences(items, limit=5)
        assert len(sentences) == 1
        assert sentences[0].cites == [1, 2]
        assert "מקור נוסף" in sentences[0].text_he

    def test_weekly_same_behaviour(self):
        items = [
            {"id": 1, "n": 1, "title": "Same story reported here", "level": "orange", "score": 8},
            {"id": 2, "n": 2, "title": "Same story reported here", "level": "orange", "score": 3},
        ]
        sentences = weekly._fallback_top_item_sentences(items, limit=5)
        assert len(sentences) == 1
        assert set(sentences[0].cites) == {1, 2}

    def test_no_duplicates_yields_one_sentence_per_item(self):
        items = [
            {"id": 1, "n": 1, "title": "Alpha story about lasers", "level": "red"},
            {"id": 2, "n": 2, "title": "Beta story about submarines", "level": "orange"},
        ]
        sentences = daily._fallback_top_item_sentences(items, limit=5)
        assert [s.cites for s in sentences] == [[1], [2]]
