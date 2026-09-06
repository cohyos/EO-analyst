"""Tests for the eoa.qa package (deterministic half of the QA continuous loop).

Every test here except ``TestLiveDbSmoke`` uses small in-memory fixtures / a fake DB cursor -- no
real Postgres connection. ``TestLiveDbSmoke`` is a genuine integration test (marked, skipped
without ``DATABASE_URL``) that only exercises the read-only query shape against the real schema.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_qa_score.py -q``
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from eoa.pipeline.triage import level_for
from eoa.qa.d1_classify import score_D1
from eoa.qa.d2_summary import score_D2
from eoa.qa.d3_events_entities import score_D3
from eoa.qa.d4_investigations import score_D4
from eoa.qa.d5_chat import score_D5
from eoa.qa.d6_daily_report import score_D6
from eoa.qa.d7_bd_report import score_D7
from eoa.qa.d8_patent_survey import score_D8
from eoa.qa.d9_tenders_conferences import score_D9
from eoa.qa.d10_ui_e2e import _pass_ratio, score_D10
from eoa.qa.sample import load_golden_ids, save_golden_ids
from eoa.qa.scorer import DOMAIN_WEIGHTS, weighted_total
from eoa.qa.types import Check, DomainScore, weighted_score

# ---------------------------------------------------------------------------------------------
# fake DB plumbing (no real Postgres) -- a tiny router keyed on a substring of the SQL text.
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


def _item(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 1,
        "domain": "out_of_scope",
        "subdomain": "",
        "title": "",
        "clean_text": "",
        "entities_mentioned": [],
        "key_facts": [],
        "israel_reasons": [],
        "summary_he": "",
        "so_what_he": "",
        "triage_reason": "",
        "uncertainty_he": "",
        "tech_readiness_note_he": "",
        "score": None,
        "level": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------------------------
# types.py
# ---------------------------------------------------------------------------------------------


class TestTypes:
    def test_weighted_score_empty(self) -> None:
        assert weighted_score([]) is None

    def test_weighted_score_all_pass(self) -> None:
        checks = [Check("a", True, 1.0), Check("b", True, 2.0)]
        assert weighted_score(checks) == 100.0

    def test_weighted_score_partial(self) -> None:
        checks = [Check("a", True, 1.0), Check("b", False, 1.0)]
        assert weighted_score(checks) == 50.0

    def test_domain_score_to_dict_roundtrips(self) -> None:
        ds = DomainScore(domain="D1", score_0_100=80.0, checks=[Check("x", True, 1.0, "ok")], n=5)
        d = ds.to_dict()
        assert d["domain"] == "D1"
        assert d["checks"][0]["name"] == "x"


# ---------------------------------------------------------------------------------------------
# D1 -- classification
# ---------------------------------------------------------------------------------------------


class TestD1:
    def test_empty_sample_is_none(self) -> None:
        result = score_D1([], conn=None)
        assert result.score_0_100 is None
        assert result.n == 0

    def test_all_clean_items_score_100(self) -> None:
        good = _item(
            id=1,
            domain="airborne_pods",
            subdomain="targeting_pods",
            title="Rafael delivers Litening targeting pod",
            clean_text="A real EO/IR targeting pod story with sensor payload details.",
            entities_mentioned=["Rafael"],
            key_facts=["עובדה אחת.", "עובדה שנייה."],
            summary_he="תקציר תקין בעברית.",
            score=8,
            level=level_for(8),
            triage_reason="נימוק תקין.",
        )
        result = score_D1([good], conn=None)
        assert result.score_0_100 == 100.0
        assert result.n == 1

    def test_invalid_subdomain_flagged(self) -> None:
        bad = _item(id=2, domain="airborne_pods", subdomain="not_a_real_subdomain", entities_mentioned=["x"])
        result = score_D1([bad], conn=None)
        subdomain_check = next(c for c in result.checks if c.name == "subdomain_valid_vs_taxonomy")
        assert subdomain_check.passed is False

    def test_no_eoir_gate_disagreement_flagged(self) -> None:
        # in-scope domain, zero entities, zero EO/IR vocabulary, zero watchlist alias -> should
        # have been out_of_scope per eoa.pipeline.classify.apply_no_eoir_gate's own rule.
        bad = _item(
            id=3,
            domain="c_uas",
            title="A completely unrelated story about grain exports",
            clean_text="Nothing technical here at all, just commodity prices.",
            entities_mentioned=[],
        )
        result = score_D1([bad], conn=None)
        gate_check = next(c for c in result.checks if c.name == "no_eoir_gate_agreement")
        assert gate_check.passed is False

    def test_reason_score_level_mismatch_flagged(self) -> None:
        bad = _item(id=4, domain="c_uas", score=8, level="yellow", entities_mentioned=["x"])  # wrong level for score=8
        result = score_D1([bad], conn=None)
        reason_check = next(c for c in result.checks if c.name == "reason_score_level_consistency")
        assert reason_check.passed is False

    def test_hebrew_truncation_detected(self) -> None:
        # ends mid-word, no terminal punctuation, long enough to trip the generic net
        truncated = _item(id=5, summary_he="זהו משפט ארוך מספיק שנקטע באמצע המילה ולא מסתיים כראוי בלי סימן")
        result = score_D1([truncated], conn=None)
        trunc_check = next(c for c in result.checks if c.name == "hebrew_truncation_zero_hits")
        assert trunc_check.passed is False

    def test_gershayim_ascii_quote_detected(self) -> None:
        bad = _item(id=6, summary_he='זהו כטב"ם עם גרשיים לא תקינים.')
        result = score_D1([bad], conn=None)
        gershayim_check = next(c for c in result.checks if c.name == "gershayim_no_ascii_quote")
        assert gershayim_check.passed is False

    def test_key_facts_duplicates_detected(self) -> None:
        bad = _item(id=7, key_facts=["עובדה זהה.", "עובדה זהה."])
        result = score_D1([bad], conn=None)
        dup_check = next(c for c in result.checks if c.name == "key_facts_no_duplicates")
        assert dup_check.passed is False

    def test_entities_mentioned_required_in_scope(self) -> None:
        bad = _item(id=8, domain="c_uas", entities_mentioned=[], key_facts=[], title="", clean_text="")
        result = score_D1([bad], conn=None)
        entities_check = next(c for c in result.checks if c.name == "entities_mentioned_nonempty_in_scope")
        assert entities_check.passed is False


# ---------------------------------------------------------------------------------------------
# D2 -- summary
# ---------------------------------------------------------------------------------------------


class TestD2:
    def test_no_in_scope_items_is_none(self) -> None:
        result = score_D2([_item(domain="out_of_scope")])
        assert result.score_0_100 is None

    def test_clean_summary_scores_well(self) -> None:
        good = _item(
            domain="c_uas",
            summary_he="זוהי כתבה על מערכת נגד כטב\"ם (C-UAS) חדשה שפותחה על ידי חברה ביטחונית.",
            so_what_he="המשמעות היא שהתחרות בתחום ה-C-UAS מתחדדת.",
        )
        result = score_D2([good])
        assert result.score_0_100 is not None
        assert result.score_0_100 >= 75.0

    def test_model_chatter_detected(self) -> None:
        bad = _item(domain="c_uas", summary_he="As an AI language model, I cannot provide this summary.")
        result = score_D2([bad])
        chatter_check = next(c for c in result.checks if c.name == "no_model_chatter")
        assert chatter_check.passed is False

    def test_meta_phrase_detected(self) -> None:
        bad = _item(domain="c_uas", summary_he="הכתבה מספקת את כל המידע הדרוש להבנת הנושא במלואו.")
        result = score_D2([bad])
        chatter_check = next(c for c in result.checks if c.name == "no_model_chatter")
        assert chatter_check.passed is False

    def test_non_hebrew_dominant_detected(self) -> None:
        bad = _item(domain="c_uas", summary_he="This is an entirely English summary with no Hebrew content at all here.")
        result = score_D2([bad])
        hebrew_check = next(c for c in result.checks if c.name == "hebrew_dominant")
        assert hebrew_check.passed is False


# ---------------------------------------------------------------------------------------------
# D3 -- events & entities (fake conn)
# ---------------------------------------------------------------------------------------------


class TestD3:
    def test_empty_scope_is_none(self) -> None:
        conn = _FakeConn([("FROM events", []), ("FROM entities", [])])
        result = score_D3([_item(id=1)], conn)
        assert result.score_0_100 is None

    def test_duplicate_events_detected(self) -> None:
        events = [
            {"id": 1, "item_id": 1, "kind": "contract_award", "title": "Same Title", "date": None, "amount_usd": None, "customer": None, "program": None, "parties": None},
            {"id": 2, "item_id": 1, "kind": "contract_award", "title": "same title", "date": None, "amount_usd": None, "customer": None, "program": None, "parties": None},
        ]
        conn = _FakeConn([("FROM events", events), ("FROM entities", [])])
        result = score_D3([_item(id=1)], conn)
        dup_check = next(c for c in result.checks if c.name == "events_duplicate_groups_zero")
        assert dup_check.passed is False

    def test_narrative_event_detected(self) -> None:
        events = [
            {"id": 3, "item_id": 1, "kind": "other", "title": "ייתכן שהמגמה תימשך", "date": None, "amount_usd": None, "customer": None, "program": None, "parties": None},
        ]
        conn = _FakeConn([("FROM events", events), ("FROM entities", [])])
        result = score_D3([_item(id=1)], conn)
        narrative_check = next(c for c in result.checks if c.name == "no_narrative_events")
        assert narrative_check.passed is False

    def test_junk_entity_detected(self) -> None:
        entities = [{"id": 1, "name": "object detection", "kind": "company", "country": None}]
        conn = _FakeConn([("FROM events", []), ("FROM entities", entities)])
        result = score_D3([_item(id=1)], conn)
        junk_check = next(c for c in result.checks if c.name == "entity_junk_gate")
        assert junk_check.passed is False

    def test_invalid_kind_detected(self) -> None:
        entities = [{"id": 1, "name": "Rafael", "kind": "not_a_kind", "country": None}]
        conn = _FakeConn([("FROM events", []), ("FROM entities", entities)])
        result = score_D3([_item(id=1)], conn)
        kind_check = next(c for c in result.checks if c.name == "entity_kind_valid")
        assert kind_check.passed is False


# ---------------------------------------------------------------------------------------------
# D4 -- investigations (fake conn)
# ---------------------------------------------------------------------------------------------


class TestD4:
    def test_empty_scope_is_none(self) -> None:
        conn = _FakeConn([("FROM jobs", [])])
        result = score_D4([], conn)
        assert result.score_0_100 is None

    def test_found_with_no_sources_flagged(self) -> None:
        jobs = [{"id": 1, "payload": {"question": "מה קורה עם Rafael Iron Beam"}, "result": {"outcome": "found", "confidence": 0.9, "sources": [], "answer_he": "תשובה מלאה כאן."}}]
        conn = _FakeConn([("FROM jobs", jobs), ("FROM investigation_log", [])])
        result = score_D4([1], conn)
        sources_check = next(c for c in result.checks if c.name == "sources_nonempty_for_found")
        assert sources_check.passed is False

    def test_not_found_high_confidence_flagged(self) -> None:
        jobs = [{"id": 2, "payload": {"question": "שאלה כלשהי"}, "result": {"outcome": "not_found", "confidence": 0.8, "sources": [], "answer_he": "לא נמצא מידע.", "what_was_tried_he": "בוצע חיפוש."}}]
        conn = _FakeConn([("FROM jobs", jobs), ("FROM investigation_log", [])])
        result = score_D4([2], conn)
        conf_check = next(c for c in result.checks if c.name == "confidence_capped_by_outcome")
        assert conf_check.passed is False

    def test_partial_unverified_prefix_required(self) -> None:
        jobs = [{"id": 3, "payload": {"question": "שאלה"}, "result": {"outcome": "partial", "confidence": 0.3, "sources": [], "answer_he": "תשובה בלי הקידומת הנדרשת."}}]
        conn = _FakeConn([("FROM jobs", jobs), ("FROM investigation_log", [])])
        result = score_D4([3], conn)
        relevance_check = next(c for c in result.checks if c.name == "relevance_check_present_consistent")
        assert relevance_check.passed is False

    def test_legacy_unanchored_job_exempt_from_anchor_check(self) -> None:
        """D1 round-1 fix (docs/qa/loop/round_1_fixes.md): a job labelled by
        scripts/mark_legacy_investigations.py's ``legacy_unanchored`` pass is exempt from
        ``queries_anchored_to_question`` (its queries predate the anchor gate and can't be
        regenerated) but still checked by the other three D4 sub-checks."""
        jobs = [
            {
                "id": 5,
                "payload": {"question": "מה קורה עם Rafael Iron Beam"},
                "result": {
                    "outcome": "found",
                    "confidence": 0.8,
                    "sources": ["https://example.com/a"],
                    "answer_he": "תשובה מלאה כאן.",
                    "legacy_unanchored": True,
                },
            }
        ]
        log_rows = [{"job_id": 5, "query": "completely unrelated query text"}]
        conn = _FakeConn([("FROM jobs", jobs), ("FROM investigation_log", log_rows)])
        result = score_D4([5], conn)
        anchor_check = next(c for c in result.checks if c.name == "queries_anchored_to_question")
        assert anchor_check.passed is True
        sources_check = next(c for c in result.checks if c.name == "sources_nonempty_for_found")
        assert sources_check.passed is True

    def test_well_formed_investigation_passes_all(self) -> None:
        jobs = [
            {
                "id": 4,
                "payload": {"question": "מה קורה עם החוזה של Rafael Iron Beam"},
                "result": {
                    "outcome": "found",
                    "confidence": 0.8,
                    "sources": ["https://example.com/a", "https://example.com/b"],
                    "answer_he": "ה-XM30 כולל מטע\"ד EO/IR מתקדם.",
                },
            }
        ]
        log_rows = [{"job_id": 4, "query": "Rafael Iron Beam contract details"}]
        conn = _FakeConn([("FROM jobs", jobs), ("FROM investigation_log", log_rows)])
        result = score_D4([4], conn)
        assert result.score_0_100 == 100.0


# ---------------------------------------------------------------------------------------------
# D5 -- chat (manual only, no table)
# ---------------------------------------------------------------------------------------------


class TestD5:
    def test_no_chat_table_is_manual_only(self) -> None:
        conn = _FakeConn([])  # no route matches -> fetchone() returns None
        result = score_D5(["question one"], conn)
        assert result.score_0_100 is None
        assert "manual" in result.note.lower()


# ---------------------------------------------------------------------------------------------
# D6 -- daily/weekly report (temp file, no live link check)
# ---------------------------------------------------------------------------------------------

_GOOD_DAILY_MD = """# דוח יומי

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

