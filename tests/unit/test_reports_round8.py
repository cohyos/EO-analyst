"""Unit tests for the R8-reports package (round-8 QA loop, closing round-7 judge findings
D6 #4/#5/#7/#8, D3/D6 #6, D8, D7 #10 -- docs/qa/loop/round_7_judge.md).

Every DB-touching function is monkeypatched at the module level (no live DB, no Ollama), mirroring
the conventions already used by tests/unit/test_report_daily.py, tests/unit/test_corroboration.py
and tests/unit/test_report_bd_territory.py.

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_reports_round8.py -q``
"""

from __future__ import annotations

import datetime as dt

from eoa.patents import survey
from eoa.report import bd_territory as bdt
from eoa.report import daily, indicators, monthly, weekly

UTC = dt.UTC


# --------------------------------------------------------------------------
# shared fakes (mirrors tests/unit/test_report_daily.py's _FakeCursor/_FakeConn)
# --------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def execute(self, sql, params=None):
        return None

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

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
# Finding #1 -- corroboration markers (D6 #4/#5): _append_item_corroboration_markers must reach
# every appendix row, not just the ones collect_items()/collect_week_items() saw first, and must
# be safe to call more than once on the same rows.
# --------------------------------------------------------------------------


class TestCorroborationMarkers:
    def test_item_marker_appended_per_status(self, monkeypatch):
        monkeypatch.setattr(
            daily,
            "_corroboration_payload_map_safe",
            lambda ids: {
                1: {"status": "single_source", "count": 0},
                2: {"status": "official_primary", "count": 0},
                3: {"status": "corroborated", "count": 2},
                4: {"status": "unknown", "count": 0},
            },
        )
        rows = [{"id": i, "title": f"Title {i}"} for i in (1, 2, 3, 4)]
        daily._append_item_corroboration_markers(rows)
        assert rows[0]["title"] == "Title 1 (מקור יחיד)"
        assert rows[1]["title"] == "Title 2 (מקור ראשוני רשמי)"
        assert rows[2]["title"] == "Title 3 (מאומת ב-2 מקורות)"
        assert rows[3]["title"] == "Title 4"  # unknown status -- no marker

    def test_item_marker_is_idempotent_on_repeat_call(self, monkeypatch):
        monkeypatch.setattr(
            daily,
            "_corroboration_payload_map_safe",
            lambda ids: {1: {"status": "corroborated", "count": 2}},
        )
        rows = [{"id": 1, "title": "Title"}]
        daily._append_item_corroboration_markers(rows)
        daily._append_item_corroboration_markers(rows)
        daily._append_item_corroboration_markers(rows)
        assert rows[0]["title"] == "Title (מאומת ב-2 מקורות)"
        assert rows[0]["title"].count("מאומת ב-2 מקורות") == 1

    def test_item_marker_skips_rows_with_no_title(self, monkeypatch):
        monkeypatch.setattr(
            daily, "_corroboration_payload_map_safe", lambda ids: {1: {"status": "single_source"}}
        )
        rows = [{"id": 1, "title": None}]
        daily._append_item_corroboration_markers(rows)
        assert rows[0]["title"] is None

    def test_event_marker_appended_to_summary_and_is_idempotent(self, monkeypatch):
        monkeypatch.setattr(
            daily,
            "_corroboration_payload_map_safe",
            lambda ids: {7: {"status": "corroborated", "count": 3}},
        )
        rows = [{"item_id": 7, "summary_he": "אירוע כלשהו."}]
        daily._append_event_corroboration_markers(rows)
        daily._append_event_corroboration_markers(rows)
        assert rows[0]["summary_he"] == "אירוע כלשהו. (מאומת ב-3 מקורות)"

    def test_daily_extend_citation_registry_entries_get_marked_by_full_registry_pass(self, monkeypatch):
        """The R8 fix's core mechanism: collect_items()'s own early call only ever sees `items`;
        an entry _extend_citation_registry adds afterward (an event whose item wasn't already in
        `items`) is untouched until the fully-extended `citation_items` list is marked again."""
        monkeypatch.setattr(
            daily,
            "_corroboration_payload_map_safe",
            lambda ids: {2: {"status": "corroborated", "count": 2}},
        )
        items: list[dict] = []
        events = [
            {
                "item_id": 2,
                "item_title": "Event-only item",
                "source_name": "Src",
                "item_url": "https://example.com/2",
                "published_at": None,
            }
        ]
        citation_items, _events_with_n = daily._extend_citation_registry(items, events)
        assert citation_items[0]["title"] == "Event-only item"  # not marked yet
        daily._append_item_corroboration_markers(citation_items)
        assert citation_items[0]["title"] == "Event-only item (מאומת ב-2 מקורות)"
        daily._append_item_corroboration_markers(citation_items)  # idempotent
        assert citation_items[0]["title"].count("מאומת ב-2 מקורות") == 1

    def test_weekly_extend_registry_with_events_entries_get_marked(self, monkeypatch):
        monkeypatch.setattr(
            daily,
            "_corroboration_payload_map_safe",
            lambda ids: {10: {"status": "single_source", "count": 0}},
        )
        events = [
            {
                "item_id": 10,
                "item_title": "Weekly extra item",
                "source_name": "Src",
                "item_url": "https://example.com/10",
                "published_at": None,
            }
        ]
        citation_items, _events_with_n = weekly._extend_registry_with_events([], events)
        assert citation_items[0]["title"] == "Weekly extra item"
        weekly._append_item_corroboration_markers(citation_items)
        assert citation_items[0]["title"] == "Weekly extra item (מקור יחיד)"

    def test_monthly_reuses_weekly_extend_helpers_and_daily_marker_fn(self, monkeypatch):
        """D6 #4: monthly never wired the marker function in at all -- this exercises the exact
        (`_extend_registry_with_ids`, `_append_item_corroboration_markers`) pair monthly.py now
        imports and calls in `build_monthly`."""
        monkeypatch.setattr(
            daily,
            "_corroboration_payload_map_safe",
            lambda ids: {5: {"status": "corroborated", "count": 4}},
        )
        items = [{"id": 5, "n": 1, "title": "Monthly item"}]
        citation_items = monthly._extend_registry_with_ids(items, set())
        assert citation_items[0]["title"] == "Monthly item"
        monthly._append_item_corroboration_markers(citation_items)
        assert citation_items[0]["title"] == "Monthly item (מאומת ב-4 מקורות)"


