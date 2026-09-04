"""Tests for eoa.tenders.report_section (section 5.2 / FR-5.2) -- pure rendering, no DB."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from eoa.tenders.report_section import (
    SECTION_TITLE_HE,
    collect_tenders,
    tenders_extra_section,
    tenders_table,
)


def _tender_row(**overrides):
    base = dict(
        id=1,
        title="EO/IR counter-UAS trackers",
        agency="Ministry of Defence",
        country="UK",
        deadline=dt.date(2026, 10, 15),
        url="https://example.gov/notice/1",
        status="open",
        relevance=5,
    )
    base.update(overrides)
    return base


def _forecast_row(**overrides):
    base = dict(
        id=1,
        platform='כטב"ם MALE',
        buyer_country="US",
        payload_need="מטע\"ד ג'ימבלי EO/IR",
        likelihood=0.55,
        window_from=dt.date(2026, 12, 1),
        window_to=dt.date(2028, 3, 1),
        rationale_he="נימוק לדוגמה [item 10]",
    )
    base.update(overrides)
    return base


class TestCollectTenders:
    def test_collect_without_period(self):
        with patch(
            "eoa.tenders.report_section._fetchall",
            side_effect=[[_tender_row()], [_forecast_row()]],
        ) as mock_fetchall:
            data = collect_tenders()
        assert len(data["open_tenders"]) == 1
        assert len(data["new_forecasts"]) == 1
        assert mock_fetchall.call_count == 2

    def test_collect_with_period_filters_forecasts(self):
        with patch(
            "eoa.tenders.report_section._fetchall",
            side_effect=[[_tender_row()], []],
        ):
            data = collect_tenders(dt.date(2026, 9, 1), dt.date(2026, 9, 4))
        assert data["open_tenders"]
        assert data["new_forecasts"] == []


class TestTendersExtraSection:
    def test_title_and_position(self):
        section = tenders_extra_section({"open_tenders": [_tender_row()], "new_forecasts": []})
        assert section["title_he"] == SECTION_TITLE_HE
        assert section["position"] == "after_outlook"

    def test_body_lists_open_tenders(self):
        section = tenders_extra_section({"open_tenders": [_tender_row()], "new_forecasts": []})
        assert "EO/IR counter-UAS trackers" in section["body_he"]
        assert "2026-10-15" in section["body_he"]

    def test_body_lists_forecasts(self):
        section = tenders_extra_section({"open_tenders": [], "new_forecasts": [_forecast_row()]})
        assert '55%' in section["body_he"] or "55%" in section["body_he"]
        assert 'כטב"ם MALE' in section["body_he"]

    def test_empty_data_still_returns_section(self):
        section = tenders_extra_section({"open_tenders": [], "new_forecasts": []})
        assert "לא זוהו" in section["body_he"]
        assert "לא נוצרו" in section["body_he"]


class TestTendersTable:
    def test_none_when_no_open_tenders(self):
        assert tenders_table({"open_tenders": [], "new_forecasts": []}) is None

    def test_table_shape(self):
        table = tenders_table({"open_tenders": [_tender_row()], "new_forecasts": []})
        assert table is not None
        assert table["headers"] == ["כותרת", "מדינה", "גורם מזמין", "דדליין", "סטטוס", "קישור"]
        assert len(table["rows"]) == 1
        assert table["rows"][0][0] == "EO/IR counter-UAS trackers"

    def test_status_translated_to_hebrew(self):
        table = tenders_table({"open_tenders": [_tender_row(status="closed")], "new_forecasts": []})
        assert table["rows"][0][4] == "סגור"