_BAD_DAILY_MD = """# דוח יומי

## תקציר מנהלים

חברת Rafael חתמה על חוזה בסך 10 מיליון דולר עם משרד הביטחון האמריקאי.

זהו משפט עם הפניה שגויה [7].

## some_raw_slug

תוכן.

## נספח מקורות

| # | כותרת | קישור |
|---|---|---|
| <a id="src-1"></a>1 | כתבה לדוגמה | [https://example.com/a](https://example.com/a) |
"""


class TestD6:
    def test_missing_file_is_none(self, tmp_path: Path) -> None:
        result = score_D6(tmp_path / "does_not_exist.md", run_link_check=False)
        assert result.score_0_100 is None

    def test_clean_report_scores_well(self, tmp_path: Path) -> None:
        path = tmp_path / "daily_2026-01-01.md"
        path.write_text(_GOOD_DAILY_MD, encoding="utf-8")
        result = score_D6(path, run_link_check=False)
        assert result.score_0_100 is not None
        assert result.score_0_100 >= 80.0

    def test_uncited_fact_and_orphan_citation_and_raw_slug_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "daily_2026-01-02.md"
        path.write_text(_BAD_DAILY_MD, encoding="utf-8")
        result = score_D6(path, run_link_check=False)
        uncited_check = next(c for c in result.checks if c.name == "every_factual_exec_summary_sentence_cited")
        orphan_check = next(c for c in result.checks if c.name == "every_inline_citation_in_appendix")
        slug_check = next(c for c in result.checks if c.name == "no_raw_slug_headings")
        assert uncited_check.passed is False
        assert orphan_check.passed is False
        assert slug_check.passed is False