# --------------------------------------------------------------------------
# Finding #2 -- "Operation Atlantic City" triple-listed (D3/D6 #6): same item + same program (or
# customer+date) across different `kind` values must merge into one row.
# --------------------------------------------------------------------------


class TestMergeSameProgramEvents:
    def test_collapses_three_kinds_sharing_a_program_into_one(self):
        rows = [
            {
                "id": 1,
                "item_id": 3702,
                "kind": "deployment",
                "parties": ["Kongsberg"],
                "customer": None,
                "program": "Operation Atlantic City",
                "date": dt.date(2026, 9, 1),
                "amount_usd": None,
            },
            {
                "id": 2,
                "item_id": 3702,
                "kind": "test",
                "parties": ["Norway MoD"],
                "customer": None,
                "program": "Operation Atlantic City",
                "date": dt.date(2026, 9, 1),
                "amount_usd": None,
            },
            {
                "id": 3,
                "item_id": 3702,
                "kind": "partnership",
                "parties": ["Kongsberg", "Raytheon"],
                "customer": None,
                "program": "Operation Atlantic City",
                "date": dt.date(2026, 9, 1),
                "amount_usd": None,
            },
        ]
        out = daily._merge_same_program_events(rows)
        assert len(out) == 1
        merged = out[0]
        assert merged["kind"] == "deployment"  # more specific than test/partnership
        assert set(merged["parties"]) == {"Kongsberg", "Norway MoD", "Raytheon"}

    def test_contract_award_outranks_deployment(self):
        rows = [
            {"id": 1, "item_id": 1, "kind": "deployment", "parties": ["A"], "program": "P", "customer": None},
            {
                "id": 2,
                "item_id": 1,
                "kind": "contract_award",
                "parties": ["B"],
                "program": "P",
                "customer": None,
            },
        ]
        out = daily._merge_same_program_events(rows)
        assert len(out) == 1
        assert out[0]["kind"] == "contract_award"
        assert set(out[0]["parties"]) == {"A", "B"}

    def test_falls_back_to_customer_and_date_when_program_empty(self):
        rows = [
            {
                "id": 1,
                "item_id": 55,
                "kind": "deployment",
                "parties": ["A"],
                "customer": "US Navy",
                "program": None,
                "date": dt.date(2026, 1, 1),
            },
            {
                "id": 2,
                "item_id": 55,
                "kind": "partnership",
                "parties": ["B"],
                "customer": "US Navy",
                "program": None,
                "date": dt.date(2026, 1, 1),
            },
        ]
        out = daily._merge_same_program_events(rows)
        assert len(out) == 1
        assert set(out[0]["parties"]) == {"A", "B"}

    def test_no_merge_across_different_items(self):
        rows = [
            {"id": 1, "item_id": 1, "kind": "deployment", "parties": ["A"], "program": "Shared Name"},
            {"id": 2, "item_id": 2, "kind": "partnership", "parties": ["B"], "program": "Shared Name"},
        ]
        out = daily._merge_same_program_events(rows)
        assert {r["id"] for r in out} == {1, 2}

    def test_program_group_key_none_without_item_id(self):
        assert daily._program_group_key({"item_id": None, "program": "X"}) is None

    def test_program_group_key_none_without_program_or_customer_date(self):
        assert daily._program_group_key({"item_id": 1, "program": "", "customer": "", "date": None}) is None

    def test_dedup_events_end_to_end_atlantic_city_scenario(self):
        rows = [
            {
                "id": 1,
                "item_id": 3702,
                "kind": "deployment",
                "title": "Deployment note",
                "parties": ["Kongsberg"],
                "customer": None,
                "program": "Operation Atlantic City",
                "date": dt.date(2026, 9, 1),
                "amount_usd": None,
                "summary_he": None,
                "item_title": None,
            },
            {
                "id": 2,
                "item_id": 3702,
                "kind": "test",
                "title": "Test note",
                "parties": ["Norway MoD"],
                "customer": None,
                "program": "Operation Atlantic City",
                "date": dt.date(2026, 9, 1),
                "amount_usd": None,
                "summary_he": None,
                "item_title": None,
            },
            {
                "id": 3,
                "item_id": 3702,
                "kind": "partnership",
                "title": "Partnership note",
                "parties": ["Kongsberg", "Raytheon"],
                "customer": None,
                "program": "Operation Atlantic City",
                "date": dt.date(2026, 9, 1),
                "amount_usd": None,
                "summary_he": None,
                "item_title": None,
            },
            # An unrelated event on a different item must survive untouched.
            {
                "id": 4,
                "item_id": 9999,
                "kind": "launch",
                "title": "Unrelated launch",
                "parties": ["Other Co"],
                "customer": None,
                "program": None,
                "date": dt.date(2026, 9, 2),
                "amount_usd": None,
                "summary_he": None,
                "item_title": None,
            },
        ]
        out = daily._dedup_events(rows)
        atlantic_rows = [r for r in out if r.get("item_id") == 3702]
        assert len(atlantic_rows) == 1
        assert atlantic_rows[0]["kind"] == "deployment"
        assert any(r["item_id"] == 9999 for r in out)


