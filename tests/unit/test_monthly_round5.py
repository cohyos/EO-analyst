"""Round 5 P1 (2026-09-06, docs/REPORT_TEMPLATE_BENCHMARK.md M1-M3): the monthly report's
migration to the structured, citation-by-construction schema (``MonthlyReportDraft``) plus
month-over-month trend tracking (M2) and the ``OutlookIndicator`` outlook (M3).

Every DB- and LLM-touching collector is monkeypatched (fakes for ``chat_structured``), same
convention as ``tests/unit/test_report_weekly_monthly.py`` / ``tests/unit/test_report_daily.py``,
so these run with no Postgres and no Ollama.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_monthly_round5.py -q``
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from eoa.llm.schemas.analysis import OutlookIndicator, ReportSection, Sentence, StructuredSection
from eoa.llm.schemas.reports import (
    MonthlyReportDraft,
    MonthlyReportDraftLegacy,
    MonthlyTrendSection,
    TrendParagraph,
)
from eoa.report import israel_section, monthly
from eoa.report.qa_citations import check

# --------------------------------------------------------------------------
# MonthlyTrendSection: change/strength consistency validator (pure, no DB/LLM)
# --------------------------------------------------------------------------


class TestMonthlyTrendSectionValidation:
    def test_new_trend_requires_no_strength_prev(self):
        MonthlyTrendSection(
            title_he="מגמה חדשה",
            domain="c_uas",
            sentences=[Sentence(text_he="טקסט.", cites=[1])],
            strength_now=3,
            change="new",
        )

    def test_new_trend_rejects_strength_prev(self):
        with pytest.raises(ValueError, match="must not have strength_prev"):
            MonthlyTrendSection(
                title_he="מגמה",
                domain="c_uas",
                sentences=[Sentence(text_he="טקסט.", cites=[1])],
                strength_now=3,
                strength_prev=2,
                change="new",
            )

    def test_stronger_requires_strength_now_greater_than_prev(self):
        with pytest.raises(ValueError, match="requires strength_now > strength_prev"):
            MonthlyTrendSection(
                title_he="מגמה",
                domain="c_uas",
                sentences=[Sentence(text_he="טקסט.", cites=[1])],
                strength_now=2,
                strength_prev=3,
                change="stronger",
            )

    def test_weaker_requires_strength_now_less_than_prev(self):
        with pytest.raises(ValueError, match="requires strength_now < strength_prev"):
            MonthlyTrendSection(
                title_he="מגמה",
                domain="c_uas",
                sentences=[Sentence(text_he="טקסט.", cites=[1])],
                strength_now=4,
                strength_prev=3,
                change="weaker",
            )

    def test_gone_forbids_sentences_and_strength_now(self):
        # valid: no sentences, no strength_now
        MonthlyTrendSection(
            title_he="מגמה ישנה",
            domain="c_uas",
            sentences=[],
            strength_now=None,
            strength_prev=3,
            change="gone",
        )
        with pytest.raises(ValueError, match="must not carry sentences"):
            MonthlyTrendSection(
                title_he="מגמה ישנה",
                domain="c_uas",
                sentences=[Sentence(text_he="טקסט.", cites=[1])],
                strength_now=None,
                strength_prev=3,
                change="gone",
            )
        with pytest.raises(ValueError, match="must not have strength_now"):
            MonthlyTrendSection(
                title_he="מגמה ישנה",
                domain="c_uas",
                sentences=[],
                strength_now=2,
                strength_prev=3,
                change="gone",
            )

    def test_non_gone_requires_at_least_one_sentence(self):
        with pytest.raises(ValueError, match="at least one Sentence"):
            MonthlyTrendSection(title_he="מגמה", domain="c_uas", sentences=[], strength_now=3, change="new")


# --------------------------------------------------------------------------
# M2 pure-function helpers: matching, prompt-block formatting, "gone" injection, trend body prose
# --------------------------------------------------------------------------


class TestMonthOverMonthHelpers:
    def test_match_previous_trend_exact_title(self):
        previous = [
            {"title_he": "מגמה: פעילות מוגברת סביב IAI", "domain": "naval_surveillance", "strength": 3}
        ]
        match = monthly._match_previous_trend("מגמה: פעילות מוגברת סביב IAI", previous)
        assert match is not None
        assert match["strength"] == 3

    def test_match_previous_trend_normalizes_whitespace_and_case(self):
        previous = [{"title_he": "  Trend   ABC  ", "domain": "c_uas", "strength": 2}]
        assert monthly._match_previous_trend("trend abc", previous) is not None

    def test_match_previous_trend_no_match_returns_none(self):
        previous = [{"title_he": "מגמה אחרת לגמרי", "domain": "c_uas", "strength": 2}]
        assert monthly._match_previous_trend("מגמה: פעילות מוגברת סביב IAI", previous) is None

    def test_format_monthly_trends_block_marks_new_trend_explicitly(self):
        trend_list = [
            {
                "kind": "entity_cluster",
                "title_he": "מגמה חדשה",
                "evidence_item_ids": [1],
                "entities": ["IAI"],
                "strength": 3,
            }
        ]
        # CR round 14: with NO previous monthly at all the block must not call the trend "new" --
        # there is simply no comparison basis (the model wrote "מגמה חדשה החודש" for all ten).
        block = monthly.format_monthly_trends_block(trend_list, {1: 1}, previous_trends=[])
        assert "אין דוח חודשי קודם להשוואה" in block
        assert "לא הופיעה בדוח החודשי הקודם — מגמה חדשה" not in block
        # ...but when a previous monthly exists and lacks this trend, it IS new.
        previous = [{"title_he": "מגמה אחרת לגמרי", "domain": "c_uas", "strength": 2}]
        block2 = monthly.format_monthly_trends_block(trend_list, {1: 1}, previous_trends=previous)
        assert "לא הופיעה בדוח החודשי הקודם — מגמה חדשה" in block2

    def test_format_monthly_trends_block_carries_previous_strength(self):
        trend_list = [
            {
                "kind": "entity_cluster",
                "title_he": "מגמה נמשכת",
                "evidence_item_ids": [1],
                "entities": ["IAI"],
                "strength": 4,
            }
        ]
        previous = [{"title_he": "מגמה נמשכת", "domain": "naval_surveillance", "strength": 2}]
        block = monthly.format_monthly_trends_block(trend_list, {1: 1}, previous_trends=previous)
        assert "חוזק בדוח החודשי הקודם: 2/5" in block
        assert "חוזק החודש: 4/5" in block

    def test_gone_trend_sections_only_for_unmatched_previous_titles(self):
        previous = [
            {"title_he": "מגמה נמשכת", "domain": "naval_surveillance", "strength": 2},
            {"title_he": "מגמה שנעלמה", "domain": "c_uas", "strength": 3},
        ]
        current_titles = {monthly._normalize_trend_title("מגמה נמשכת")}
        gone = monthly._gone_trend_sections(current_titles, previous)
        assert len(gone) == 1
        assert gone[0].title_he == "מגמה שנעלמה"
        assert gone[0].change == "gone"
        assert gone[0].strength_prev == 3
        assert gone[0].strength_now is None
        assert gone[0].sentences == []

    def test_gone_trend_sections_empty_when_all_matched(self):
        previous = [{"title_he": "מגמה נמשכת", "domain": "naval_surveillance", "strength": 2}]
        current_titles = {monthly._normalize_trend_title("מגמה נמשכת")}
        assert monthly._gone_trend_sections(current_titles, previous) == []

    def test_render_trend_body_gone(self):
        trend = MonthlyTrendSection(
            title_he="מגמה שנעלמה",
            domain="c_uas",
            sentences=[],
            strength_now=None,
            strength_prev=3,
            change="gone",
        )
        body = monthly._render_trend_body(trend, has_previous_report=True)
        assert "לא נמצאו לה ראיות חדשות החודש" in body
        assert "3/5" in body

    def test_render_trend_body_stronger_includes_before_after_numbers(self):
        trend = MonthlyTrendSection(
            title_he="מגמה",
            domain="c_uas",
            sentences=[Sentence(text_he="הפעילות גברה.", cites=[1])],
            strength_now=4,
            strength_prev=2,
            change="stronger",
        )
        body = monthly._render_trend_body(trend, has_previous_report=True)
        assert "התחזקה מ-2/5" in body
        assert "ל-4/5" in body
        assert "הפעילות גברה. [1]" in body

    def test_render_trend_body_new_has_no_before_number(self):
        trend = MonthlyTrendSection(
            title_he="מגמה",
            domain="c_uas",
            sentences=[Sentence(text_he="נתגלתה מגמה.", cites=[1])],
            strength_now=3,
            change="new",
        )
        body = monthly._render_trend_body(trend, has_previous_report=True)
        assert "מגמה חדשה החודש." in body

    def test_render_trend_body_new_with_no_previous_report_is_labeled_honestly(self):
        """CR-monthly.md item 1(c): the FIRST monthly report ever built has no previous report at
        all -- every trend's ``change`` is "new" by construction (nothing to compare to), but that
        must never read as "מגמה חדשה החודש" (a claim about THIS month vs. a previous one); it must
        say plainly there was no previous report to compare against."""
        trend = MonthlyTrendSection(
            title_he="מגמה",
            domain="c_uas",
            sentences=[Sentence(text_he="נתגלתה מגמה.", cites=[1])],
            strength_now=3,
            change="new",
        )
        body = monthly._render_trend_body(trend, has_previous_report=False)
        assert "מגמה חדשה החודש" not in body
        assert "לא נמדדה בחודש הקודם (אין דוח קודם)" in body


# --------------------------------------------------------------------------
# MonthlyReportDraft structured schema: qa_citations.check dispatches to the structured path
# --------------------------------------------------------------------------

ITEMS_REGISTRY = [{"n": 1}, {"n": 2}, {"n": 3}]


class TestMonthlyReportDraftStructuredQA:
    def test_happy_path_every_sentence_cited_passes(self):
        draft = MonthlyReportDraft(
            exec_summary=[Sentence(text_he="תקציר כללי.", cites=[1])],
            trends=[
                MonthlyTrendSection(
                    title_he="מגמה",
                    domain="c_uas",
                    sentences=[Sentence(text_he="מגמה חדשה בתחום.", cites=[2])],
                    strength_now=3,
                    change="new",
                )
            ],
            sections=[
                StructuredSection(
                    title_he="סעיף", domain="c_uas", sentences=[Sentence(text_he="עובדה בסעיף.", cites=[3])]
                )
            ],
            outlook=[OutlookIndicator(text_he="להערכתנו המגמה תימשך.", cites=[], is_assessment=True)],
            open_points_he=[],
        )
        result = check(draft, ITEMS_REGISTRY)
        assert result.passed, result.errors

    def test_unknown_reference_is_rejected(self):
        draft = MonthlyReportDraft(
            exec_summary=[Sentence(text_he="תקציר עם הפניה לא קיימת.", cites=[999])],
            trends=[],
            sections=[],
            outlook=[],
            open_points_he=[],
        )
        result = check(draft, ITEMS_REGISTRY)
        assert not result.passed
        assert 999 in result.bad_refs

    def test_docx_builder_treats_it_as_structured_not_legacy(self):
        from eoa.report.docx_builder import _is_legacy_prose_draft

        draft = MonthlyReportDraft(exec_summary=[], trends=[], sections=[], outlook=[], open_points_he=[])
        assert _is_legacy_prose_draft(draft) is False


# --------------------------------------------------------------------------
# Legacy class still parses (persisted-report backward compatibility)
# --------------------------------------------------------------------------


class TestLegacyMonthlyDraftStillParses:
    def test_legacy_class_round_trips_old_shape(self):
        legacy = MonthlyReportDraftLegacy(
            exec_summary_he="תקציר ישן [1].",
            trend_paragraphs=[TrendParagraph(title_he="מגמה ישנה", prose_he="פרוזה ישנה [1].")],
            sections=[ReportSection(title_he="סעיף ישן", domain="c_uas", prose_he="תוכן ישן [1].")],
            outlook_he="להערכתנו זה יימשך.",
            open_points_he=["שאלה פתוחה?"],
        )
        payload = legacy.model_dump_json()
        reparsed = MonthlyReportDraftLegacy.model_validate(json.loads(payload))
        assert reparsed.exec_summary_he == "תקציר ישן [1]."
        assert reparsed.trend_paragraphs[0].title_he == "מגמה ישנה"

    def test_legacy_class_is_distinct_from_new_structured_class(self):
        assert not hasattr(MonthlyReportDraftLegacy, "model_fields") or (
            "exec_summary" not in MonthlyReportDraftLegacy.model_fields
        )
        assert "exec_summary_he" not in MonthlyReportDraft.model_fields


# --------------------------------------------------------------------------
# build_monthly integration: fakes for chat_structured drive the retry/fallback loop end to end
# --------------------------------------------------------------------------

MONTH_ITEMS = [
    {
        "id": 201,
        "n": 1,
        "title": "IAI wins naval radar deal",
        "domain": "naval_surveillance",
        "source_name": "Naval News",
        "url": "https://example.com/201",
        "published_at": dt.date(2026, 8, 5),
        "level": "red",
        "summary_he": "IAI זכתה בחוזה חדש לאספקת מערכת EO ימית.",
        "so_what_he": "מחזק את מעמדה התחרותי.",
    },
]

_TREND_TITLE = "מגמה: פעילות מוגברת סביב IAI בתחום ניווט ימי"


def _valid_monthly_draft(*, strength_now=4, strength_prev=None, change="new") -> MonthlyReportDraft:
    trend_kwargs = {"strength_prev": strength_prev} if strength_prev is not None else {}
    return MonthlyReportDraft(
        exec_summary=[Sentence(text_he="IAI זכתה בחוזה חדש לאספקת מערכת EO ימית.", cites=[1])],
        trends=[
            MonthlyTrendSection(
                title_he=_TREND_TITLE,
                domain="naval_surveillance",
                sentences=[Sentence(text_he="הפעילות סביב IAI התחזקה בתחום הימי.", cites=[1])],
                strength_now=strength_now,
                change=change,
                **trend_kwargs,
            )
        ],
        sections=[
            StructuredSection(
                title_he="תצפית ימית",
                domain="naval_surveillance",
                sentences=[Sentence(text_he="IAI זכתה בחוזה חדש.", cites=[1])],
            ),
        ],
        outlook=[OutlookIndicator(text_he="להערכתנו המגמה תימשך.", cites=[], is_assessment=True)],
        open_points_he=[],
    )


def _invalid_monthly_draft() -> MonthlyReportDraft:
    return MonthlyReportDraft(
        exec_summary=[Sentence(text_he="טענה עם הפניה לא קיימת.", cites=[999])],
        trends=[],
        sections=[],
        outlook=[],
        open_points_he=[],
    )


@pytest.fixture
def patch_monthly_collectors(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    monkeypatch.setattr(monthly, "collect_month_items", lambda s, e: [dict(it) for it in MONTH_ITEMS])
    monkeypatch.setattr(monthly, "collect_yellow_domain_summary", lambda s, e, limit=None: [])
    monkeypatch.setattr(monthly, "collect_events", lambda s, e, limit=None: [])
    monkeypatch.setattr(monthly, "collect_deep_search", lambda s, e, limit=None: [])
    monkeypatch.setattr(monthly, "collect_open_clarifications", lambda: [])
    monkeypatch.setattr(
        monthly.trends_mod,
        "detect_trends",
        lambda period: [
            {
                "kind": "entity_cluster",
                "title_he": _TREND_TITLE,
                "evidence_item_ids": [201],
                "entities": ["IAI"],
                "strength": 4,
            }
        ],
    )
    monkeypatch.setattr(monthly, "collect_previous_monthly_trends", lambda period_start: [])
    # CR-monthly.md item 1(c): this DB lookup isn't mocked elsewhere in this fixture -- default to
    # "no previous report" (matches collect_previous_monthly_trends's own empty default above).
    monkeypatch.setattr(monthly, "_has_previous_monthly_report", lambda period_start: False)
    monkeypatch.setattr(monthly, "players_map", lambda: {})
    monkeypatch.setattr(monthly, "top_events_by_amount", lambda s, e, limit=10: [])
    monkeypatch.setattr(monthly, "full_horizon_table", lambda: [])
    monkeypatch.setattr(monthly, "watchlist_changes", lambda s, e: [])
    # Section 3 known-failure fix (audit SOL-AUDIT-2026-09-24): `build_monthly`'s two-failure
    # fallback path does `from eoa.report.israel_section import collect_israel_items` INSIDE the
    # function body (see monthly.py), so it re-resolves `israel_section.collect_israel_items` at
    # call time -- it must be patched on the `israel_section` module itself, not on `monthly`.
    # Unmocked, it hits the live DB and can inject extra live items/citation numbers into the
    # fallback draft, making TestBuildMonthlyDeterministicFallback's fixed-registry assertion
    # (`check(draft, [dict(it) for it in MONTH_ITEMS])`) flaky/non-deterministic.
    monkeypatch.setattr(israel_section, "collect_israel_items", lambda *a, **k: [])

    def _fake_persist(start, end, docx_path, md_path, html_path, items, qa, draft, **kwargs):
        captured["draft"] = draft
        captured["qa"] = qa
        return 888

    monkeypatch.setattr(monthly, "_persist_report", _fake_persist)
    monkeypatch.setattr(monthly, "_report_path", lambda period_end, ext: tmp_path / f"monthly.{ext}")
    return captured


class TestBuildMonthlyHappyPath:
    def test_happy_path_renders_every_citation_and_qa_passes(self, monkeypatch, patch_monthly_collectors):
        calls = []

        def fake_chat_structured(role, schema, messages, **kw):
            calls.append(messages)
            return _valid_monthly_draft()

        monkeypatch.setattr(monthly, "chat_structured", fake_chat_structured)
        paths = monthly.build_monthly(period_end=dt.date(2026, 8, 31))

        assert paths.qa.passed, paths.qa.errors
        assert len(calls) == 1  # no retry needed
        md_text = paths.md.read_text(encoding="utf-8")
        assert "[1]" in md_text
        assert "IAI זכתה בחוזה חדש" in md_text


class TestBuildMonthlyCorrectiveRetry:
    def test_unknown_reference_rejected_then_repaired_on_retry(self, monkeypatch, patch_monthly_collectors):
        calls = []

        def fake_chat_structured(role, schema, messages, **kw):
            calls.append(messages)
            if len(calls) == 1:
                return _invalid_monthly_draft()
            return _valid_monthly_draft()

        monkeypatch.setattr(monthly, "chat_structured", fake_chat_structured)
        paths = monthly.build_monthly(period_end=dt.date(2026, 8, 31))

        assert len(calls) == 2, "must retry exactly once after the first citation failure"
        assert paths.qa.passed, paths.qa.errors
        # the corrective-retry prompt must include the QA error text so the model can fix it
        correction_msg = calls[1][-1]["content"]
        assert "האזכורים" in correction_msg


class TestBuildMonthlyDeterministicFallback:
    def test_two_failures_fall_back_to_deterministic_cited_summary(
        self, monkeypatch, patch_monthly_collectors
    ):
        calls = []

        def fake_chat_structured(role, schema, messages, **kw):
            calls.append(messages)
            return _invalid_monthly_draft()

        monkeypatch.setattr(monthly, "chat_structured", fake_chat_structured)
        paths = monthly.build_monthly(period_end=dt.date(2026, 8, 31))

        assert len(calls) == 2, "initial draft + exactly one corrective retry, then fallback"
        assert not paths.qa.passed
        draft = patch_monthly_collectors["draft"]
        assert draft.exec_summary, "the fallback must never leave an empty exec summary"
        assert all(s.cites for s in draft.exec_summary)
        assert "תקציר מובנה אוטומטית" in draft.system_note_he
        # every citation in the deterministic fallback must itself pass QA
        fallback_qa = check(draft, [dict(it) for it in MONTH_ITEMS])
        assert fallback_qa.passed, fallback_qa.errors


class TestBuildMonthlyMonthOverMonth:
    def test_month_over_month_change_from_fake_previous_report(self, monkeypatch, patch_monthly_collectors):
        previous_trends = [
            {"title_he": _TREND_TITLE, "domain": "naval_surveillance", "strength": 2},
            {"title_he": "מגמה ישנה שנעלמה החודש", "domain": "c_uas", "strength": 3},
        ]
        monkeypatch.setattr(monthly, "collect_previous_monthly_trends", lambda period_start: previous_trends)

        def fake_chat_structured(role, schema, messages, **kw):
            # the prompt must be grounded in the previous report's real strength (2), not invented
            prompt = messages[1]["content"]
            assert "חוזק בדוח החודשי הקודם: 2/5" in prompt
            return _valid_monthly_draft(strength_now=4, strength_prev=2, change="stronger")

        monkeypatch.setattr(monthly, "chat_structured", fake_chat_structured)
        paths = monthly.build_monthly(period_end=dt.date(2026, 8, 31))

        assert paths.qa.passed, paths.qa.errors
        draft = patch_monthly_collectors["draft"]
        titles = {t.title_he: t for t in draft.trends}
        assert titles[_TREND_TITLE].change == "stronger"
        assert titles[_TREND_TITLE].strength_prev == 2

        gone_title = "מגמה ישנה שנעלמה החודש"
        assert gone_title in titles, "an unmatched previous-month trend must be injected as change='gone'"
        assert titles[gone_title].change == "gone"
        assert titles[gone_title].sentences == []

        md_text = paths.md.read_text(encoding="utf-8")
        assert "לא נמצאו לה ראיות חדשות החודש" in md_text

    def test_no_previous_report_everything_is_new(self, monkeypatch, patch_monthly_collectors):
        # patch_monthly_collectors already returns [] from collect_previous_monthly_trends

        def fake_chat_structured(role, schema, messages, **kw):
            prompt = messages[1]["content"]
            assert "לא הופיעה בדוח החודשי הקודם — מגמה חדשה" in prompt
            return _valid_monthly_draft(strength_now=4, change="new")

        monkeypatch.setattr(monthly, "chat_structured", fake_chat_structured)
        paths = monthly.build_monthly(period_end=dt.date(2026, 8, 31))

        assert paths.qa.passed, paths.qa.errors
        draft = patch_monthly_collectors["draft"]
        assert draft.trends[0].change == "new"
        assert draft.trends[0].strength_prev is None


class TestBuildMonthlyNoItems:
    def test_zero_items_skips_llm_and_still_persists(self, monkeypatch, tmp_path):
        monkeypatch.setattr(monthly, "collect_month_items", lambda s, e: [])
        monkeypatch.setattr(monthly, "collect_yellow_domain_summary", lambda s, e, limit=None: [])
        monkeypatch.setattr(monthly, "collect_events", lambda s, e, limit=None: [])
        monkeypatch.setattr(monthly, "collect_deep_search", lambda s, e, limit=None: [])
        monkeypatch.setattr(monthly, "collect_open_clarifications", lambda: [])
        monkeypatch.setattr(monthly.trends_mod, "detect_trends", lambda period: [])
        monkeypatch.setattr(monthly, "collect_previous_monthly_trends", lambda period_start: [])
        monkeypatch.setattr(monthly, "players_map", lambda: {})
        monkeypatch.setattr(monthly, "top_events_by_amount", lambda s, e, limit=10: [])
        monkeypatch.setattr(monthly, "full_horizon_table", lambda: [])
        monkeypatch.setattr(monthly, "watchlist_changes", lambda s, e: [])
        # unmocked, this is a real SELECT against `reports` (mirrors patch_monthly_collectors above).
        monkeypatch.setattr(monthly, "_has_previous_monthly_report", lambda period_start: False)
        monkeypatch.setattr(monthly, "_persist_report", lambda *a, **k: 1)
        monkeypatch.setattr(monthly, "_report_path", lambda period_end, ext: tmp_path / f"monthly.{ext}")

        def _fail_if_called(*a, **k):
            raise AssertionError("chat_structured should not be called with zero items")

        monkeypatch.setattr(monthly, "chat_structured", _fail_if_called)

        paths = monthly.build_monthly(period_end=dt.date(2026, 8, 31))
        assert paths.qa.passed


class TestBuildMonthlyTopEventGetsCitationOutsideOrdinaryRegistry:
    """F15 follow-up (SOL-REVIEW-2026-09-24 review): `top_events_by_amount` is its own, separately
    collected query -- an event whose item never made it into `collect_month_items`/trend evidence/
    `collect_events` (e.g. a yellow-level item) previously had no registry entry at all, so its own
    top-events table row rendered a bare "—" instead of a real `[n]` citation. `monthly.py`'s
    `_extend_registry_with_ids(citation_items, _top_event_ids)` call (lines ~1009-1012) is meant to
    register it before the table is built. This exercises `build_monthly` end to end and reads the
    actual rendered markdown -- old code (before that call existed) renders "—" for this row."""

    def test_top_event_absent_from_ordinary_registry_gets_a_real_citation(
        self, monkeypatch, patch_monthly_collectors
    ) -> None:
        # item 555 is deliberately NOT in MONTH_ITEMS, the trend evidence, or collect_events --
        # the only place it is ever mentioned is this top-events-by-amount row.
        monkeypatch.setattr(
            monthly,
            "top_events_by_amount",
            lambda s, e, limit=10: [
                {
                    "id": 9001,
                    "item_id": 555,
                    "kind": "contract",
                    "title": "Elbit wins $400M radar contract",
                    "date": dt.date(2026, 8, 12),
                    "amount_usd": 400_000_000,
                    "currency": "USD",
                    "parties": ["Elbit Systems"],
                    "customer": "Undisclosed",
                    "program": None,
                    "confidence": 0.9,
                    "item_url": "https://example.com/555",
                    "item_title": "Elbit wins $400M radar contract",
                    "published_at": dt.datetime(2026, 8, 12, tzinfo=dt.UTC),
                    "source_name": "Defense News",
                }
            ],
        )

        # `_extend_registry_with_ids` fetches the missing item fresh from the DB -- fake that
        # lookup (this test has no live Postgres) so item 555 resolves to a real row instead of
        # being silently dropped as "not found".
        def _fake_connection(timeout: float | None = None):
            class _Cur:
                def execute(self, sql, params):
                    self._ids = params["ids"]

                def fetchall(self):
                    return [
                        {
                            "id": iid,
                            "url": f"https://example.com/{iid}",
                            "title": "Elbit wins $400M radar contract",
                            "published_at": dt.datetime(2026, 8, 12, tzinfo=dt.UTC),
                            "source_name": "Defense News",
                        }
                        for iid in self._ids
                    ]

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            class _Conn:
                def cursor(self):
                    return _Cur()

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return _Conn()

        import eoa.report.weekly as weekly_mod

        monkeypatch.setattr(weekly_mod, "connection", _fake_connection)

        def fake_chat_structured(role, schema, messages, **kw):
            return _valid_monthly_draft()

        monkeypatch.setattr(monthly, "chat_structured", fake_chat_structured)
        paths = monthly.build_monthly(period_end=dt.date(2026, 8, 31))

        assert paths.qa.passed, paths.qa.errors
        md_text = paths.md.read_text(encoding="utf-8")
        # The top-events table row for this event must NOT be the "no citation" placeholder --
        # old code (before the F15 registry-extension call) renders a bare "—" here instead.
        assert "Elbit Systems" in md_text
        top_events_line = next(
            line for line in md_text.splitlines() if "Elbit Systems" in line and "|" in line
        )
        assert "—" not in top_events_line.split("|")[-2], (
            f"top event's citation cell must not be the empty placeholder: {top_events_line!r}"
        )
        assert re_search_bracket_citation(top_events_line), (
            f"expected a real [n] citation in the top-events row: {top_events_line!r}"
        )


def re_search_bracket_citation(text: str) -> bool:
    import re

    return re.search(r"\[\d+\]", text) is not None
