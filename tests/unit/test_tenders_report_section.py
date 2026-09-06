"""Tests for eoa.tenders.report_section (section 5.2 / FR-5.2) -- pure rendering, no DB."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from eoa.tenders.report_section import (
    SECTION_TITLE_HE,
    _trim_rationale,
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
            side_effect=[[_tender_row()], [_forecast_row()], [{"c": 2}]],
        ) as mock_fetchall:
            data = collect_tenders()
        assert len(data["open_tenders"]) == 1
        assert len(data["new_forecasts"]) == 1
        assert data["unknown_count"] == 2
        assert mock_fetchall.call_count == 3

    def test_collect_with_period_filters_forecasts(self):
        with patch(
            "eoa.tenders.report_section._fetchall",
            side_effect=[[_tender_row()], [], [{"c": 0}]],
        ):
            data = collect_tenders(dt.date(2026, 9, 1), dt.date(2026, 9, 4))
        assert data["open_tenders"]
        assert data["new_forecasts"] == []
        assert data["unknown_count"] == 0


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
        assert "55%" in section["body_he"] or "55%" in section["body_he"]
        assert 'כטב"ם MALE' in section["body_he"]

    def test_empty_data_still_returns_section(self):
        section = tenders_extra_section({"open_tenders": [], "new_forecasts": []})
        assert "לא זוהו" in section["body_he"]
        assert "לא נוצרו" in section["body_he"]

    def test_unknown_count_shown_as_a_short_check_line_not_as_open(self):
        """F2.d: undated ('unknown'-status) rows must never appear in the open-tenders list --
        only as a short count line."""
        section = tenders_extra_section({"open_tenders": [], "new_forecasts": [], "unknown_count": 3})
        assert "3" in section["body_he"]
        assert "לבדיקה" in section["body_he"]
        assert "ללא תאריכים" in section["body_he"]

    def test_zero_unknown_count_produces_no_extra_line(self):
        section = tenders_extra_section({"open_tenders": [], "new_forecasts": [], "unknown_count": 0})
        assert "לבדיקה" not in section["body_he"]

    def test_forecast_rationale_trimmed_to_one_sentence(self):
        """F2.d: the forecast line must carry only a single trimmed sentence, not the full
        multi-sentence LLM rationale."""
        long_rationale = "משפט ראשון קצר. משפט שני שלא אמור להופיע בדוח כלל, גם אם ארוך למדי."
        section = tenders_extra_section(
            {"open_tenders": [], "new_forecasts": [_forecast_row(rationale_he=long_rationale)]}
        )
        assert "משפט ראשון קצר" in section["body_he"]
        assert "משפט שני שלא אמור להופיע" not in section["body_he"]

    def test_one_line_per_forecast(self):
        """F2.d: each forecast is exactly one line (no embedded newlines from a multi-line
        rationale leaking into the section)."""
        section = tenders_extra_section(
            {"open_tenders": [], "new_forecasts": [_forecast_row(rationale_he="שורה אחת.\nשורה שנייה.")]}
        )
        forecast_lines = [ln for ln in section["body_he"].split("\n") if ln.startswith("- ")]
        assert len(forecast_lines) == 1


class TestTrimRationale:
    def test_returns_first_sentence_only(self):
        assert _trim_rationale("משפט אחד. משפט שני.") == "משפט אחד."

    def test_truncates_overly_long_single_sentence(self):
        text = "א" * 250
        trimmed = _trim_rationale(text, max_chars=200)
        assert len(trimmed) == 200
        assert trimmed.endswith("…")

    def test_empty_input_returns_empty_string(self):
        assert _trim_rationale("") == ""
        assert _trim_rationale(None) == ""


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
