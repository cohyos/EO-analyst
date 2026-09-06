"""Round 3 D6 fixes (docs/QA_CONTINUOUS_LOOP.md D6, docs/qa/loop/round_2_judge.md-equivalent D6
findings 1-5 for this round): deterministic fallback synthesis for daily/weekly reports, tighter
Israel-industry "תחרות ומתחרים" eligibility, the appendix/table duplicate-title false positive in
the D6 scorer, Hebrew-quote normalisation, and a weekly-report-after-quarantine regression test.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_report_round3_d6.py -q``
"""

from __future__ import annotations

import datetime as dt

import pytest

from eoa.llm.schemas.analysis import (
    AnalystNote,
    DailyReportDraft,
    OutlookIndicator,
    ReportSection,
    Sentence,
    StructuredSection,
)
from eoa.llm.schemas.reports import (
    MonthlyReportDraftLegacy,
    TrendParagraph,
    WeeklyReportDraft,
    WeeklyTrendSection,
)
from eoa.qa import d6_daily_report as d6
from eoa.report import daily, weekly
from eoa.report import israel_section as isec
from eoa.report.qa_citations import check
from eoa.report.textnorm import normalize_draft, normalize_hebrew_punctuation

_GERSHAYIM = "״"
_GERESH = "׳"

# --------------------------------------------------------------------------
# Finding 1: deterministic fallback synthesis (daily + weekly) -- pure-function unit tests for the
# building blocks, then a full-draft test proving the result passes eoa.report.qa_citations.check.
# --------------------------------------------------------------------------


class TestDailyFallbackSentences:
    def test_top_item_sentences_cite_the_items_own_n(self):
        items = [
            {"id": 1, "n": 1, "title": "Item A", "level": "red", "so_what_he": "משמעות ראשונה."},
            {"id": 2, "n": 2, "title": "Item B", "level": "orange", "summary_he": "תקציר שני."},
        ]
        sentences = daily._fallback_top_item_sentences(items, limit=5)
        assert [s.cites for s in sentences] == [[1], [2]]
        assert "Item A" in sentences[0].text_he
        assert "Item B" in sentences[1].text_he

    def test_top_item_sentences_skip_items_without_n(self):
        items = [{"id": 1, "n": None, "title": "No registry number"}]
        assert daily._fallback_top_item_sentences(items, limit=5) == []

    def test_top_item_sentences_respect_limit(self):
        items = [{"id": i, "n": i, "title": f"Item {i}", "level": "red"} for i in range(1, 10)]
        assert len(daily._fallback_top_item_sentences(items, limit=3)) == 3

    def test_event_sentences_order_by_date_desc_and_cite_event_n(self):
        events = [
            {
                "id": 1,
                "n": 1,
                "kind": "contract_award",
                "parties": ["Elbit"],
                "customer": "USAF",
                "date": dt.date(2026, 9, 1),
                "amount_usd": 1_000_000,
            },
            {
                "id": 2,
                "n": 2,
                "kind": "partnership",
                "parties": ["Rafael"],
                "program": "Iron Beam",
                "date": dt.date(2026, 9, 5),
                "amount_usd": None,
            },
            {"id": 3, "n": None, "kind": "test", "parties": ["Skipped -- no registry number"]},
        ]
        sentences = daily._fallback_event_sentences(events, limit=5)
        assert len(sentences) == 2
        assert sentences[0].cites == [2]  # 2026-09-05 sorts before 2026-09-01
        assert sentences[1].cites == [1]
        assert "שותפות" in sentences[0].text_he
        assert "זכייה בחוזה" in sentences[1].text_he
        assert "Rafael" in sentences[0].text_he

    def test_israel_item_sentences_cite_the_items_own_n(self):
        items = [{"id": 9, "n": 3, "title": "Israel item", "so_what_he": "רלוונטי לישראל."}]
        sentences = daily._fallback_israel_item_sentences(items, limit=5)
        assert sentences[0].cites == [3]
        assert "Israel item" in sentences[0].text_he

    def test_deterministic_fallback_draft_is_never_empty_and_passes_citation_check(self):
        items = [{"id": 1, "n": 1, "title": "Top item", "level": "red", "so_what_he": "חשוב."}]
        events_with_n = [
            {
                "id": 1,
                "n": 1,
                "kind": "contract_award",
                "parties": ["Elbit"],
                "customer": "USAF",
                "date": dt.date(2026, 9, 1),
                "amount_usd": 5_000_000,
            }
        ]
        israel_items = [{"id": 2, "n": 2, "title": "Israel item", "so_what_he": "רלוונטי."}]
        draft = daily._deterministic_fallback_draft(items, events_with_n, israel_items)

        assert draft.exec_summary, "the whole point of finding 1: never an empty exec summary"
        assert draft.sections == []
        assert "תקציר מובנה אוטומטית" in draft.system_note_he
        assert "ללא ניסוח מודל" in draft.system_note_he

        registry = [{"n": 1}, {"n": 2}]
        qa = check(draft, registry)
        assert qa.passed, qa.errors

    def test_extend_registry_with_rows_assigns_new_n_and_mutates_in_place(self):
        citation_items = [{"id": 1, "n": 1}]
        rows = [{"id": 5, "title": "t5", "source_name": "s", "url": "u", "published_at": None}]
        daily._extend_registry_with_rows(citation_items, rows)
        assert rows[0]["n"] == 2
        assert citation_items[-1]["id"] == 5

    def test_extend_registry_with_rows_reuses_existing_n_for_already_present_id(self):
        citation_items = [{"id": 1, "n": 1}, {"id": 5, "n": 2}]
        rows = [{"id": 5, "title": "already present"}]
        daily._extend_registry_with_rows(citation_items, rows)
        assert rows[0]["n"] == 2
        assert len(citation_items) == 2