# ---------------------------------------------------------------------------------------------
# D7 -- BD report (temp file, conn=None skips the DB-backed conference-date check)
# ---------------------------------------------------------------------------------------------

_GOOD_BD_MD = """# דוח פיתוח עסקי

## תקציר מנהלים

תוכן.

## כנסים קרובים בטריטוריה

| שם | תאריכים |
|---|---|
| AUSA 2026 | 2026-10-12 - 2026-10-14 |

## פעולות מומלצות

| עדיפות | פעולה |
|---|---|
| גבוהה | להציג את יכולות Rafael בכנס |

## נספח מקורות

תוכן.
"""

_BAD_BD_MD = """# דוח פיתוח עסקי

## תקציר מנהלים

תוכן.

## כנסים קרובים בטריטוריה



## פעולות מומלצות



## נספח מקורות

תוכן.
"""


class TestD7:
    def test_no_files_is_none(self) -> None:
        result = score_D7([], conn=None)
        assert result.score_0_100 is None

    def test_clean_report_scores_well(self, tmp_path: Path) -> None:
        path = tmp_path / "bd_us_2026-01-01.md"
        path.write_text(_GOOD_BD_MD, encoding="utf-8")
        result = score_D7([path], conn=None)
        actions_check = next(c for c in result.checks if c.name == "actions_table_nonempty")
        assert actions_check.passed is True

    def test_empty_headings_and_missing_actions_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "bd_us_2026-01-02.md"
        path.write_text(_BAD_BD_MD, encoding="utf-8")
        result = score_D7([path], conn=None)
        empty_check = next(c for c in result.checks if c.name == "no_empty_headings")
        actions_check = next(c for c in result.checks if c.name == "actions_table_nonempty")
        assert empty_check.passed is False
        assert actions_check.passed is False

    def test_competitor_promotion_detected(self, tmp_path: Path) -> None:
        text = _GOOD_BD_MD.replace("להציג את יכולות Rafael בכנס", "להציג את יכולות Shield AI בכנס")
        path = tmp_path / "bd_us_2026-01-03.md"
        path.write_text(text, encoding="utf-8")
        result = score_D7([path], conn=None)
        promo_check = next(c for c in result.checks if c.name == "no_competitor_promotion_language")
        assert promo_check.passed is False