# --------------------------------------------------------------------------
# Finding #3 -- indicator-watchlist evidence column (D6 #7): [n] for any status when a match
# exists, "—" only when it truly doesn't.
# --------------------------------------------------------------------------


class TestIndicatorEvidenceColumn:
    def test_matured_row_uses_precise_evidence_id(self):
        row = {"_row_status": "matured", "matured_evidence_item_id": 42, "text_he": "X"}
        by_id = {42: {"id": 42, "n": 7}}
        assert indicators._evidence_cell(row, by_id, []) == "[7]"

    def test_open_row_finds_fresh_match_via_key_terms(self):
        row = {"_row_status": "open", "text_he": "מערכת DROIC צפויה להתפרס בקרוב"}
        items = [{"id": 1, "n": 3, "title": "עדכון על מערכת ה-DROIC", "summary_he": "", "so_what_he": ""}]
        by_id = {1: {"id": 1, "n": 3}}
        assert indicators._evidence_cell(row, by_id, items) == "[3]"

    def test_dropped_row_always_stays_dash_even_with_a_matching_item(self):
        row = {"_row_status": "dropped", "text_he": "מערכת DROIC ירדה מהמעקב"}
        items = [{"id": 1, "n": 1, "title": "DROIC news", "summary_he": "", "so_what_he": ""}]
        by_id = {1: {"id": 1, "n": 1}}
        assert indicators._evidence_cell(row, by_id, items) == "—"

    def test_no_match_stays_dash(self):
        row = {"_row_status": "open", "text_he": "אין כאן מונח מזוהה כלל"}
        assert indicators._evidence_cell(row, {}, []) == "—"

    def test_render_watchlist_table_weekly_shows_evidence_for_open_row(self):
        rows = [
            {
                "id": 1,
                "text_he": "מערכת DROIC צפויה להתפרס",
                "first_seen": dt.datetime(2026, 8, 1, tzinfo=UTC),
                "last_seen": dt.datetime(2026, 9, 1, tzinfo=UTC),
                "status": "open",
                "kind": "weekly",
                "_row_status": "open",
            }
        ]
        items = [{"id": 9, "n": 2, "title": "DROIC עדכון", "summary_he": "", "so_what_he": ""}]
        citation_items = [{"id": 9, "n": 2}]
        section = indicators.render_watchlist_table(rows, citation_items, items, kind="weekly")
        assert section is not None
        assert "| [2] |" in section["body_he"]