class TestWeeklyFallbackSentences:
    def test_top_item_sentences_cite_the_items_own_n(self):
        items = [{"id": 1, "n": 1, "title": "Weekly item", "level": "orange", "so_what_he": "מגמה."}]
        sentences = weekly._fallback_top_item_sentences(items, limit=5)
        assert sentences[0].cites == [1]
        assert "Weekly item" in sentences[0].text_he

    def test_event_sentences_skip_events_without_n(self):
        events = [{"id": 1, "n": None, "kind": "launch", "parties": ["X"]}]
        assert weekly._fallback_event_sentences(events, limit=5) == []

    def test_extend_registry_with_rows_assigns_new_n_and_mutates_in_place(self):
        citation_items = [{"id": 1, "n": 1}]
        rows = [{"id": 7, "title": "t7"}]
        weekly._extend_registry_with_rows(citation_items, rows)
        assert rows[0]["n"] == 2
        assert citation_items[-1]["id"] == 7

    def test_deterministic_fallback_draft_is_never_empty_and_passes_citation_check(self):
        items = [{"id": 1, "n": 1, "title": "Top weekly item", "level": "red", "so_what_he": "חשוב."}]
        events_with_n = [
            {
                "id": 1,
                "n": 1,
                "kind": "m_and_a",
                "parties": ["Rafael", "Elbit"],
                "date": dt.date(2026, 9, 3),
                "amount_usd": 2_000_000,
            }
        ]
        israel_items = [{"id": 2, "n": 2, "title": "Israel weekly item", "so_what_he": "רלוונטי."}]
        draft = weekly._deterministic_fallback_draft(items, events_with_n, israel_items)

        assert draft.exec_summary
        assert draft.trends == []
        assert draft.sections == []
        assert "תקציר מובנה אוטומטית" in draft.system_note_he

        registry = [{"n": 1}, {"n": 2}]
        qa = check(draft, registry)
        assert qa.passed, qa.errors


# --------------------------------------------------------------------------
# Finding 2: tighter "תעשייה ישראלית" eligibility -- an item whose only Israeli hook is a
# government/military org (no Israeli company entity, no business event kind) is excluded from
# every category bucket rather than defaulting into "תחרות ומתחרים".
# --------------------------------------------------------------------------