# ---------------------------------------------------------------------------------------------
# D8 -- patent survey
# ---------------------------------------------------------------------------------------------

_GOOD_PATENT_MD = """# סקר פטנטים

## תקציר מנהלים

תוכן.

## ציר זמן שנתי

| שנה | מספר פטנטים |
|---|---|
| 2024 | 3 |

## קודי CPC מובילים

| קוד CPC | מספר פטנטים |
|---|---|
| G01J5 | 3 |

## נספח מקורות

תוכן.
"""

_DISCLOSURE_PATENT_MD = """# סקר פטנטים

## תקציר מנהלים

תוכן.

## ציר זמן שנתי

אין נתוני ציר זמן שנתי זמינים לפטנטים במדגם זה (חסרים תאריכי פרסום/הגשה במקור הנתונים).

## קודי CPC מובילים

אין נתוני קודי CPC זמינים לפטנטים במדגם זה (מקור החיפוש חסר-המפתחות אינו מספק סיווג CPC).

## נספח מקורות

תוכן.
"""


class TestD8:
    def test_missing_file_is_none(self, tmp_path: Path) -> None:
        result = score_D8(tmp_path / "missing.md")
        assert result.score_0_100 is None

    def test_timeline_and_cpc_present_with_real_data_rows(self, tmp_path: Path) -> None:
        path = tmp_path / "patent_survey_x_2026-01-01.md"
        path.write_text(_GOOD_PATENT_MD, encoding="utf-8")
        result = score_D8(path)
        timeline_check = next(c for c in result.checks if c.name == "timeline_present")
        cpc_check = next(c for c in result.checks if c.name == "cpc_present")
        assert timeline_check.passed is True
        assert cpc_check.passed is True

    def test_timeline_and_cpc_present_with_explicit_disclosure(self, tmp_path: Path) -> None:
        """Round 3 D8 finding 3: a genuinely data-starved survey (no CPC/date data at all) is
        never scored as "missing" as long as it carries the explicit disclosure sentence in place
        of a silently-empty table."""
        path = tmp_path / "patent_survey_d_2026-01-01.md"
        path.write_text(_DISCLOSURE_PATENT_MD, encoding="utf-8")
        result = score_D8(path)
        timeline_check = next(c for c in result.checks if c.name == "timeline_present")
        cpc_check = next(c for c in result.checks if c.name == "cpc_present")
        assert timeline_check.passed is True
        assert cpc_check.passed is True

    def test_timeline_missing_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "patent_survey_y_2026-01-01.md"
        path.write_text("# סקר\n\n## תקציר מנהלים\n\nתוכן.\n", encoding="utf-8")
        result = score_D8(path)
        timeline_check = next(c for c in result.checks if c.name == "timeline_present")
        cpc_check = next(c for c in result.checks if c.name == "cpc_present")
        assert timeline_check.passed is False
        assert cpc_check.passed is False

    def test_bare_heading_with_no_data_or_disclosure_fails(self, tmp_path: Path) -> None:
        """The exact round-1 bug: a heading exists but the table under it has zero data rows and
        no disclosure sentence -- must fail, not silently pass on heading presence alone."""
        path = tmp_path / "patent_survey_bare_2026-01-01.md"
        path.write_text(
            "# סקר\n\n## ציר זמן שנתי\n\n| שנה | מספר פטנטים |\n|---|---|\n\n"
            "## קודי CPC מובילים\n\n| קוד CPC | מספר פטנטים |\n|---|---|\n\n## נספח מקורות\n\nתוכן.\n",
            encoding="utf-8",
        )
        result = score_D8(path)
        timeline_check = next(c for c in result.checks if c.name == "timeline_present")
        cpc_check = next(c for c in result.checks if c.name == "cpc_present")
        assert timeline_check.passed is False
        assert cpc_check.passed is False

    def test_ltr_isolation_checked_from_html(self, tmp_path: Path) -> None:
        md_path = tmp_path / "patent_survey_z_2026-01-01.md"
        md_path.write_text(_GOOD_PATENT_MD, encoding="utf-8")
        html_bad = tmp_path / "patent_survey_z_2026-01-01.html"
        html_bad.write_text("<p>US1234567B2 not wrapped at all</p>", encoding="utf-8")
        result = score_D8(md_path, html_bad)
        ltr_check = next(c for c in result.checks if c.name == "patent_numbers_ltr_isolated")
        assert ltr_check.passed is False

        html_good = tmp_path / "patent_survey_z2_2026-01-01.html"
        html_good.write_text('<p><bdi dir="ltr">US1234567B2</bdi> is wrapped</p>', encoding="utf-8")
        result2 = score_D8(md_path, html_good)
        ltr_check2 = next(c for c in result2.checks if c.name == "patent_numbers_ltr_isolated")
        assert ltr_check2.passed is True