# --------------------------------------------------------------------------
# Finding #4 -- daily indicator-table per-story cap (D6 #8).
# --------------------------------------------------------------------------


def _watchlist_row(
    row_id: int,
    text: str,
    *,
    first_seen: dt.datetime,
    last_seen: dt.datetime | None = None,
    status: str = "open",
) -> dict:
    return {
        "id": row_id,
        "text_he": text,
        "first_seen": first_seen,
        "last_seen": last_seen or first_seen,
        "status": status,
        "kind": "daily",
        "_row_status": status,
    }


class TestIndicatorClusterCap:
    def test_cluster_key_groups_by_top2_content_tokens(self):
        key1 = indicators._cluster_key("Volkswagenmanufacturing Rafaelpartnership note one")
        key2 = indicators._cluster_key("Volkswagenmanufacturing Rafaelpartnership note two")
        key3 = indicators._cluster_key("Estoniaprocurement Slingdefense note three")
        assert key1 == key2
        assert key1 != key3

    def test_caps_rows_per_cluster_preferring_evidence_then_recency(self):
        d0 = dt.datetime(2026, 8, 1, tzinfo=UTC)
        r1 = _watchlist_row(1, "Volkswagenmanufacturing Rafaelpartnership alpha", first_seen=d0)
        r2 = _watchlist_row(
            2,
            "Volkswagenmanufacturing Rafaelpartnership beta",
            first_seen=d0 + dt.timedelta(days=1),
            last_seen=d0 + dt.timedelta(days=5),
        )
        r3 = _watchlist_row(
            3, "Volkswagenmanufacturing Rafaelpartnership gamma", first_seen=d0 + dt.timedelta(days=2)
        )
        r3["_row_status"] = "matured"
        r3["matured_evidence_item_id"] = 100
        r4 = _watchlist_row(
            4,
            "Volkswagenmanufacturing Rafaelpartnership delta",
            first_seen=d0 + dt.timedelta(days=3),
            last_seen=d0 + dt.timedelta(days=4),
        )
        kept = indicators._cap_watchlist_rows([r1, r2, r3, r4], max_per_cluster=3, max_total=8)
        kept_ids = {r["id"] for r in kept}
        assert len(kept_ids) == 3
        assert 3 in kept_ids  # has evidence -- always kept
        assert 1 not in kept_ids  # least recent, no evidence -- dropped

    def test_caps_total_rows_keeping_oldest_first_seen(self):
        d0 = dt.datetime(2026, 8, 1, tzinfo=UTC)
        rows = []
        clusters = [
            ("Volkswagenmanufacturing Rafaelpartnership", 0),
            ("Estoniaprocurement Slingdefense", 3),
            ("Reapersuccessor Timelineaccelerating", 6),
        ]
        row_id = 1
        for prefix, offset in clusters:
            for i in range(3):
                rows.append(
                    _watchlist_row(
                        row_id, f"{prefix} note {i}", first_seen=d0 + dt.timedelta(days=offset + i)
                    )
                )
                row_id += 1
        assert len(rows) == 9
        kept = indicators._cap_watchlist_rows(rows, max_per_cluster=3, max_total=8)
        assert len(kept) == 8
        # the single newest first_seen row (id 9, offset 6+2=8) is the one trimmed
        assert 9 not in {r["id"] for r in kept}

    def test_render_watchlist_table_daily_and_weekly_both_apply_cap(self):
        # R12-reports #2 (round-11 judge D6 worst #4, docs/qa/loop/round_12_fixes.md): this test
        # used to be named "..._but_weekly_does_not" and asserted the weekly table was left
        # uncapped at 9 rows -- round 11 found a live weekly indicator table had grown to 10 rows
        # against the brief's own <= 8-row cap, so `indicators.render_watchlist_table` now runs
        # the same per-story/8-row cap for every ``kind`` (see that module's own updated
        # docstring). Updated in place rather than left contradicting the new intended behavior.
        d0 = dt.datetime(2026, 8, 1, tzinfo=UTC)
        rows = []
        clusters = [
            ("Volkswagenmanufacturing Rafaelpartnership", 0),
            ("Estoniaprocurement Slingdefense", 3),
            ("Reapersuccessor Timelineaccelerating", 6),
        ]
        row_id = 1
        for prefix, offset in clusters:
            for i in range(3):
                rows.append(
                    _watchlist_row(
                        row_id, f"{prefix} note {i}", first_seen=d0 + dt.timedelta(days=offset + i)
                    )
                )
                row_id += 1

        daily_section = indicators.render_watchlist_table(rows, [], [], kind="daily")
        weekly_section = indicators.render_watchlist_table(rows, [], [], kind="weekly")
        monthly_section = indicators.render_watchlist_table(rows, [], [], kind="monthly")
        # header (2 lines) + rows -- all three kinds capped at 8 now
        assert len(daily_section["body_he"].splitlines()) == 2 + 8
        assert len(weekly_section["body_he"].splitlines()) == 2 + 8
        assert len(monthly_section["body_he"].splitlines()) == 2 + 8


