"""Round 5 QA checks (docs/QA_CONTINUOUS_LOOP.md "round 5 checks" section, added against
docs/REPORT_TEMPLATE_BENCHMARK.md sec 4's twelve implementation items, which were landing in other
engineers' file scopes the same evening this package was written).

Every check here must be tolerant of the underlying report feature not existing yet in an older
file -- a missing section is a normal ``passed=False`` (asserted below), never an exception. This
file complements ``tests/unit/test_qa_score.py`` (which keeps a "good" daily-report fixture that
now also satisfies every round-5 D6 check) with one pass/fail pair per new check.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_qa_round5.py -q``
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from eoa.qa.d4_investigations import score_D4
from eoa.qa.d6_daily_report import score_D6
from eoa.qa.d7_bd_report import score_D7
from eoa.qa.d8_patent_survey import score_D8
from eoa.qa.d9_tenders_conferences import score_D9

# ---------------------------------------------------------------------------------------------
# fake DB plumbing (same shape as test_qa_score.py's _FakeCursor/_FakeConn)
# ---------------------------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, router: list[tuple[str, list[dict[str, Any]]]]) -> None:
        self._router = router
        self._rows: list[dict[str, Any]] = []

    def execute(self, query: str, params: Any = None) -> None:
        for needle, rows in self._router:
            if needle in query:
                self._rows = rows
                return
        self._rows = []

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class _FakeConn:
    def __init__(self, router: list[tuple[str, list[dict[str, Any]]]]) -> None:
        self._router = router

    def cursor(self, **kwargs: Any) -> _FakeCursor:
        return _FakeCursor(self._router)


def _check(result: Any, name: str) -> Any:
    return next(c for c in result.checks if c.name == name)


# ---------------------------------------------------------------------------------------------
# D6 -- daily/weekly report round-5 checks
# ---------------------------------------------------------------------------------------------

_GOOD_D6_MD = """# דוח יומי

## שורה תחתונה

רפאל חתמה על חוזה משמעותי עם משרד הביטחון האמריקאי [1].

## תקציר מנהלים

חברת Rafael חתמה על חוזה בסך 10 מיליון דולר עם משרד הביטחון האמריקאי בתאריך 5 בספטמבר [1].

## מה השתנה מאז הדוח הקודם

פריט חדש נוסף מאז אתמול [1].

## תעשייה ישראלית

| סוג | חברה | פרטים | מקור |
|---|---|---|---|
| זכייה | Rafael | חוזה חדש | [1] |

## מכרזים

טבלת מכרזים.

## מבט קדימה

- סבירות גבוהה שהעסקה תושלם עד סוף החודש. ביטחון: בינוני, בהתבסס על שני מקורות [1].

## מעקב אינדיקטורים

| אינדיקטור | סטטוס |
|---|---|
| עסקת רפאל | חדש |

## נספח מקורות