class TestIsraelSectionEligibility:
    def test_agency_only_hook_with_no_event_is_excluded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: set())
        item = {
            "id": 1,
            "israel_reasons": [],
            "summary_he": "",
            "so_what_he": "",
            "title": "",
            "entities_mentioned": ["IDF"],
        }
        assert isec._categorize(item) == set()

    def test_israeli_company_entity_admits_item_to_competition(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: set())
        monkeypatch.setattr("eoa.pipeline.israel_focus.israeli_watchlist_names", lambda: ["Elbit"])
        item = {
            "id": 1,
            "israel_reasons": [],
            "summary_he": "",
            "so_what_he": "",
            "title": "",
            "entities_mentioned": ["Elbit"],
        }
        assert isec._categorize(item) == {isec._CATEGORY_COMPETITION}

    @pytest.mark.parametrize("kind", ["partnership", "investment", "test", "launch"])
    def test_business_event_kind_admits_item_to_competition(
        self, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        """These four kinds have no dedicated bucket of their own (unlike
        contract_award/m_and_a/deployment, which land directly in `wins`), so an otherwise-
        uncategorized item with one of them is admitted to the default 'competition' bucket."""
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: {kind})
        item = {
            "id": 1,
            "israel_reasons": [],
            "summary_he": "",
            "so_what_he": "",
            "title": "",
            "entities_mentioned": ["IDF"],
        }
        cats = isec._categorize(item)
        assert cats == {isec._CATEGORY_COMPETITION}

    @pytest.mark.parametrize("kind", ["contract_award", "m_and_a", "deployment"])
    def test_win_event_kind_lands_in_wins_not_the_default_bucket(
        self, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        """A win-kind item lands in `wins` directly (pre-existing behaviour) -- the new default-
        bucket eligibility gate only ever runs when `cats` is still empty at that point, so an
        item that already has a dedicated bucket never additionally falls into `competition` here
        just because its only entity is a government/military org."""
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: {kind})
        item = {
            "id": 1,
            "israel_reasons": [],
            "summary_he": "",
            "so_what_he": "",
            "title": "",
            "entities_mentioned": ["IDF"],
        }
        assert isec._categorize(item) == {isec._CATEGORY_WINS}

    def test_daily_israel_tables_excludes_political_op_ed_but_keeps_business_item(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end reproduction of the judge's exact live finding: an op-ed tagged only 'IDF'
        must not land in the rendered "תחרות ומתחרים" table, while a real business item does."""
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: set())
        monkeypatch.setattr("eoa.pipeline.israel_focus.israeli_watchlist_names", lambda: ["Elbit"])
        op_ed_item = {
            "id": 7,
            "title": 'עד הבחירות יהיה צה"ל הקורבן הראשי',
            "israel_reasons": [],
            "entities_mentioned": ["IDF"],
            "summary_he": "",
            "so_what_he": "",
        }
        business_item = {
            "id": 8,
            "title": "Elbit wins contract",
            "israel_reasons": [],
            "entities_mentioned": ["Elbit"],
            "summary_he": "",
            "so_what_he": "",
        }
        monkeypatch.setattr(
            isec, "collect_israel_items", lambda start, end, min_relevance=0.5: [op_ed_item, business_item]
        )
        tables = isec.daily_israel_tables([], dt.datetime(2026, 9, 1), dt.datetime(2026, 9, 2))
        titles_shown = {row[0] for t in tables for row in t["rows"]}
        assert "Elbit wins contract" in titles_shown
        assert 'עד הבחירות יהיה צה"ל הקורבן הראשי' not in titles_shown


# --------------------------------------------------------------------------
# Finding 3: the D6 "no_duplicate_sentences" scorer no longer flags a table-row title that
# legitimately repeats in the "נספח מקורות" appendix (the appendix is a citation registry, not
# narrative prose); two *non*-appendix sections restating the same sentence is still caught.
# --------------------------------------------------------------------------

_TITLE_WITH_PUNCTUATION = "זה שעולה וזה שיורד: אילו מקצועות יהפכו מבוקשים בעידן ה-AI?"

_MD_APPENDIX_TITLE_OVERLAP = f"""# דוח יומי לבדיקה

## תקציר מנהלים

תקציר קצר עם ציטוט אחד [1].

## תעשייה ישראלית — תחרות ומתחרים

| כותרת | ישויות | מקור |
|---|---|---|
| {_TITLE_WITH_PUNCTUATION} | Elbit | [1] |

## נספח מקורות

| # | כותרת | מקור | תאריך | קישור |
|---|---|---|---|---|
| <a id="src-1"></a>1 | {_TITLE_WITH_PUNCTUATION} | Example Source | 2026-09-06 | [https://example.com/1](https://example.com/1) |
"""

_MD_NON_APPENDIX_DUPLICATE = f"""# דוח לבדיקה

## תקציר מנהלים

תקציר קצר עם ציטוט אחד [1].

## תעשייה ישראלית — תחרות ומתחרים

| כותרת | ישויות | מקור |
|---|---|---|
| {_TITLE_WITH_PUNCTUATION} | Elbit | [1] |

## מעקב טכנולוגי

| כותרת | תחום | מקור |
|---|---|---|
| {_TITLE_WITH_PUNCTUATION} | תחום | [1] |

## נספח מקורות

| # | כותרת | מקור | תאריך | קישור |
|---|---|---|---|---|
| <a id="src-1"></a>1 | כותרת אחרת לגמרי שאינה חוזרת על עצמה בשום מקום | Example Source | 2026-09-06 | [https://example.com/1](https://example.com/1) |
"""


def _dup_check(score: d6.DomainScore) -> d6.Check:
    return next(c for c in score.checks if c.name == "no_duplicate_sentences")


def test_score_d6_appendix_title_overlap_not_flagged_as_duplicate(tmp_path):
    md_path = tmp_path / "daily_appendix_overlap.md"
    md_path.write_text(_MD_APPENDIX_TITLE_OVERLAP, encoding="utf-8")
    score = d6.score_D6(md_path, run_link_check=False)
    assert _dup_check(score).passed


def test_score_d6_still_flags_duplicate_across_two_non_appendix_sections(tmp_path):
    md_path = tmp_path / "daily_real_duplicate.md"
    md_path.write_text(_MD_NON_APPENDIX_DUPLICATE, encoding="utf-8")
    score = d6.score_D6(md_path, run_link_check=False)
    assert not _dup_check(score).passed


# --------------------------------------------------------------------------
# Finding 4: shared Hebrew-quote normaliser, wired into daily/weekly/monthly drafts.
# --------------------------------------------------------------------------


class TestNormalizeHebrewPunctuation:
    def test_doubled_ascii_quotes_collapse_to_gershayim(self):
        assert normalize_hebrew_punctuation('ארה""ב') == f"ארה{_GERSHAYIM}ב"

    def test_single_ascii_quote_between_hebrew_letters_becomes_gershayim(self):
        assert normalize_hebrew_punctuation('צה"ל') == f"צה{_GERSHAYIM}ל"

    def test_apostrophe_after_hebrew_letter_becomes_geresh(self):
        assert normalize_hebrew_punctuation("וכו'") == f"וכו{_GERESH}"

    def test_latin_quotes_are_left_alone(self):
        text = 'Northrop Grumman "Sniper" pod'
        assert normalize_hebrew_punctuation(text) == text

    def test_empty_and_none_are_safe(self):
        assert normalize_hebrew_punctuation(None) is None
        assert normalize_hebrew_punctuation("") == ""

    def test_realistic_full_sentence(self):
        text = 'ממשלת ארה""ב אישרה עסקה עם צה"ל.'
        normalized = normalize_hebrew_punctuation(text)
        assert '""' not in normalized
        assert f"ארה{_GERSHAYIM}ב" in normalized
        assert f"צה{_GERSHAYIM}ל" in normalized


class TestNormalizeDraft:
    def test_structured_daily_draft(self):
        draft = DailyReportDraft(
            exec_summary=[Sentence(text_he='חתמה עם ארה""ב הסכם.', cites=[1])],
            sections=[
                StructuredSection(
                    title_he="כותרת",
                    domain="secondary",
                    sentences=[Sentence(text_he='זהו משפט על צה"ל.', cites=[1])],
                )
            ],
            outlook=[OutlookIndicator(text_he='להערכתנו ארה""ב תמשיך.', cites=[], is_assessment=True)],
            open_points_he=['מה קורה עם ארה""ב?'],
        )
        normalized = normalize_draft(draft)

        assert f"ארה{_GERSHAYIM}ב" in normalized.exec_summary[0].text_he
        assert '""' not in normalized.exec_summary[0].text_he
        assert f"צה{_GERSHAYIM}ל" in normalized.sections[0].sentences[0].text_he
        assert f"ארה{_GERSHAYIM}ב" in normalized.outlook[0].text_he
        assert f"ארה{_GERSHAYIM}ב" in normalized.open_points_he[0]
        # the original draft object is untouched (model_copy, never mutated in place)
        assert '""' in draft.exec_summary[0].text_he

    def test_weekly_draft_trends_and_analyst_note(self):
        draft = WeeklyReportDraft(
            exec_summary=[Sentence(text_he="עדכון קצר.", cites=[1])],
            trends=[
                WeeklyTrendSection(
                    title_he='מגמה בתעשיית ארה""ב',
                    sentences=[Sentence(text_he='נרשמה עלייה בפעילות בארה""ב.', cites=[1])],
                )
            ],
            sections=[],
            analyst_note_he=AnalystNote(sentences_he=['להערכתנו זה קשור לארה""ב.']),
            outlook=[],
            open_points_he=[],
        )
        normalized = normalize_draft(draft)

        assert f"ארה{_GERSHAYIM}ב" in normalized.trends[0].title_he
        assert f"ארה{_GERSHAYIM}ב" in normalized.trends[0].sentences[0].text_he
        assert f"ארה{_GERSHAYIM}ב" in normalized.analyst_note_he.sentences_he[0]

    def test_legacy_monthly_draft(self):
        draft = MonthlyReportDraftLegacy(
            exec_summary_he='סיכום על ארה""ב.',
            trend_paragraphs=[TrendParagraph(title_he="מגמה", prose_he='התפתחות בארה""ב.')],
            sections=[ReportSection(title_he="סעיף", domain="secondary", prose_he='עוד על ארה""ב.')],
            outlook_he='להערכתנו ארה""ב תמשיך.',
            open_points_he=['מה עם ארה""ב?'],
        )
        normalized = normalize_draft(draft)

        assert f"ארה{_GERSHAYIM}ב" in normalized.exec_summary_he
        assert f"ארה{_GERSHAYIM}ב" in normalized.trend_paragraphs[0].prose_he
        assert f"ארה{_GERSHAYIM}ב" in normalized.sections[0].prose_he
        assert f"ארה{_GERSHAYIM}ב" in normalized.outlook_he
        assert f"ארה{_GERSHAYIM}ב" in normalized.open_points_he[0]

    def test_no_op_when_nothing_needs_normalizing(self):
        draft = DailyReportDraft(
            exec_summary=[Sentence(text_he="משפט תקין.", cites=[1])],
            sections=[],
            outlook=[],
            open_points_he=[],
        )
        normalized = normalize_draft(draft)
        assert normalized.exec_summary[0].text_he == "משפט תקין."


# --------------------------------------------------------------------------
# Finding 5: a quarantined `.contaminated.md.bak` weekly report file must not stop the next
# `build_weekly()` run from producing a fresh `weekly_<date>.md` -- nothing in weekly.py checks
# for or short-circuits on an existing file at all (verified by reading `_report_path`/
# `build_weekly`: it always collects, drafts and writes unconditionally).
# --------------------------------------------------------------------------

_BAK_TEST_ITEMS = [
    {
        "id": 501,
        "n": 1,
        "title": "Item for the quarantine regression test",
        "domain": "airborne_pods",
        "source_name": "Test Source",
        "url": "https://example.com/501",
        "published_at": dt.date(2026, 9, 1),
        "level": "red",
        "summary_he": "תקציר לבדיקה.",
        "so_what_he": "משמעות לבדיקה.",
    }
]


def _minimal_weekly_draft() -> WeeklyReportDraft:
    return WeeklyReportDraft(
        exec_summary=[Sentence(text_he="עדכון קצר לבדיקת הרגרסיה.", cites=[1])],
        trends=[],
        sections=[],
        outlook=[],
        open_points_he=[],
    )


def test_build_weekly_ignores_existing_contaminated_backup_and_writes_fresh_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # `_reports_to_tmp` (tests/conftest.py, autouse) already points `EOA_REPORT_OUTPUT_DIR` at
    # `tmp_path / "reports"` -- deliberately NOT monkeypatching `weekly._report_path` here (unlike
    # the fixture in test_report_weekly_monthly.py) so this test exercises the *real* path
    # computation and can plant the quarantined file at the exact stem `build_weekly` will target.
    period_end = dt.date(2026, 9, 5)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    bak_path = reports_dir / f"weekly_{period_end.isoformat()}.contaminated.md.bak"
    bak_content = "### CONTAMINATED CONTENT -- DO NOT USE\n"
    bak_path.write_text(bak_content, encoding="utf-8")

    monkeypatch.setattr(weekly, "collect_week_items", lambda s, e: [dict(it) for it in _BAK_TEST_ITEMS])
    monkeypatch.setattr(weekly, "collect_yellow_domain_summary", lambda s, e: [])
    monkeypatch.setattr(weekly, "collect_events", lambda s, e, limit=None: [])
    monkeypatch.setattr(weekly, "collect_deep_search", lambda s, e: [])
    monkeypatch.setattr(weekly, "collect_open_clarifications", lambda: [])
    monkeypatch.setattr(weekly.trends_mod, "detect_trends", lambda period: [])
    monkeypatch.setattr(
        weekly,
        "collect_meta_summary",
        lambda s, e: {"lessons": [], "feedback_total": 0, "feedback_deltas": []},
    )
    monkeypatch.setattr(weekly, "upcoming_conferences", lambda days=90: [])
    monkeypatch.setattr(weekly, "draft_weekly", lambda *a, **k: _minimal_weekly_draft())
    monkeypatch.setattr(weekly, "_persist_report", lambda *a, **k: 999)

    paths = weekly.build_weekly(period_end=period_end)

    fresh_md = reports_dir / f"weekly_{period_end.isoformat()}.md"
    assert paths.md == fresh_md
    assert fresh_md.exists()
    fresh_text = fresh_md.read_text(encoding="utf-8")
    assert "CONTAMINATED" not in fresh_text
    assert "עדכון קצר לבדיקת הרגרסיה" in fresh_text
    # the stale backup is left exactly as it was -- build_weekly never reads it
    assert bak_path.read_text(encoding="utf-8") == bak_content
    assert paths.qa.passed