# --------------------------------------------------------------------------
# Finding #5 -- patent-survey sources-appendix reliability column (D8).
# --------------------------------------------------------------------------


class TestSurveyAppendixReliability:
    def test_db_item_kind_returns_none_docx_builder_resolves_it_itself(self):
        assert survey._appendix_reliability({"kind": "db_item", "url": "https://example.com"}) is None

    def test_no_url_returns_none(self):
        assert survey._appendix_reliability({"kind": "patent"}) is None

    def test_matches_source_host(self, monkeypatch):
        monkeypatch.setattr(survey, "_SOURCE_HOST_RELIABILITY_CACHE", None, raising=False)
        fake_cursor = _FakeCursor([{"url": "https://patents.google.com/x", "reliability": 5}])
        monkeypatch.setattr(survey, "connection", lambda timeout=5: _FakeConn(fake_cursor))
        val = survey._appendix_reliability(
            {"kind": "patent", "url": "https://patents.google.com/patent/US123"}
        )
        assert val == {"kind": "primary", "score": 1.0, "label": None}

    def test_no_host_match_returns_none(self, monkeypatch):
        # R9-reports #4 (round-8 judge D8 #9): queries a host that is neither a monitored `sources`
        # row nor one of `_PATENT_OFFICE_HOSTS` -- patents.google.com itself moved off this "no
        # match at all" case once that fix shipped (see TestPatentOfficeHostReliability below), so
        # this now uses a host distinct from both to keep testing the genuine no-match path.
        monkeypatch.setattr(survey, "_SOURCE_HOST_RELIABILITY_CACHE", None, raising=False)
        fake_cursor = _FakeCursor([{"url": "https://defensenews.com/x", "reliability": 5}])
        monkeypatch.setattr(survey, "connection", lambda timeout=5: _FakeConn(fake_cursor))
        val = survey._appendix_reliability(
            {"kind": "patent", "url": "https://example-patent-registry.test/patent/US999"}
        )
        assert val is None

    def test_secondary_reliability_below_four(self, monkeypatch):
        monkeypatch.setattr(survey, "_SOURCE_HOST_RELIABILITY_CACHE", None, raising=False)
        fake_cursor = _FakeCursor([{"url": "https://uspto.gov/x", "reliability": 3}])
        monkeypatch.setattr(survey, "connection", lambda timeout=5: _FakeConn(fake_cursor))
        val = survey._appendix_reliability({"kind": "patent", "url": "https://uspto.gov/patent/US1"})
        assert val == {"kind": "secondary", "score": 0.6, "label": None}


# --------------------------------------------------------------------------
# Finding #6 -- bd_kr / genuinely-empty-territory stub (D7 #10).
# --------------------------------------------------------------------------