# ---------------------------------------------------------------------------------------------
# D9 -- tenders/conferences/sources (fake conn)
# ---------------------------------------------------------------------------------------------


class TestD9:
    def test_empty_tables_is_none(self) -> None:
        conn = _FakeConn([("FROM tenders", []), ("FROM conferences", []), ("FROM sources", [])])
        result = score_D9(conn)
        assert result.score_0_100 is None

    def test_open_tender_without_dates_flagged(self) -> None:
        tenders = [{"id": 1, "status": "open", "deadline": None, "published_at": None}]
        conn = _FakeConn([("FROM tenders", tenders), ("FROM conferences", []), ("FROM sources", [])])
        result = score_D9(conn)
        check = next(c for c in result.checks if c.name == "tenders_open_rows_have_dates")
        assert check.passed is False

    def test_bad_conference_status_flagged(self) -> None:
        import datetime as dt

        conferences = [{"id": 1, "status": "made_up_status", "start_date": dt.date(2026, 10, 1), "organizer": "X"}]
        conn = _FakeConn([("FROM tenders", []), ("FROM conferences", conferences), ("FROM sources", [])])
        result = score_D9(conn)
        check = next(c for c in result.checks if c.name == "conferences_status_and_dates_real")
        assert check.passed is False

    def test_stale_source_flagged(self) -> None:
        sources = [{"id": 1, "name": "Old Source", "last_fetched_at": None}]
        conn = _FakeConn([("FROM tenders", []), ("FROM conferences", []), ("FROM sources", sources)])
        result = score_D9(conn)
        check = next(c for c in result.checks if c.name == "sources_enabled_fetched_recently")
        assert check.passed is False


