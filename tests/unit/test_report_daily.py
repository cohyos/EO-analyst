"""Unit tests for eoa.report.daily's pure-logic helpers (no DB, no Ollama): the F3 collection
window, F7 section-title normalization, F9/F16 event dedup/filter/sort, and the F6 tenders
forecast table.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_report_daily.py -q``
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from eoa.llm.schemas.analysis import DailyReportDraft, ReportSection
from eoa.report import daily

# --------------------------------------------------------------------------
# F3: _period() collection window
# --------------------------------------------------------------------------


def test_period_both_none_is_24h_ending_now():
    before = dt.datetime.now(daily.JERUSALEM)
    start, end, label = daily._period(None, None)
    after = dt.datetime.now(daily.JERUSALEM)

    assert start.tzinfo is not None and end.tzinfo is not None  # tz-aware, not naive
    span = end - start
    assert dt.timedelta(hours=23, minutes=59) <= span <= dt.timedelta(hours=24, minutes=1)
    # end is "now" (within the test's own execution window), not just today's date at midnight
    assert before - dt.timedelta(seconds=5) <= end <= after + dt.timedelta(seconds=5)
    assert label == after.date()


def test_period_explicit_dates_span_whole_jerusalem_days():
    start_date = dt.date(2026, 9, 1)
    end_date = dt.date(2026, 9, 3)
    start, end, label = daily._period(start_date, end_date)

    assert label == end_date
    assert start.astimezone(daily.JERUSALEM).date() == start_date
    assert start.astimezone(daily.JERUSALEM).time() == dt.time.min
    assert end.astimezone(daily.JERUSALEM).date() == end_date
    # end is the last instant of the day, not midnight of the next one
    assert end.astimezone(daily.JERUSALEM).hour == 23


def test_period_single_explicit_date_is_one_day():
    d = dt.date(2026, 9, 4)
    start, end, label = daily._period(d, None)
    assert label == d
    assert start.astimezone(daily.JERUSALEM).date() == d
    assert end.astimezone(daily.JERUSALEM).date() == d


# --------------------------------------------------------------------------
# F7: section title normalization (taxonomy label overrides whatever the model wrote)
# --------------------------------------------------------------------------


def test_normalize_section_titles_uses_taxonomy_label():
    draft = DailyReportDraft(
        exec_summary_he="תקציר.",
        sections=[
            ReportSection(title_he="נגד כטב", domain="c_uas", prose_he="פרוזה."),
        ],
        outlook_he="",
        open_points_he=[],
    )
    normalized = daily._normalize_section_titles(draft)
    assert normalized.sections[0].title_he == 'נגד כטב"מים (C-UAS)'


def test_normalize_section_titles_leaves_unknown_domain_alone():
    draft = DailyReportDraft(
        exec_summary_he="תקציר.",
        sections=[ReportSection(title_he="כותרת כלשהי", domain="", prose_he="פרוזה.")],
        outlook_he="",
        open_points_he=[],
    )
    normalized = daily._normalize_section_titles(draft)
    assert normalized.sections[0].title_he == "כותרת כלשהי"


# --------------------------------------------------------------------------
# F9/F16: event dedup / information-free filtering / sort / cap
# --------------------------------------------------------------------------


def test_dedup_events_keeps_richest_of_duplicate_group():
    rows = [
        {"id": 1, "kind": "contract_award", "parties": ["Elbit"], "customer": "USAF", "program": None},
        {
            "id": 2,
            "kind": "contract_award",
            "parties": ["elbit"],  # same identity, case-insensitive
            "customer": "usaf",
            "program": None,
            "amount_usd": 80_000_000,  # richer: also has an amount
        },
    ]
    out = daily._dedup_events(rows)
    assert len(out) == 1
    assert out[0]["id"] == 2


def test_dedup_events_keeps_distinct_kinds_separate():
    rows = [
        {"id": 1, "kind": "contract_award", "parties": ["A"], "customer": None, "program": None},
        {"id": 2, "kind": "launch", "parties": ["A"], "customer": None, "program": None},
    ]
    out = daily._dedup_events(rows)
    assert {r["id"] for r in out} == {1, 2}


def test_event_has_signal_drops_information_free_rows():
    assert not daily._event_has_signal({"parties": [], "customer": None, "program": None, "amount_usd": None})
    assert daily._event_has_signal(
        {"parties": ["Elbit"], "customer": None, "program": None, "amount_usd": None}
    )
    assert daily._event_has_signal({"parties": [], "customer": "USAF", "program": None, "amount_usd": None})
    assert daily._event_has_signal({"parties": [], "customer": None, "program": None, "amount_usd": 0})


def test_collect_events_sort_and_limit_via_helpers():
    rows = [
        {"id": 1, "date": dt.date(2026, 9, 1), "amount_usd": 10},
        {"id": 2, "date": dt.date(2026, 9, 3), "amount_usd": 5},
        {"id": 3, "date": dt.date(2026, 9, 3), "amount_usd": 50},
        {"id": 4, "date": None, "amount_usd": None},
    ]
    ordered = sorted(rows, key=daily._event_sort_key, reverse=True)
    assert [r["id"] for r in ordered] == [3, 2, 1, 4]


# --------------------------------------------------------------------------
# F6: tenders forecast sub-table (replaces the old tenders_extra_section bulleted block)
# --------------------------------------------------------------------------


def test_tenders_forecast_table_none_when_no_forecasts():
    assert daily._tenders_forecast_table({"open_tenders": [], "new_forecasts": []}) is None


# --------------------------------------------------------------------------
# Q3-14 (docs/qa/findings_Q3_r1.md): exec summary must not say "no findings" next to full tables
# --------------------------------------------------------------------------


class TestTableCounts:
    def test_total_sums_all_fields(self):
        counts = daily.TableCounts(events=2, open_tenders=1, new_forecasts=3, deep_search=0)
        assert counts.total == 6

    def test_context_he_empty_when_all_zero(self):
        assert daily.TableCounts().context_he() == "אין (כל הטבלאות ריקות בתקופה זו)."

    def test_context_he_lists_only_nonzero_fields(self):
        text = daily.TableCounts(events=2, open_tenders=0, new_forecasts=1, deep_search=0).context_he()
        assert "2 אירועים" in text
        assert "1 תחזיות" in text
        assert "מכרזים פתוחים" not in text


class TestDraftReportTablesOnlyFallback:
    def test_empty_items_and_empty_tables_uses_no_items_draft(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("chat_structured must not be called with zero items and zero tables")

        monkeypatch.setattr(daily, "chat_structured", boom)
        draft = daily.draft_report([], table_counts=daily.TableCounts())
        assert "אין ממצאים" in draft.exec_summary_he
        assert draft.sections == []

    def test_empty_items_but_nonempty_tables_summarizes_tables_instead(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("chat_structured must not be called with zero items")

        monkeypatch.setattr(daily, "chat_structured", boom)
        counts = daily.TableCounts(events=3, open_tenders=2, new_forecasts=0, deep_search=1)
        draft = daily.draft_report([], table_counts=counts)
        assert "אין ממצאים" not in draft.exec_summary_he
        assert "3 אירועים" in draft.exec_summary_he
        assert "2 מכרזים פתוחים" in draft.exec_summary_he
        assert "1 חקירות עומק" in draft.exec_summary_he

    def test_nonempty_items_still_calls_llm_with_counts_in_prompt(self, monkeypatch):
        captured_messages = []

        def fake_chat_structured(role, schema, messages, **kw):
            captured_messages.append(messages)
            return DailyReportDraft(
                exec_summary_he="תקציר [1].", sections=[], outlook_he="", open_points_he=[]
            )

        monkeypatch.setattr(daily, "chat_structured", fake_chat_structured)
        items = [{"n": 1, "id": 1, "title": "t", "domain": "c_uas", "summary_he": "s", "so_what_he": "so"}]
        counts = daily.TableCounts(events=5)
        daily.draft_report(items, table_counts=counts)
        prompt = captured_messages[0][1]["content"]
        assert "5 אירועים" in prompt


# --------------------------------------------------------------------------
# Q3-15 (docs/qa/findings_Q3_r1.md): section domain validation -- never render a raw slug
# --------------------------------------------------------------------------


class TestDomainLabelFuzzyMatch:
    def test_exact_taxonomy_key_resolves_normally(self):
        assert daily._domain_label("c_uas") != daily._UNKNOWN_DOMAIN_LABEL_HE

    def test_near_miss_slug_fuzzy_matches_real_key(self):
        # "naval_eo_ir" is not a real taxonomy key, but is close to "naval_surveillance".
        label = daily._domain_label("naval_eo_ir")
        assert label == daily._domain_label("naval_surveillance")
        assert label != "naval_eo_ir"

    def test_completely_unrelated_slug_falls_back_to_unknown_label(self):
        assert daily._domain_label("xyz_totally_made_up_123") == daily._UNKNOWN_DOMAIN_LABEL_HE

    def test_raw_slug_never_rendered(self):
        for domain in ("naval_eo_ir", "xyz_totally_made_up_123", "some_other_bogus_domain"):
            assert daily._domain_label(domain) != domain

    def test_empty_domain_is_general(self):
        assert daily._domain_label(None) == "כללי"
        assert daily._domain_label("") == "כללי"


class TestNormalizeSectionTitlesNeverRawSlug:
    def test_unknown_domain_gets_fallback_label_not_raw_slug(self):
        draft = DailyReportDraft(
            exec_summary_he="תקציר.",
            sections=[ReportSection(title_he="naval_eo_ir", domain="naval_eo_ir", prose_he="פרוזה [1].")],
            outlook_he="",
            open_points_he=[],
        )
        normalized = daily._normalize_section_titles(draft)
        assert normalized.sections[0].title_he != "naval_eo_ir"


class TestDeepSearchOrphanFilter:
    """Q3-15: the deep-investigations section must not include an open question about an item
    that isn't in this report's own item list."""

    def test_filters_entries_whose_item_is_not_in_report(self):
        items = [{"id": 1}, {"id": 2}]
        deep_search = [
            {"job_id": 1, "trigger_item_id": 1, "question": "in report"},
            {"job_id": 2, "trigger_item_id": 99, "question": "orphaned"},
            {"job_id": 3, "trigger_item_id": None, "question": "not item-specific"},
        ]
        filtered = daily._filter_deep_search_to_items_included(deep_search, items)
        assert {d["job_id"] for d in filtered} == {1, 3}

    def test_no_items_drops_every_item_specific_entry(self):
        deep_search = [
            {"job_id": 1, "trigger_item_id": 1, "question": "orphaned, no items at all"},
            {"job_id": 2, "trigger_item_id": None, "question": "still kept"},
        ]
        filtered = daily._filter_deep_search_to_items_included(deep_search, [])
        assert {d["job_id"] for d in filtered} == {2}


class _FakeCursor:
    """Records the executed SQL/params and returns a fixed row set, regardless of the query --
    good enough to characterize the WHERE clause `collect_items` builds without a live DB."""

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

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_item_row(item_id: int) -> dict[str, Any]:
    return {
        "id": item_id,
        "url": f"https://example.com/{item_id}",
        "title": "t",
        "domain": "d",
        "subdomain": None,
        "published_at": None,
        "level": "red",
        "score": 5,
        "summary_he": "",
        "so_what_he": "",
        "report_kind": None,
        "geography": None,
        "trl": None,
        "source_name": "s",
    }


# --------------------------------------------------------------------------
# F20: collect_items excludes tender-linked items and undated/source-less rows
# --------------------------------------------------------------------------


def test_collect_items_sql_excludes_tender_linked_rows(monkeypatch):
    fake_cursor = _FakeCursor([_fake_item_row(1), _fake_item_row(2), _fake_item_row(3)])
    monkeypatch.setattr(daily, "connection", lambda: _FakeConn(fake_cursor))

    rows = daily.collect_items(dt.date(2026, 9, 5), dt.date(2026, 9, 5))

    executed_sql = fake_cursor.queries[0][0]
    assert "NOT EXISTS (SELECT 1 FROM tenders t WHERE t.item_id = i.id)" in executed_sql
    assert len(rows) == 3
    assert [r["n"] for r in rows] == [1, 2, 3]


def test_collect_items_sql_excludes_undated_sourceless_rows(monkeypatch):
    fake_cursor = _FakeCursor([_fake_item_row(1), _fake_item_row(2), _fake_item_row(3)])
    monkeypatch.setattr(daily, "connection", lambda: _FakeConn(fake_cursor))

    daily.collect_items(dt.date(2026, 9, 5), dt.date(2026, 9, 5))

    executed_sql = fake_cursor.queries[0][0]
    assert "NOT (i.published_at IS NULL AND i.source_id IS NULL)" in executed_sql


def test_tenders_forecast_table_shape_and_rationale_cap():
    long_rationale = "א" * 250
    data = {
        "new_forecasts": [
            {
                "platform": "MQ-9 Reaper",
                "payload_need": "EO/IR gimbal upgrade",
                "likelihood": 0.72,
                "window_from": dt.date(2026, 10, 1),
                "window_to": dt.date(2027, 1, 1),
                "rationale_he": long_rationale,
            }
        ]
    }
    tbl = daily._tenders_forecast_table(data)
    assert tbl is not None
    assert tbl["headers"] == ["פלטפורמה", "צורך/Payload", "סבירות", "חלון", "נימוק"]
    row = tbl["rows"][0]
    assert row[0] == "MQ-9 Reaper"
    assert row[2] == "72%"
    assert len(row[4]) <= 200