class TestBdTerritoryEmptyStub:
    def test_pending_expansion_search_id_returns_latest_job_id(self, monkeypatch):
        # `_pending_expansion_search_id` goes through `_fetchall` (its own "SELECT id ..." query,
        # a separate query shape from `_has_pending_expansion_search`'s "SELECT 1 ...").
        monkeypatch.setattr(bdt, "_fetchall", lambda q, p=None: [{"id": 555}])
        assert bdt._pending_expansion_search_id("KR") == 555

    def test_pending_expansion_search_id_none_when_no_rows(self, monkeypatch):
        monkeypatch.setattr(bdt, "_fetchall", lambda q, p=None: [])
        assert bdt._pending_expansion_search_id("KR") is None

    def test_enqueue_reuses_pending_job_id_without_enqueueing_again(self, monkeypatch):
        # `_has_pending_expansion_search` (the gate) and `_pending_expansion_search_id` (the id
        # report-back) both go through `_fetchall`, but with different query shapes/row keys
        # ("SELECT 1 ..." vs "SELECT id ...") -- the fake distinguishes them by query text, same
        # as the two real queries do.
        def fake_fetchall(query, params=None):
            if "SELECT id" in query:
                return [{"id": 42}]
            return [{"1": 1}]

        monkeypatch.setattr(bdt, "_fetchall", fake_fetchall)

        def _fail(*a, **k):
            raise AssertionError("enqueue_job should not be called when a job is already pending")

        monkeypatch.setattr("eoa.memory.relational.enqueue_job", _fail)
        assert bdt._enqueue_territory_expansion_search("KR") == 42

    def test_enqueue_creates_new_job_when_none_pending(self, monkeypatch):
        monkeypatch.setattr(bdt, "_fetchall", lambda q, p=None: [])
        monkeypatch.setattr("eoa.memory.relational.enqueue_job", lambda kind, payload, priority=5: 99)
        assert bdt._enqueue_territory_expansion_search("KR") == 99

    def test_enqueue_returns_none_on_db_failure(self, monkeypatch):
        def _boom(q, p=None):
            raise RuntimeError("db down")

        monkeypatch.setattr(bdt, "_fetchall", _boom)
        assert bdt._enqueue_territory_expansion_search("KR") is None

    @staticmethod
    def _mock_empty_collectors(monkeypatch, tmp_path, *, dormant: list[str]):
        monkeypatch.setattr("eoa.patents.report_section.collect_patents_bd",
                            lambda territory: {"territory": territory, "competitor_patents": []})
        monkeypatch.setattr(bdt, "collect_market_items", lambda t, s, e, max_items=250: [])
        monkeypatch.setattr(bdt, "collect_platform_events", lambda t, s, e, limit=25: [])
        monkeypatch.setattr(
            bdt, "collect_tenders_and_forecasts", lambda t, limit=20: {"tenders": [], "forecasts": []}
        )
        monkeypatch.setattr(bdt, "collect_active_competitors", lambda t, ids, s, e, limit=15: [])
        monkeypatch.setattr(
            bdt,
            "collect_conferences_for_territory",
            lambda t, months=12, international_limit=5: {"territory": [], "international": []},
        )
        monkeypatch.setattr(
            bdt, "collect_dormant_watchlist_competitors", lambda t, active, limit=20: list(dormant)
        )
        monkeypatch.setattr(bdt, "_persist_report", lambda *a, **k: 1)
        monkeypatch.setattr(bdt, "_report_path", lambda territory, period_end, ext: tmp_path / f"bd.{ext}")

        def _fail_if_called(*a, **k):
            raise AssertionError("chat_structured should not be called with zero items")

        monkeypatch.setattr("eoa.report.bd_territory.chat_structured", _fail_if_called)

    def test_empty_stub_has_bluf_line_what_checked_section_and_stays_d7_exempt(self, monkeypatch, tmp_path):
        self._mock_empty_collectors(monkeypatch, tmp_path, dormant=["Hanwha"])
        monkeypatch.setattr(bdt, "_enqueue_territory_expansion_search", lambda code: 4242)

        paths = bdt.build_bd_territory("KR", 90, period_end=dt.date(2026, 9, 6))
        text = paths.md.read_text(encoding="utf-8")

        assert "שורה תחתונה: לא זוהתה פעילות בטריטוריה בחלון הנבדק; הופעלה חקירה ממוקדת." in text
        assert "מה נבדק" in text
        assert "Hanwha" in text
        assert "4242" in text

        from eoa.qa.d7_bd_report import _EMPTY_TERRITORY_MARKER_HE

        assert _EMPTY_TERRITORY_MARKER_HE in text

    def test_empty_stub_without_watchlist_uses_honest_bluf_variant(self, monkeypatch, tmp_path):
        self._mock_empty_collectors(monkeypatch, tmp_path, dormant=[])

        paths = bdt.build_bd_territory("DE", 90, period_end=dt.date(2026, 9, 6))
        text = paths.md.read_text(encoding="utf-8")

        assert "שורה תחתונה: לא זוהתה פעילות בטריטוריה בחלון הנבדק." in text
        assert "הופעלה חקירה ממוקדת" not in text
        assert "לא הוגדרו חברות מעקב" in text