# ---------------------------------------------------------------------------------------------
# D10 -- UI/e2e
# ---------------------------------------------------------------------------------------------


class TestD10:
    def test_skipped_by_default(self) -> None:
        result = score_D10(run_e2e=False)
        assert result.score_0_100 is None
        assert "skipped" in result.note.lower()

    def test_pass_ratio_parsing(self) -> None:
        report = {
            "suites": [
                {
                    "specs": [
                        {"tests": [{"results": [{"status": "passed"}]}]},
                        {"tests": [{"results": [{"status": "failed"}]}]},
                    ],
                    "suites": [
                        {"specs": [{"tests": [{"results": [{"status": "passed"}]}]}], "suites": []},
                    ],
                }
            ]
        }
        passed, total = _pass_ratio(report)
        assert (passed, total) == (2, 3)


# ---------------------------------------------------------------------------------------------
# sample.py (JSON roundtrip -- no DB)
# ---------------------------------------------------------------------------------------------


class TestSampleJson:
    def test_save_and_load_golden_ids_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "golden_items.json"
        save_golden_ids(path, item_ids=[1, 2, 3], investigation_job_ids=[10, 20])
        item_ids, job_ids = load_golden_ids(path)
        assert item_ids == [1, 2, 3]
        assert job_ids == [10, 20]