| # | כותרת | קישור |
|---|---|---|
| <a id="src-1"></a>1 | כתבה לדוגמה | [https://example.com/a](https://example.com/a) |
"""

# Missing every round-5 feature -- an "older" report, pre-dating this round's parallel work.
_LEGACY_D6_MD = """# דוח יומי

## תקציר מנהלים

חברת Rafael חתמה על חוזה בסך 10 מיליון דולר עם משרד הביטחון האמריקאי בתאריך 5 בספטמבר [1].

## תעשייה ישראלית — זכיות וחוזים

טבלה כלשהי.

## תעשייה ישראלית — תחרות ומתחרים

טבלה כלשהי.

## מבט קדימה

להערכתנו, סבירות גבוהה שהעסקה תיסגר תוך שמירה על ביטחון גבוה בהערכה [1].

## נספח מקורות

| # | כותרת | קישור |
|---|---|---|
| <a id="src-1"></a>1 | כתבה לדוגמה | [https://example.com/a](https://example.com/a) |
"""

_DUPLICATE_ROW_D6_MD = """# דוח יומי

## תקציר מנהלים

תוכן. [1]

## טבלת אירועים עסקיים

| תאריך | חברה | פרטים | מקור |
|---|---|---|---|
| 2026-09-05 | Rafael | חוזה | [1] |

## תחזיות מכרזים

| תאריך | חברה | פרטים | מקור |
|---|---|---|---|
| 2026-09-05 | Rafael | חוזה | [1] |

## נספח מקורות

| # | כותרת | קישור |
|---|---|---|
| <a id="src-1"></a>1 | כתבה לדוגמה | [https://example.com/a](https://example.com/a) |
"""


class TestD6Round5:
    def test_good_report_passes_every_round5_check(self, tmp_path: Path) -> None:
        path = tmp_path / "daily_2026-09-06.md"
        path.write_text(_GOOD_D6_MD, encoding="utf-8")
        result = score_D6(path, run_link_check=False)
        for name in (
            "bluf_present_and_short",
            "what_changed_section_present",
            "indicator_watchlist_table_present",
            "israel_single_table_with_type_column",
            "outlook_likelihood_and_confidence_separated",
            "exec_summary_no_filler_phrases",
            "no_row_repeated_across_tables",
            "heading_count_within_budget",
        ):
            assert _check(result, name).passed is True, name

    def test_legacy_report_fails_bluf_what_changed_indicator_watchlist(self, tmp_path: Path) -> None:
        path = tmp_path / "daily_2026-09-01.md"
        path.write_text(_LEGACY_D6_MD, encoding="utf-8")
        result = score_D6(path, run_link_check=False)
        assert _check(result, "bluf_present_and_short").passed is False
        assert _check(result, "what_changed_section_present").passed is False
        assert _check(result, "indicator_watchlist_table_present").passed is False

    def test_legacy_report_fails_israel_single_table_merge(self, tmp_path: Path) -> None:
        """Two separate 'תעשייה ישראלית' headings (the pre-merge shape) must fail, not just a
        missing 'סוג' column."""
        path = tmp_path / "daily_2026-09-02.md"
        path.write_text(_LEGACY_D6_MD, encoding="utf-8")
        result = score_D6(path, run_link_check=False)
        check = _check(result, "israel_single_table_with_type_column")
        assert check.passed is False
        assert "2 separate israel headings" in check.evidence

    def test_legacy_outlook_mixes_likelihood_and_confidence_in_one_clause(self, tmp_path: Path) -> None:
        """ICD 203: likelihood and confidence must never share a clause -- the legacy fixture's
        single run-on sentence carries both 'סבירות' and 'ביטחון' in the same clause."""
        path = tmp_path / "daily_2026-09-03.md"
        path.write_text(_LEGACY_D6_MD, encoding="utf-8")
        result = score_D6(path, run_link_check=False)
        check = _check(result, "outlook_likelihood_and_confidence_separated")
        assert check.passed is False

    def test_filler_phrase_in_exec_summary_detected(self, tmp_path: Path) -> None:
        text = _GOOD_D6_MD.replace(
            "חברת Rafael חתמה על חוזה בסך 10 מיליון דולר עם משרד הביטחון האמריקאי בתאריך 5 בספטמבר [1].",
            "יש לציין כי חברת Rafael חתמה על חוזה בסך 10 מיליון דולר עם משרד הביטחון האמריקאי [1].",
        )
        path = tmp_path / "daily_2026-09-04.md"
        path.write_text(text, encoding="utf-8")
        result = score_D6(path, run_link_check=False)
        check = _check(result, "exec_summary_no_filler_phrases")
        assert check.passed is False
        assert "יש לציין" in check.evidence

    def test_row_repeated_across_two_tables_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "daily_2026-09-07.md"
        path.write_text(_DUPLICATE_ROW_D6_MD, encoding="utf-8")
        result = score_D6(path, run_link_check=False)
        check = _check(result, "no_row_repeated_across_tables")
        assert check.passed is False

    def test_weekly_heading_budget_exceeded(self, tmp_path: Path) -> None:
        headings = "\n\n".join(f"## סעיף מספר {i}\n\nתוכן." for i in range(20))
        text = f"# דוח שבועי\n\n## תקציר מנהלים\n\nתוכן. [1]\n\n{headings}\n"
        path = tmp_path / "weekly_2026-09-06.md"
        path.write_text(text, encoding="utf-8")
        result = score_D6(path, run_link_check=False)
        check = _check(result, "heading_count_within_budget")
        assert check.passed is False
        assert "budget: 16" in check.evidence

    def test_daily_heading_budget_ok_within_daily_cap(self, tmp_path: Path) -> None:
        path = tmp_path / "daily_2026-09-08.md"
        path.write_text(_GOOD_D6_MD, encoding="utf-8")
        result = score_D6(path, run_link_check=False)
        check = _check(result, "heading_count_within_budget")
        assert "budget: 12" in check.evidence

    def test_monthly_bare_citation_fails_structured_check(self, tmp_path: Path) -> None:
        monthly = tmp_path / "monthly_2026-09-30.md"
        monthly.write_text(
            "# דוח חודשי\n\n## תקציר מנהלים\n\nחברת Rafael זכתה בחוזה [1].\n", encoding="utf-8"
        )
        daily = tmp_path / "daily_2026-09-06.md"
        daily.write_text(_GOOD_D6_MD, encoding="utf-8")
        result = score_D6(daily, run_link_check=False, monthly_path=monthly)
        check = _check(result, "monthly_is_structured")
        assert check.passed is False

    def test_monthly_structured_link_citations_pass(self, tmp_path: Path) -> None:
        monthly = tmp_path / "monthly_2026-09-30.md"
        monthly.write_text(
            "# דוח חודשי\n\n## תקציר מנהלים\n\nחברת Rafael זכתה בחוזה [1](#src-1).\n", encoding="utf-8"
        )
        daily = tmp_path / "daily_2026-09-06.md"
        daily.write_text(_GOOD_D6_MD, encoding="utf-8")
        result = score_D6(daily, run_link_check=False, monthly_path=monthly)
        check = _check(result, "monthly_is_structured")
        assert check.passed is True

    def test_monthly_check_omitted_when_no_monthly_report(self, tmp_path: Path) -> None:
        daily = tmp_path / "daily_2026-09-06.md"
        daily.write_text(_GOOD_D6_MD, encoding="utf-8")
        result = score_D6(daily, run_link_check=False, monthly_path=None)
        assert all(c.name != "monthly_is_structured" for c in result.checks)


# ---------------------------------------------------------------------------------------------
# D7 -- BD territory report round-5 checks
# ---------------------------------------------------------------------------------------------

_GOOD_D7_MD = """# דוח פיתוח עסקי

## שורה תחתונה

יש לפעול מול Rafael בתוך 30 יום להצגת יכולות מתקדמות [1].

## תקציר מנהלים

תוכן. [1]

## מפת קונים / צינור הזדמנויות

| הזדמנות | שלב | גורם רוכש | תאריך יעד | מקור |
|---|---|---|---|---|
| מכרז הגנה אווירית | RFI | משרד ההגנה | 2026-12-01 | [1] |

## הנחות והפרכות

- הנחה: התקציב יאושר כמתוכנן. מה עשוי להפריך זאת: עיכוב תקציבי ממשלתי [1].

## פעולות מומלצות

| עדיפות | פעולה |
|---|---|
| גבוהה | להציג את יכולות Rafael בכנס |

## נספח מקורות

תוכן.
"""

_LEGACY_D7_MD = """# דוח פיתוח עסקי

## תקציר מנהלים

תוכן. [1]

## סיכונים והנחות

טקסט חופשי בלבד ללא רשימת הפרכות.

## פעולות מומלצות

| עדיפות | פעולה |
|---|---|
| גבוהה | להציג את יכולות Rafael בכנס |

## נספח מקורות

תוכן.
"""


class TestD7Round5:
    def test_good_bd_report_passes_bluf_pipeline_assumptions(self, tmp_path: Path) -> None:
        path = tmp_path / "bd_us_2026-09-06.md"
        path.write_text(_GOOD_D7_MD, encoding="utf-8")
        result = score_D7([path], conn=None)
        assert _check(result, "bluf_present_and_short").passed is True
        assert _check(result, "buyer_pipeline_table_present").passed is True
        assert _check(result, "assumptions_falsifiers_list_present").passed is True

    def test_legacy_bd_report_fails_bluf_pipeline_assumptions(self, tmp_path: Path) -> None:
        path = tmp_path / "bd_us_2026-09-01.md"
        path.write_text(_LEGACY_D7_MD, encoding="utf-8")
        result = score_D7([path], conn=None)
        assert _check(result, "bluf_present_and_short").passed is False
        assert _check(result, "buyer_pipeline_table_present").passed is False
        assert _check(result, "assumptions_falsifiers_list_present").passed is False

    def test_acquisition_watch_out_of_territory_row_detected(self, tmp_path: Path) -> None:
        text = _GOOD_D7_MD.replace(
            "## נספח מקורות",
            "## מעקב רכישות ושותפויות\n\n"
            "| תאריך | חברה | סוג אירוע | צד שכנגד | סכום | מקור |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| 2026-09-01 | Elbit | שותפות | Hensoldt | — | [1] |\n\n"
            "## נספח מקורות",
        )
        path = tmp_path / "bd_us_2026-09-06.md"
        path.write_text(text, encoding="utf-8")
        entities = [
            {"name": "Elbit", "country": "IL"},
            {"name": "Hensoldt", "country": "DE"},
        ]
        conn = _FakeConn([("FROM entities", entities)])
        result = score_D7([path], conn=conn)
        check = _check(result, "acquisition_watch_scoped_to_territory")
        assert check.passed is False
        assert "Hensoldt" in check.evidence

    def test_acquisition_watch_global_marker_exempts_row(self, tmp_path: Path) -> None:
        text = _GOOD_D7_MD.replace(
            "## נספח מקורות",
            "## מעקב רכישות ושותפויות\n\n"
            "פעילות גלובלית של חברות מעקב שמקורן ב-us (הקשר בלבד, לא ממוקדת בטריטוריה זו):\n\n"
            "| תאריך | חברה | סוג אירוע | צד שכנגד | סכום | מקור |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| 2026-09-01 | Elbit | שותפות | Hensoldt | — | [1] |\n\n"
            "## נספח מקורות",
        )
        path = tmp_path / "bd_us_2026-09-06.md"
        path.write_text(text, encoding="utf-8")
        entities = [
            {"name": "Elbit", "country": "IL"},
            {"name": "Hensoldt", "country": "DE"},
        ]
        conn = _FakeConn([("FROM entities", entities)])
        result = score_D7([path], conn=conn)
        check = _check(result, "acquisition_watch_scoped_to_territory")
        assert check.passed is True


# ---------------------------------------------------------------------------------------------
# D8 -- patent survey round-5 checks
# ---------------------------------------------------------------------------------------------

_GOOD_D8_MD = """# סקר פטנטים

## שיטה והיקף

שאילתת חיפוש: FPA DROIC. טווח תאריכים: 2020-2026. כיסוי נתוני מקצה: 80%.

## תקציר מנהלים

תוכן. [1]

## השלכות עסקיות והמלצות

- עדיפות: גבוהה. ביטחון: 0.7. יזום פנייה לגורמים מזמינים. [1]

## פרופיל מקצה: Rafael

תוכן.

## אשכול טכנולוגי: FPA עם פיקסל דיגיטלי

תוכן.

## מטריצת אשכול x מקצה

| מקצה | FPA עם פיקסל דיגיטלי |
|---|---|
| Rafael | 3 |

## טבלת פטנטים

| # | כותרת | מקצה |
|---|---|---|
| 1 | פטנט לדוגמה | Rafael |

## נספח מקורות

תוכן.
"""

_LEGACY_D8_MD = """# סקר פטנטים

## תקציר מנהלים

ל-17 מתוך 17 הפטנטים אין נתוני מקצה — לא ניתן להסיק בלעדיות או נתח שוק. [1]

## השלכות עסקיות והמלצות

יזום פנייה לגורמים מזמינים/שותפים בתחום מערכות התצפית התרמיות. נימוק: השוק בשלבי פיתוח. [1]

## פרופיל מקצה: Europe

תוכן.

## אשכולות טכנולוגיה

- לא מסווג

## טבלת פטנטים

| # | כותרת | מקצה |
|---|---|---|
| 1 | פטנט לדוגמה | Europe |

## נספח מקורות

תוכן.
"""


class TestD8Round5:
    def test_good_survey_passes_methodology_coverage_implications_assignee_cluster(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "patent_survey_x_2026-09-06.md"
        path.write_text(_GOOD_D8_MD, encoding="utf-8")
        result = score_D8(path)
        for name in (
            "methodology_box_before_summary",
            "coverage_tag_present",
            "implications_have_priority_confidence",
            "no_bogus_assignee",
            "no_unclassified_cluster_when_patents_exist",
            "cpc_assignee_matrix_present",
        ):
            assert _check(result, name).passed is True, name

    def test_legacy_survey_fails_methodology_and_coverage_tag(self, tmp_path: Path) -> None:
        path = tmp_path / "patent_survey_y_2026-09-01.md"
        path.write_text(_LEGACY_D8_MD, encoding="utf-8")
        result = score_D8(path)
        assert _check(result, "methodology_box_before_summary").passed is False
        assert _check(result, "coverage_tag_present").passed is False

    def test_legacy_survey_fails_implications_priority_confidence(self, tmp_path: Path) -> None:
        path = tmp_path / "patent_survey_z_2026-09-01.md"
        path.write_text(_LEGACY_D8_MD, encoding="utf-8")
        result = score_D8(path)
        assert _check(result, "implications_have_priority_confidence").passed is False

    def test_bogus_assignee_europe_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "patent_survey_w_2026-09-01.md"
        path.write_text(_LEGACY_D8_MD, encoding="utf-8")
        result = score_D8(path)
        check = _check(result, "no_bogus_assignee")
        assert check.passed is False
        assert "Europe" in check.evidence

    def test_unclassified_cluster_with_populated_patents_table_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "patent_survey_v_2026-09-01.md"
        path.write_text(_LEGACY_D8_MD, encoding="utf-8")
        result = score_D8(path)
        check = _check(result, "no_unclassified_cluster_when_patents_exist")
        assert check.passed is False

    def test_unclassified_cluster_check_not_applicable_without_patent_data(self, tmp_path: Path) -> None:
        text = "# סקר\n\n## תקציר מנהלים\n\nתוכן.\n\n## אשכולות טכנולוגיה\n\n- לא מסווג\n"
        path = tmp_path / "patent_survey_u_2026-09-01.md"
        path.write_text(text, encoding="utf-8")
        result = score_D8(path)
        check = _check(result, "no_unclassified_cluster_when_patents_exist")
        assert check.passed is True
        assert "not applicable" in check.evidence

    def test_legacy_survey_with_patent_data_fails_missing_matrix(self, tmp_path: Path) -> None:
        """docs/REPORT_TEMPLATE_BENCHMARK.md sec 3.5 row 7: a populated patents table with no CPC/
        cluster x assignee matrix heading anywhere in the survey must fail, not silently pass."""
        path = tmp_path / "patent_survey_t_2026-09-01.md"
        path.write_text(_LEGACY_D8_MD, encoding="utf-8")
        result = score_D8(path)
        check = _check(result, "cpc_assignee_matrix_present")
        assert check.passed is False

    def test_matrix_check_not_applicable_without_patent_data(self, tmp_path: Path) -> None:
        text = "# סקר\n\n## תקציר מנהלים\n\nתוכן.\n\n## אשכולות טכנולוגיה\n\n- לא מסווג\n"
        path = tmp_path / "patent_survey_s_2026-09-01.md"
        path.write_text(text, encoding="utf-8")
        result = score_D8(path)
        check = _check(result, "cpc_assignee_matrix_present")
        assert check.passed is True
        assert "not applicable" in check.evidence

    def test_matrix_heading_present_but_empty_table_fails(self, tmp_path: Path) -> None:
        text = _GOOD_D8_MD.replace(
            "| Rafael | 3 |\n",
            "",
        )
        path = tmp_path / "patent_survey_r_2026-09-06.md"
        path.write_text(text, encoding="utf-8")
        result = score_D8(path)
        check = _check(result, "cpc_assignee_matrix_present")
        assert check.passed is False


# ---------------------------------------------------------------------------------------------
# D4 -- deep-search investigation round-5 checks (report-rendering based)
# ---------------------------------------------------------------------------------------------

_GOOD_D4_REPORT_MD = """# דוח יומי

## תקציר מנהלים

תוכן. [1]

## חקירות עומק

- **מה קרה עם עסקת רפאל?** — חלקי: נכון להיום טרם התקבלה החלטה.
- **מה קרה עם מפעל פולקסווגן?** — נחסם: התשובה נחסמה בבדיקת אבטחה (חשד להזרקת הוראות).

## נספח מקורות

תוכן.
"""

# The exact live regression evidence quoted in docs/REPORT_TEMPLATE_BENCHMARK.md sec 2.6 (DS3):
# a security-blocked investigation rendered under the generic "לא נמצא" (not_found) label.
_BAD_D4_REPORT_MD = """# דוח יומי

## תקציר מנהלים

תוכן. [1]

## חקירות עומק

- **מה קרה עם עסקת רפאל?** — חלקי: נכון להיום טרם התקבלה החלטה.
- **מה קרה עם מפעל פולקסווגן?** — לא נמצא: התשובה נחסמה בבדיקת אבטחה (חשד להזרקת הוראות בתוכן שנשלף).

## נספח מקורות

תוכן.
"""

_DUPLICATE_QUESTION_D4_REPORT_MD = """# דוח יומי

## תקציר מנהלים

תוכן. [1]

## חקירות עומק

- **מה קרה עם עסקת רפאל?** — חלקי: תשובה ראשונה.
- **מה קרה עם עסקת רפאל?** — נמצא: תשובה שונייה וסותרת.

## נספח מקורות

תוכן.
"""


class TestD4Round5:
    def test_blocked_entry_correctly_labeled_passes(self, tmp_path: Path) -> None:
        path = tmp_path / "daily_2026-09-06.md"
        path.write_text(_GOOD_D4_REPORT_MD, encoding="utf-8")
        conn = _FakeConn([("FROM jobs", []), ("FROM investigation_log", [])])
        result = score_D4([], conn, report_path=path)
        # score_D4 returns "manual only" (score_0_100=None) when there are no jobs at all, but the
        # two report-rendering checks are still appended when the report file/section exist.
        assert _check(result, "blocked_distinct_from_not_found").passed is True
        assert _check(result, "no_contradictory_reruns_in_report").passed is True

    def test_blocked_entry_mislabeled_as_not_found_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "daily_2026-09-05.md"
        path.write_text(_BAD_D4_REPORT_MD, encoding="utf-8")
        conn = _FakeConn([("FROM jobs", []), ("FROM investigation_log", [])])
        result = score_D4([], conn, report_path=path)
        check = _check(result, "blocked_distinct_from_not_found")
        assert check.passed is False

    def test_duplicate_question_rerun_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "daily_2026-09-04.md"
        path.write_text(_DUPLICATE_QUESTION_D4_REPORT_MD, encoding="utf-8")
        conn = _FakeConn([("FROM jobs", []), ("FROM investigation_log", [])])
        result = score_D4([], conn, report_path=path)
        check = _check(result, "no_contradictory_reruns_in_report")
        assert check.passed is False

    def test_checks_omitted_when_no_report_path(self) -> None:
        conn = _FakeConn([("FROM jobs", []), ("FROM investigation_log", [])])
        result = score_D4([], conn, report_path=None)
        assert all(c.name != "blocked_distinct_from_not_found" for c in result.checks)
        assert all(c.name != "no_contradictory_reruns_in_report" for c in result.checks)

    def test_report_checks_still_included_when_jobs_also_in_scope(self, tmp_path: Path) -> None:
        """Regression: a stash mishap once dropped ``report_checks`` from the returned check list
        whenever ``job_ids`` was non-empty (the ``n == 0`` short-circuit above is the only path that
        appended them) -- every round-5 test above happens to pass ``job_ids=[]``, so this is the
        one case that would have silently masked it. Both the job-scoped D4 checks and the
        report-rendering round-5 checks must appear together."""
        path = tmp_path / "daily_2026-09-06.md"
        path.write_text(_GOOD_D4_REPORT_MD, encoding="utf-8")
        job_row = {
            "id": 1,
            "payload": {"question": "מה קרה עם עסקת רפאל?"},
            "result": {
                "outcome": "not_found",
                "confidence": 0.2,
                "sources": [],
                "answer_he": "",
                "what_was_tried_he": "חיפוש מקיף בוצע במקורות פתוחים.",
                "relevance_check": None,
            },
            "item_title": "",
            "entities_mentioned": [],
        }
        conn = _FakeConn([("FROM jobs", [job_row]), ("FROM investigation_log", [])])
        result = score_D4([1], conn, report_path=path)
        for name in (
            "sources_nonempty_for_found",
            "confidence_capped_by_outcome",
            "relevance_check_present_consistent",
            "queries_anchored_to_question",
            "blocked_distinct_from_not_found",
            "no_contradictory_reruns_in_report",
        ):
            assert any(c.name == name for c in result.checks), name
        assert result.n == 1 + 2  # 1 DB job + 2 rendered investigation entries


# ---------------------------------------------------------------------------------------------
# D9 -- source reliability column round-5 check
# ---------------------------------------------------------------------------------------------

_GOOD_D9_REPORT_MD = """# דוח יומי

## תקציר מנהלים

תוכן. [1]

## נספח מקורות

| # | כותרת | אמינות | קישור |
|---|---|---|---|
| <a id="src-1"></a>1 | כתבה לדוגמה | ראשוני | [https://example.com/a](https://example.com/a) |
"""

_LEGACY_D9_REPORT_MD = """# דוח יומי

## תקציר מנהלים

תוכן. [1]

## נספח מקורות

| # | כותרת | קישור |
|---|---|---|
| <a id="src-1"></a>1 | כתבה לדוגמה | [https://example.com/a](https://example.com/a) |
"""


class TestD9Round5:
    _TENDERS: ClassVar = [{"id": 1, "status": "open", "deadline": "2026-12-01", "published_at": "2026-09-01"}]

    def test_appendix_with_reliability_column_passes(self, tmp_path: Path) -> None:
        path = tmp_path / "daily_2026-09-06.md"
        path.write_text(_GOOD_D9_REPORT_MD, encoding="utf-8")
        conn = _FakeConn([("FROM tenders", self._TENDERS), ("FROM conferences", []), ("FROM sources", [])])
        result = score_D9(conn, report_path=path)
        check = _check(result, "source_reliability_column_in_appendix")
        assert check.passed is True

    def test_appendix_without_reliability_column_fails(self, tmp_path: Path) -> None:
        path = tmp_path / "daily_2026-09-01.md"
        path.write_text(_LEGACY_D9_REPORT_MD, encoding="utf-8")
        conn = _FakeConn([("FROM tenders", self._TENDERS), ("FROM conferences", []), ("FROM sources", [])])
        result = score_D9(conn, report_path=path)
        check = _check(result, "source_reliability_column_in_appendix")
        assert check.passed is False

    def test_no_report_file_is_not_applicable(self) -> None:
        conn = _FakeConn([("FROM tenders", self._TENDERS), ("FROM conferences", []), ("FROM sources", [])])
        result = score_D9(conn, report_path=None)
        check = _check(result, "source_reliability_column_in_appendix")
        assert check.passed is True
        assert "not applicable" in check.evidence
