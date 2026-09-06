"""W14 (docs/REVIEW_2026-09-06_evening.md, user finding 2026-09-06 19:10): unit tests for the
reports-list additive fields on ``eoa.api.services``:

- ``title_he``/``subject_he``/``built_at`` -- a descriptive Hebrew title per report kind.
- ``preview_he``/``source_count``/``qa_issues`` -- a short content preview + counters read from
  the already-rendered ``path_md`` file.
- ``group_key``/``is_latest`` -- version grouping so the UI shows only the newest run per
  kind+subject by default.

Pure logic + a fake-DB-cursor convention matching ``tests/unit/test_report_citations.py`` (no live
DB/Ollama/network); file-based preview extraction uses ``tmp_path`` real files.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_reports_list_round4.py -q``
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from eoa.api import services

JERUSALEM_OFFSET = dt.timezone(dt.timedelta(hours=3))


def _dt(y, m, d, hh, mm, ss=0) -> dt.datetime:
    return dt.datetime(y, m, d, hh, mm, ss, tzinfo=JERUSALEM_OFFSET)


# --------------------------------------------------------------------------
# _report_subject_he / _territory_label_he
# --------------------------------------------------------------------------


class TestReportSubjectHe:
    def test_patent_survey_subject_is_topic_from_qa_report(self):
        subject = services._report_subject_he(
            "patent_survey", None, {"topic": "FPA עם פיקסל דיגיטלי (DROIC)"}
        )
        assert subject == "FPA עם פיקסל דיגיטלי (DROIC)"

    def test_patent_survey_missing_topic_is_none(self):
        assert services._report_subject_he("patent_survey", None, {}) is None

    def test_bd_territory_subject_is_hebrew_country_name(self):
        assert services._report_subject_he("bd_territory", "US", {}) == 'ארה"ב'
        assert services._report_subject_he("bd_territory", "IL", {}) == "ישראל"

    def test_bd_territory_unknown_code_falls_back_to_raw_code(self):
        # never invented -- an unrecognized territory shows the code itself, not a guess.
        assert services._report_subject_he("bd_territory", "ZZ", {}) == "ZZ"

    def test_daily_and_weekly_have_no_subject(self):
        assert services._report_subject_he("daily", None, {}) is None
        assert services._report_subject_he("weekly", None, {}) is None


# --------------------------------------------------------------------------
# _report_title_he -- verified against the exact live-DB rows/examples in the W14 task
# --------------------------------------------------------------------------


class TestReportTitleHe:
    def test_patent_survey_title_matches_live_example(self):
        # eoa reports.id=49, live DB: topic "FPA עם פיקסל דיגיטלי (DROIC)", created_at 19:03:13
        title = services._report_title_he(
            "patent_survey",
            "FPA עם פיקסל דיגיטלי (DROIC)",
            None,
            None,
            dt.date(2026, 9, 6),
            _dt(2026, 9, 6, 19, 3, 13),
        )
        assert title == "סקר פטנטים: FPA עם פיקסל דיגיטלי (DROIC) — 06.09 19:03"

    def test_bd_territory_title_matches_live_example(self):
        # reports.id=42, live DB: territory US, created_at 17:28:16
        title = services._report_title_he(
            "bd_territory",
            'ארה"ב',
            "US",
            dt.date(2026, 6, 9),
            dt.date(2026, 9, 6),
            _dt(2026, 9, 6, 17, 28, 16),
        )
        assert title == 'דוח פיתוח עסקי — ארה"ב — 06.09 17:28'

    def test_weekly_title_matches_live_example(self):
        # reports.id=51, live DB: period 2026-08-31..2026-09-06 (ISO week 36)
        title = services._report_title_he(
            "weekly", None, None, dt.date(2026, 8, 31), dt.date(2026, 9, 6), _dt(2026, 9, 6, 19, 43, 28)
        )
        assert title == "דוח שבועי — שבוע 36 (31.08–06.09)"

    def test_daily_title_matches_live_example(self):
        # reports.id=40, live DB: period_end 2026-09-06
        title = services._report_title_he(
            "daily", None, None, dt.date(2026, 9, 5), dt.date(2026, 9, 6), _dt(2026, 9, 6, 17, 12, 17)
        )
        assert title == "דוח יומי — 06.09"

    def test_monthly_title_uses_month_year(self):
        title = services._report_title_he(
            "monthly", None, None, dt.date(2026, 9, 1), dt.date(2026, 9, 30), _dt(2026, 9, 30, 8, 0, 0)
        )
        assert title == "דוח חודשי — 09.2026"

    def test_bd_territory_falls_back_to_dash_when_subject_missing(self):
        title = services._report_title_he("bd_territory", None, None, None, None, _dt(2026, 9, 6, 12, 0, 0))
        assert title.startswith("דוח פיתוח עסקי — — ")

    def test_unknown_kind_still_produces_a_descriptive_title(self):
        title = services._report_title_he("adhoc", None, None, None, None, _dt(2026, 9, 6, 8, 0, 0))
        assert title == "דוח אד-הוק — 06.09 08:00"


# --------------------------------------------------------------------------
# _report_group_key
# --------------------------------------------------------------------------


class TestReportGroupKey:
    def test_patent_survey_groups_by_topic_case_and_whitespace_insensitive(self):
        row_a = {"kind": "patent_survey"}
        row_b = {"kind": "patent_survey"}
        key_a = services._report_group_key(row_a, "  FPA עם פיקסל דיגיטלי (DROIC)  ")
        key_b = services._report_group_key(row_b, "fpa עם פיקסל דיגיטלי (droic)")
        assert key_a == key_b

    def test_bd_territory_groups_by_territory_code(self):
        assert services._report_group_key({"kind": "bd_territory", "territory": "us"}, "x") == (
            services._report_group_key({"kind": "bd_territory", "territory": "US"}, "y")
        )

    def test_daily_groups_by_kind_and_period(self):
        row1 = {"kind": "daily", "period_start": dt.date(2026, 9, 5), "period_end": dt.date(2026, 9, 6)}
        row2 = {"kind": "daily", "period_start": dt.date(2026, 9, 5), "period_end": dt.date(2026, 9, 6)}
        row3 = {"kind": "daily", "period_start": dt.date(2026, 9, 6), "period_end": dt.date(2026, 9, 6)}
        assert services._report_group_key(row1, None) == services._report_group_key(row2, None)
        assert services._report_group_key(row1, None) != services._report_group_key(row3, None)

    def test_different_patent_topics_are_different_groups(self):
        key_a = services._report_group_key({"kind": "patent_survey"}, "Topic A")
        key_b = services._report_group_key({"kind": "patent_survey"}, "Topic B")
        assert key_a != key_b


# --------------------------------------------------------------------------
# _report_preview_from_text -- strips [n]/blockquotes/markdown, keeps first two sentences
# --------------------------------------------------------------------------


class TestReportPreviewFromText:
    def test_extracts_first_two_sentences_and_strips_citation_markers(self):
        md = (
            "# דוח יומי\n\n**תאריך:** יום ראשון\n\n"
            "## תקציר מנהלים\n\n"
            "אין תקציר לתקופה זו. [1](#src-1)\n\n"
            "לא זוהו פריטים חדשים. [2](#src-2)[3](#src-3)\n\n"
            "פירוט מלא בטבלאות בהמשך הדוח.\n\n"
            "## טבלת אירועים עסקיים\n\nמשהו אחר לגמרי שלא אמור להופיע.\n"
        )
        preview = services._report_preview_from_text(md)
        assert preview == "אין תקציר לתקופה זו. לא זוהו פריטים חדשים."
        assert "[" not in preview
        assert "טבלת אירועים" not in preview

    def test_drops_blockquote_asides(self):
        md = (
            "## תקציר מנהלים\n\n"
            "משפט תקציר אמיתי. [1]\n"
            "  > **התקדמות פטנט [1]:** זהו תוכן משני שאסור שיופיע בתצוגה המקדימה.\n\n"
            "## נוף הפטנטים\n"
        )
        preview = services._report_preview_from_text(md)
        assert preview == "משפט תקציר אמיתי."
        assert "התקדמות פטנט" not in preview

    def test_no_heading_returns_none(self):
        assert services._report_preview_from_text("# כותרת\n\nללא תקציר מנהלים כאן.\n") is None

    def test_strips_bold_and_plain_markdown_links(self):
        md = "## תקציר מנהלים\n\n**חברה חשובה** חתמה הסכם. ראו [כאן](https://example.com) לפרטים.\n"
        preview = services._report_preview_from_text(md)
        assert preview == "חברה חשובה חתמה הסכם. ראו כאן לפרטים."


# --------------------------------------------------------------------------
# _report_file_stats -- reads a real (tmp_path) file, caches per (id, created_at)
# --------------------------------------------------------------------------


class TestReportFileStats:
    def _write_report_md(self, tmp_path: Path) -> Path:
        appendix_rows = "".join(
            f'| <a id="src-{n}"></a>{n} | t{n} | s{n} | 1.1.2026 | u{n} |\n' for n in range(1, 4)
        )
        content = (
            "# דוח יומי\n\n## תקציר מנהלים\n\nמשפט ראשון. משפט שני. משפט שלישי שלא אמור להופיע.\n\n"
            "## נספח מקורות\n\n" + appendix_rows
        )
        p = tmp_path / "daily_2026-09-06.md"
        p.write_text(content, encoding="utf-8")
        return p

    def test_reads_preview_and_counts_appendix_rows(self, tmp_path: Path):
        services._REPORT_PREVIEW_CACHE.clear()
        p = self._write_report_md(tmp_path)
        stats = services._report_file_stats(9001, _dt(2026, 9, 6, 8, 0, 0), str(p))
        assert stats["preview_he"] == "משפט ראשון. משפט שני."
        assert stats["source_count"] == 3

    def test_result_is_cached_per_id_and_created_at(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        services._REPORT_PREVIEW_CACHE.clear()
        p = self._write_report_md(tmp_path)
        created_at = _dt(2026, 9, 6, 8, 0, 0)
        first = services._report_file_stats(9002, created_at, str(p))

        def _boom(*_a, **_kw):
            raise AssertionError("should not re-read the file for the same (id, created_at)")

        monkeypatch.setattr(services, "_resolve_repo_path", _boom)
        second = services._report_file_stats(9002, created_at, str(p))
        assert second == first

    def test_missing_file_returns_none_preview_and_zero_sources(self, tmp_path: Path):
        services._REPORT_PREVIEW_CACHE.clear()
        missing = tmp_path / "does_not_exist.md"
        stats = services._report_file_stats(9003, _dt(2026, 9, 6, 8, 0, 0), str(missing))
        assert stats == {"preview_he": None, "source_count": 0}

    def test_no_path_md_returns_none_preview_and_zero_sources(self):
        services._REPORT_PREVIEW_CACHE.clear()
        stats = services._report_file_stats(9004, _dt(2026, 9, 6, 8, 0, 0), None)
        assert stats == {"preview_he": None, "source_count": 0}


# --------------------------------------------------------------------------
# _report_card -- end-to-end additive-field shape
# --------------------------------------------------------------------------


class TestReportCard:
    def _row(self, tmp_path: Path, **overrides) -> dict[str, Any]:
        p = tmp_path / "patent_survey_x.md"
        p.write_text(
            "## תקציר מנהלים\n\nתקציר אמיתי כאן. משפט שני.\n\n## נספח מקורות\n\n"
            '| <a id="src-1"></a>1 | a | b | c | d |\n',
            encoding="utf-8",
        )
        base = dict(
            id=1,
            kind="patent_survey",
            period_start=None,
            period_end=dt.date(2026, 9, 6),
            path_docx=None,
            path_md=str(p),
            path_html=None,
            qa_passed=True,
            created_at=_dt(2026, 9, 6, 19, 3, 13),
            items_included=[1, 2, 3],
            territory=None,
            qa_report={"topic": "FPA עם פיקסל דיגיטלי (DROIC)"},
        )
        base.update(overrides)
        return base

    def test_card_carries_every_old_field_plus_the_new_additive_ones(self, tmp_path: Path):
        services._REPORT_PREVIEW_CACHE.clear()
        card = services._report_card(self._row(tmp_path))
        # old fields untouched
        assert card["id"] == 1
        assert card["kind"] == "patent_survey"
        assert card["headline_count"] == 3
        assert card["territory"] is None
        # new fields
        assert card["title_he"] == "סקר פטנטים: FPA עם פיקסל דיגיטלי (DROIC) — 06.09 19:03"
        assert card["subject_he"] == "FPA עם פיקסל דיגיטלי (DROIC)"
        assert card["built_at"] == _dt(2026, 9, 6, 19, 3, 13).isoformat()
        assert card["preview_he"] == "תקציר אמיתי כאן. משפט שני."
        assert card["source_count"] == 1
        assert card["qa_issues"] == 0
        assert card["group_key"] == "patent_survey:fpa עם פיקסל דיגיטלי (droic)"

    def test_qa_issues_counts_errors_list(self, tmp_path: Path):
        services._REPORT_PREVIEW_CACHE.clear()
        row = self._row(tmp_path, kind="daily", qa_report={"errors": ["e1", "e2"]}, qa_passed=False)
        card = services._report_card(row)
        assert card["qa_issues"] == 2


# --------------------------------------------------------------------------
# list_reports -- is_latest marks only the newest row per group_key
# --------------------------------------------------------------------------


class TestListReportsIsLatest:
    def _rows(self) -> list[dict[str, Any]]:
        # created_at DESC, matching how the real SQL orders them.
        return [
            {
                "id": 50,
                "kind": "patent_survey",
                "period_start": None,
                "period_end": dt.date(2026, 9, 6),
                "path_docx": None,
                "path_md": None,
                "path_html": None,
                "qa_passed": True,
                "created_at": _dt(2026, 9, 6, 19, 4, 52),
                "items_included": [1],
                "territory": None,
                "qa_report": {"topic": "Anduril Lattice counter-UAS EO/IR optical tracking patents"},
            },
            {
                "id": 45,
                "kind": "patent_survey",
                "period_start": None,
                "period_end": dt.date(2026, 9, 6),
                "path_docx": None,
                "path_md": None,
                "path_html": None,
                "qa_passed": True,
                "created_at": _dt(2026, 9, 6, 17, 33, 15),
                "items_included": [],
                "territory": None,
                "qa_report": {"topic": "Anduril Lattice counter-UAS EO/IR optical tracking patents"},
            },
            {
                "id": 42,
                "kind": "bd_territory",
                "period_start": dt.date(2026, 6, 9),
                "period_end": dt.date(2026, 9, 6),
                "path_docx": None,
                "path_md": None,
                "path_html": None,
                "qa_passed": True,
                "created_at": _dt(2026, 9, 6, 17, 28, 16),
                "items_included": list(range(26)),
                "territory": "US",
                "qa_report": {"passed": True, "errors": []},
            },
        ]

    def test_only_newest_per_group_is_latest(self, monkeypatch: pytest.MonkeyPatch):
        services._REPORT_PREVIEW_CACHE.clear()
        monkeypatch.setattr(services, "_fetchall", lambda *_a, **_kw: self._rows())
        cards = services.list_reports()
        by_id = {c["id"]: c for c in cards}
        assert by_id[50]["is_latest"] is True
        assert by_id[45]["is_latest"] is False  # older run of the same patent-survey topic
        assert by_id[42]["is_latest"] is True  # only bd_territory/US row present -> latest


# --------------------------------------------------------------------------
# get_report -- is_latest via a dedicated "any newer row in this group?" query
# --------------------------------------------------------------------------


class TestGetReportIsLatest:
    def test_is_latest_true_when_no_newer_row_in_group(self, monkeypatch: pytest.MonkeyPatch):
        services._REPORT_PREVIEW_CACHE.clear()
        row = {
            "id": 49,
            "kind": "patent_survey",
            "period_start": None,
            "period_end": dt.date(2026, 9, 6),
            "path_docx": None,
            "path_md": None,
            "path_html": None,
            "qa_passed": True,
            "created_at": _dt(2026, 9, 6, 19, 3, 13),
            "items_included": [1, 2],
            "territory": None,
            "qa_report": {"topic": "FPA עם פיקסל דיגיטלי (DROIC)"},
        }

        def fetchone(query: str, params: Any = None) -> Any:
            if "FROM reports WHERE id" in query:
                return row
            if "AND created_at >" in query:
                return None  # no newer row in the same topic group
            raise AssertionError(query)

        monkeypatch.setattr(services, "_fetchone", fetchone)
        monkeypatch.setattr(services, "_fetchall", lambda *_a, **_kw: [])
        card = services.get_report(49)
        assert card is not None
        assert card["is_latest"] is True

    def test_is_latest_false_when_a_newer_row_exists(self, monkeypatch: pytest.MonkeyPatch):
        services._REPORT_PREVIEW_CACHE.clear()
        row = {
            "id": 27,
            "kind": "patent_survey",
            "period_start": None,
            "period_end": dt.date(2026, 9, 6),
            "path_docx": None,
            "path_md": None,
            "path_html": None,
            "qa_passed": True,
            "created_at": _dt(2026, 9, 6, 7, 23, 45),
            "items_included": [1],
            "territory": None,
            "qa_report": {"topic": "FPA עם פיקסל דיגיטלי (DROIC)"},
        }

        def fetchone(query: str, params: Any = None) -> Any:
            if "FROM reports WHERE id" in query:
                return row
            if "AND created_at >" in query:
                return {"x": 1}  # a newer row of the same topic exists (e.g. id=49)
            raise AssertionError(query)

        monkeypatch.setattr(services, "_fetchone", fetchone)
        monkeypatch.setattr(services, "_fetchall", lambda *_a, **_kw: [])
        card = services.get_report(27)
        assert card is not None
        assert card["is_latest"] is False