# ---------------------------------------------------------------------------------------------
# scorer.py -- weighted_total
# ---------------------------------------------------------------------------------------------


class TestWeightedTotal:
    def _all_scores(self, value: float) -> dict[str, DomainScore]:
        return {d: DomainScore(domain=d, score_0_100=value, checks=[], n=1) for d in DOMAIN_WEIGHTS}

    def test_uniform_auto_only(self) -> None:
        scores = self._all_scores(80.0)
        assert weighted_total(scores) == 80.0

    def test_blends_judge_50_50(self) -> None:
        scores = self._all_scores(60.0)
        judge = {d: 100.0 for d in DOMAIN_WEIGHTS}
        assert weighted_total(scores, judge_scores=judge) == 80.0

    def test_excludes_domains_with_no_signal_at_all(self) -> None:
        scores = self._all_scores(90.0)
        scores["D5"] = DomainScore(domain="D5", score_0_100=None, checks=[], n=0)
        scores["D10"] = DomainScore(domain="D10", score_0_100=None, checks=[], n=0)
        # D5/D10 have no auto score and no judge score -> excluded from the average entirely,
        # so the total should still be exactly 90.0 (not dragged toward 0).
        assert weighted_total(scores) == 90.0

    def test_manual_only_domain_can_still_contribute_via_judge(self) -> None:
        scores = self._all_scores(90.0)
        scores["D5"] = DomainScore(domain="D5", score_0_100=None, checks=[], n=0)
        total = weighted_total(scores, judge_scores={"D5": 70.0})
        assert total is not None
        assert total < 90.0


# ---------------------------------------------------------------------------------------------
# one genuine integration test -- skipped without DATABASE_URL
# ---------------------------------------------------------------------------------------------


@pytest.mark.integration
class TestLiveDbSmoke:
    def test_score_d9_runs_against_live_schema(self) -> None:
        if not os.environ.get("DATABASE_URL"):
            pytest.skip("DATABASE_URL not set")
        from eoa.db import connection

        with connection() as conn:
            result = score_D9(conn)
        assert result.n >= 0  # just proves the queries run against the real schema without error
