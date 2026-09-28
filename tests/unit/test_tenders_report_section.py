"""Tests for eoa.tenders.report_section (section 5.2 / FR-5.2) -- pure rendering, no DB."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from eoa.tenders.report_section import (
    SECTION_TITLE_HE,
    _trim_rationale,
    attach_forecast_citations,
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

    def test_collect_softens_unsupported_intensifier_in_forecast_rationale(self):
        """PD-fix-3 item 3: the live monthly/weekly-report finding -- a forecast rationale carrying
        an unsupported ``eoa.report.claims_gate`` trigger word ("משמעותית", no digit anywhere in
        the sentence to back it up) must come back softened, matching every other free-text report
        cell (``eoa.report.israel_section``/``eoa.report.tech_watch``)."""
        raw = "ייתכן שהמימוש המסחרי של הפלטפורמה יאפשר להוזיל משמעותית עלויות אימון."
        with patch(
            "eoa.tenders.report_section._fetchall",
            side_effect=[[_tender_row()], [_forecast_row(rationale_he=raw)], [{"c": 0}]],
        ):
            data = collect_tenders()
        softened = data["new_forecasts"][0]["rationale_he"]
        assert "משמעותית" not in softened
        assert "ייתכן שהמימוש המסחרי" in softened  # softened, not dropped -- content remains

    def test_collect_leaves_unflagged_rationale_untouched(self):
        with patch(
            "eoa.tenders.report_section._fetchall",
            side_effect=[[_tender_row()], [_forecast_row()], [{"c": 0}]],
        ):
            data = collect_tenders()
        assert data["new_forecasts"][0]["rationale_he"] == "נימוק לדוגמה [item 10]"


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

    def test_same_title_buyer_deadline_collapses_to_one_row_with_count(self):
        """R-tender-dedupe item 4 (daily_2026-09-28.md): reproduces the reported bug -- the same
        "Night Vision Devices for Foreign Military Sales (FMS)" notice posted under 5 distinct
        SAM.gov ids (same buyer, same deadline) must render as ONE row noting the count, linking
        the first-encountered notice's own URL."""
        dup_title = "Night Vision Devices for Foreign Military Sales (FMS)"
        rows = [
            _tender_row(
                id=i,
                title=dup_title,
                agency="DEPT OF DEFENSE.DEPT OF THE ARMY.AMC.ACC.ACC-CTRS.ACC-APG.W6QK ACC-APG",
                deadline=dt.date(2026, 9, 29),
                url=f"https://sam.gov/workspace/contract/opp/{i}/view",
            )
            for i in range(5)
        ]
        table = tenders_table({"open_tenders": rows, "new_forecasts": []})
        assert len(table["rows"]) == 1
        assert table["rows"][0][0] == f"{dup_title} (×5)"
        assert table["rows"][0][5] == "https://sam.gov/workspace/contract/opp/0/view"  # first row's link

    def test_distinct_deadlines_are_not_collapsed(self):
        dup_title = "Night Vision Devices for Foreign Military Sales (FMS)"
        rows = [
            _tender_row(id=1, title=dup_title, agency="A", deadline=dt.date(2026, 9, 29)),
            _tender_row(id=2, title=dup_title, agency="A", deadline=dt.date(2026, 10, 15)),
        ]
        table = tenders_table({"open_tenders": rows, "new_forecasts": []})
        assert len(table["rows"]) == 2
        assert "(×" not in table["rows"][0][0]

    def test_single_occurrence_title_gets_no_count_suffix(self):
        table = tenders_table({"open_tenders": [_tender_row()], "new_forecasts": []})
        assert "(×" not in table["rows"][0][0]


class TestAttachForecastCitations:
    def _rows(self, ids: list[int]) -> list[dict]:
        return [
            dict(
                id=i,
                url=f"https://example.gov/{i}",
                title=f"item {i}",
                published_at=dt.datetime(2026, 9, i, tzinfo=dt.UTC),
                source_name="S",
            )
            for i in ids
        ]

    def test_caps_citations_to_three_most_recent_per_forecast(self):
        """R-appendix-cap item 3 (daily_2026-09-28.md): reproduces the reported bug -- a forecast
        row that names 5 trigger items must only ever register/cite its 3 most recently-published
        ones, so the appendix does not grow by more than what the table actually cites."""
        rows = self._rows([1, 2, 3, 4, 5])  # published Sep 1..5 -- 3/4/5 are the most recent
        with patch("eoa.tenders.report_section._fetchall", return_value=rows):
            citation_items: list[dict] = []
            forecasts = [{"sources": [f"item:{i}" for i in [1, 2, 3, 4, 5]]}]
            attach_forecast_citations(citation_items, forecasts)
        assert len(citation_items) == 3
        assert len(forecasts[0]["_citation_ns"]) == 3
        assert {it["id"] for it in citation_items} == {3, 4, 5}

    def test_excluded_source_never_consumes_a_registry_slot(self):
        rows = self._rows([1, 2])
        with patch("eoa.tenders.report_section._fetchall", return_value=rows):
            citation_items: list[dict] = []
            forecasts = [{"sources": ["item:1", "item:2"]}]  # only 2 sources, under the cap of 3
            attach_forecast_citations(citation_items, forecasts)
        assert len(citation_items) == 2  # both kept -- under the cap, nothing excluded

    def test_shared_source_across_forecasts_registered_once(self):
        rows = self._rows([1])
        with patch("eoa.tenders.report_section._fetchall", return_value=rows):
            citation_items: list[dict] = []
            forecasts = [{"sources": ["item:1"]}, {"sources": ["item:1"]}]
            attach_forecast_citations(citation_items, forecasts)
        assert len(citation_items) == 1
        assert forecasts[0]["_citation_ns"] == forecasts[1]["_citation_ns"]

    def test_missing_published_at_sorts_as_least_recent(self):
        rows = self._rows([2, 3, 4])
        rows.append(
            dict(id=1, url="https://example.gov/1", title="item 1", published_at=None, source_name="S")
        )
        with patch("eoa.tenders.report_section._fetchall", return_value=rows):
            citation_items: list[dict] = []
            forecasts = [{"sources": ["item:1", "item:2", "item:3", "item:4"]}]
            attach_forecast_citations(citation_items, forecasts)
        kept_ids = {it["id"] for it in citation_items}
        assert kept_ids == {2, 3, 4}  # id 1 (no published_at) is the one excluded by the cap
