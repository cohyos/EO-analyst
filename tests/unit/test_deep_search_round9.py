"""R9-investigations (docs/qa/loop/round_8_judge.md, D4 = 63; worst-list #2, #3, #4): three fixes
to the deep-search citation-integrity and rerun-reconciliation pipeline.

  1. **Nominalised decision phrasing slips past the hedge-downgrade guard** (job 147,
     ``eoa.search.deep_search._downgrade_unhedged_decision_claims``): job 147's own live rerun
     answer asserted "בחירת נורקין על פני אבולעפיה" ("the selection of Norkin over Abulafia") as
     settled fact while its own cited Globes/Calcalist sources described an unresolved candidate
     process -- but round 8's fixed decision-verb list (הוחלט/נבחר/זכה/נחתם) never matches this
     exact sentence, because "בחירת" is a noun ("the selection of"), not the verb "נבחר" ("was
     selected"). Fixed by widening the check (now `_sentence_claims_settled_decision`) to also
     catch nominalised Hebrew decision/appointment phrases (בחירת/הבחירה ב-/ההחלטה על/המינוי
     של/הזכייה של/החתימה על) and their English equivalents (the selection/appointment of, the
     decision to).
  2. **Citation-integrity invariant made explicit** (``eoa.search.deep_search._tool_read`` /
     ``_finalize_outcome``): a page discarded by ``_low_quality_page_reason`` was already
     structurally excluded from ``sources`` (the single ``read_urls.append`` call site is gated on
     the reason being ``None``), but nothing recorded *that* a URL had been discarded, so the
     invariant "a rejected page can never end up cited" depended entirely on no other code path
     ever touching ``read_urls``. Made explicit and defensive: ``Investigation.low_quality_read_
     urls`` now records every URL ``_tool_read`` discards, and ``_finalize_outcome``'s ``sources``
     assignment filters against it directly rather than only relying on nothing else appending to
     ``read_urls``.
  3. **Duplicate items show contradictory investigation outcomes** (item 96, a plain
     ``dedup_of=10`` Hebrew duplicate of golden item 10; ``eoa.report.daily.collect_deep_search`` /
     ``reconcile_deep_search_reruns``): item 96's own never-rerun ``not_found`` investigation (job
     45) rendered directly beside item 10's newly-fixed ``found`` answer (job 156) in the same live
     weekly report -- the round-8 reconciliation fix groups by question-text and explicit rerun/
     expansion lineage, but has no mechanism for an item's own ``dedup_of`` chain. Fixed by
     extracting each trigger item's ``dedup_of`` link in ``collect_deep_search`` and folding it
     into ``reconcile_deep_search_reruns``'s union-find as a third pass, transitively and in both
     directions.

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 EOA_SEARCH_NO_CACHE=1 .venv/Scripts/python.exe -m pytest
tests/unit/test_deep_search_round9.py -q``
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from eoa.llm.schemas.analysis import InvestigationOut
from eoa.report import daily
from eoa.report.daily import collect_deep_search, reconcile_deep_search_reruns
from eoa.search import deep_search as ds
from eoa.search.deep_search import (
    Budget,
    Investigation,
    SearchHit,
    _downgrade_unhedged_decision_claims,
    _finalize_outcome,
    _sentence_claims_settled_decision,
    _tool_read,
)


def _budget(**overrides) -> Budget:
    base = dict(max_queries=15, max_pages=30, deadline=time.monotonic() + 3600, confidence_stop=0.8)
    base.update(overrides)
    return Budget(**base)


def _hit(url: str, score: float = 0.0) -> SearchHit:
    return SearchHit(url=url, title="Title", snippet="snippet", engine="ddgs", score=score)


@pytest.fixture(autouse=True)
def _no_db(monkeypatch: pytest.MonkeyPatch):
    """Pure-unit tests: never touch the DB/network/LLM (same discipline as
    test_deep_search_round8.py's own `_no_db` fixture)."""
    monkeypatch.setattr(ds, "_log", lambda *a, **k: None)
    monkeypatch.setattr(ds, "_learn", lambda *a, **k: None)
    monkeypatch.setattr(ds, "_check_stop", lambda *a, **k: None)


# =================================================================================================
# Finding 1: nominalised Hebrew/English decision phrasing is caught, not just finite verbs
# =================================================================================================


class TestSentenceClaimsSettledDecision:
    def test_still_catches_the_original_finite_verbs(self) -> None:
        assert _sentence_claims_settled_decision("הוחלט למנות אותו לתפקיד.")
        assert _sentence_claims_settled_decision("נבחר עמירם נורקין לתפקיד.")
        assert _sentence_claims_settled_decision("זכה בפרויקט המכרז.")
        assert _sentence_claims_settled_decision("החוזה נחתם אתמול.")

    def test_job147_exact_live_phrasing_is_now_caught(self) -> None:
        """job 147's exact live-reproduced sentence -- round 8's own verb list missed it."""
        assert _sentence_claims_settled_decision(
            "בחירת נורקין על פני אבולעפיה נושאת משמעות אסטרטגית משמעותית."
        )

    def test_nominal_phrase_with_attached_suffix_still_matches(self) -> None:
        assert _sentence_claims_settled_decision("בחירתו של המנהל החדש הוכרזה היום.")

    def test_habchira_be_prefix_form_matches(self) -> None:
        assert _sentence_claims_settled_decision("הבחירה בנורקין מעידה על כיוון חדש.")

    def test_hahachlata_al_matches(self) -> None:
        assert _sentence_claims_settled_decision("ההחלטה על מינויו התקבלה בישיבת הדירקטוריון.")

    def test_haminuy_shel_matches(self) -> None:
        assert _sentence_claims_settled_decision("המינוי של רונן כהן ייכנס לתוקף בחודש הבא.")

    def test_hazechiya_shel_matches(self) -> None:
        assert _sentence_claims_settled_decision("הזכייה של החברה במכרז אושרה רשמית.")

    def test_hachatima_al_matches(self) -> None:
        assert _sentence_claims_settled_decision("החתימה על ההסכם בוצעה בשבוע שעבר.")

    def test_english_equivalents_match_case_insensitively(self) -> None:
        assert _sentence_claims_settled_decision("The selection of Norkin over Abulafia is final.")
        assert _sentence_claims_settled_decision("THE APPOINTMENT OF the new director was confirmed.")
        assert _sentence_claims_settled_decision("The board reached the decision to proceed.")

    def test_unrelated_sentence_does_not_match(self) -> None:
        assert not _sentence_claims_settled_decision("החברה פרסמה עדכון כללי על התוכנית.")
        assert not _sentence_claims_settled_decision("The company issued a general program update.")


class TestDowngradeCatchesNominalPhrasing:
    def _inv_with_result(self, answer_he: str, outcome: str = "found") -> Investigation:
        inv = Investigation(job_id=147, item_id=81, question="q")
        inv.read_urls = ["https://globes.example.com"]
        inv.result = InvestigationOut(
            outcome=outcome, answer_he=answer_he, confidence=0.9, sources=inv.read_urls
        )
        return inv

    def test_job147_live_rerun_repro_end_to_end(self) -> None:
        """Reproduces job 147's actual live rerun answer (fetched from the jobs table during this
        round's investigation): the headline sentence never uses a finite decision verb at all."""
        answer = (
            "בחירת נורקין על פני אבולעפיה נושאת משמעות אסטרטגית משמעותית, שכן היא מעידה על "
            "התמקדות של אנדוריל במערכות של חיל האוויר. פרטים נוספים יפורסמו בהמשך."
        )
        inv = self._inv_with_result(answer)
        inv.hedged_read_urls = ["https://globes.example.com"]
        _downgrade_unhedged_decision_claims(inv)
        assert "בחירת נורקין" not in inv.result.answer_he
        assert "טרם אושרה סופית" in inv.result.answer_he
        assert "פרטים נוספים יפורסמו בהמשך" in inv.result.answer_he
        assert "בחירת נורקין" in inv.result.contradictions_he

    def test_english_nominal_phrase_is_also_downgraded(self) -> None:
        inv = self._inv_with_result(
            "The selection of Norkin over Abulafia was confirmed. Background information follows."
        )
        inv.hedged_read_urls = ["https://globes.example.com"]
        _downgrade_unhedged_decision_claims(inv)
        assert "The selection of Norkin" not in inv.result.answer_he
        assert "Background information follows" in inv.result.answer_he


# =================================================================================================
# Finding 2: a low-quality-discarded page can never end up in `sources`/citations
# =================================================================================================


class TestLowQualityCitationInvariant:
    def test_tool_read_records_the_discarded_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inv = Investigation(job_id=146, item_id=44, question="q")
        inv.hits_seen = {"https://cf.example.com": _hit("https://cf.example.com")}
        budget = _budget()
        monkeypatch.setattr(
            "eoa.fetch.remote.fetch_remote",
            lambda url: {
                "text": "Just a moment... Enable JavaScript and cookies to continue" + " x" * 200,
                "title": "Just a moment...",
            },
        )
        _tool_read(inv, budget, "https://cf.example.com", round_no=1)
        assert inv.low_quality_read_urls == ["https://cf.example.com"]
        assert inv.read_urls == []

    def test_finalize_outcome_filters_a_low_quality_url_out_of_sources_even_if_read_urls_leaked_it(
        self,
    ) -> None:
        """Defence-in-depth: even if a future change to this module somehow let a discarded URL
        into `read_urls` (today's only append site is gated on `_low_quality_page_reason`
        returning `None`, but this asserts the invariant holds regardless of that gate), the
        `_finalize_outcome` filter against `low_quality_read_urls` must still keep it out of the
        final `sources` list."""
        inv = Investigation(job_id=1, item_id=1, question="q")
        # simulate the leak the filter is meant to guard against
        inv.read_urls = ["https://good.example.com", "https://cf.example.com"]
        inv.low_quality_read_urls = ["https://cf.example.com"]
        inv.result = InvestigationOut(
            outcome="found",
            answer_he="תשובה עם מקור אחד תקין ואחד שנפסל.",
            confidence=0.9,
            sources=["https://good.example.com", "https://cf.example.com"],
        )
        budget = _budget()
        _finalize_outcome(inv, budget)
        assert inv.result.sources == ["https://good.example.com"]
        assert "https://cf.example.com" not in inv.result.sources

    def test_finalize_outcome_normal_path_unaffected_when_nothing_was_discarded(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.read_urls = ["https://a.example.com", "https://b.example.com"]
        inv.result = InvestigationOut(
            outcome="found", answer_he="תשובה תקינה.", confidence=0.9, sources=inv.read_urls
        )
        budget = _budget()
        _finalize_outcome(inv, budget)
        assert inv.result.sources == ["https://a.example.com", "https://b.example.com"]


# =================================================================================================
# Finding 3: reconcile_deep_search_reruns folds an item's own dedup_of chain into one group
# =================================================================================================


def _entry(
    job_id: int,
    item_id: int | None,
    question: str,
    outcome: str,
    *,
    rerun_of_job_id: int | None = None,
    expanded_from_job_id: int | None = None,
    trigger_item_dedup_of: int | None = None,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "trigger_item_id": item_id,
        "question": question,
        "outcome": outcome,
        "answer_he": f"answer {job_id}",
        "rerun_of_job_id": rerun_of_job_id,
        "expanded_from_job_id": expanded_from_job_id,
        "trigger_item_dedup_of": trigger_item_dedup_of,
    }


class TestReconcileDedupOfGrouping:
    def test_item_96_dedup_of_10_folds_into_item_10s_group(self) -> None:
        """The exact live finding: item 96 (dedup_of=10) has its own stale not_found (job 45)
        while item 10 has a fixed found answer (job 156) -- differently worded questions, no
        rerun/expansion lineage between them, only the dedup_of link connects them."""
        rows = [
            _entry(156, 10, "מה ידוע על מערכת הלייזר של AeroVironment?", "found"),
            _entry(45, 96, "תעדכן אותי לגבי עסקת הלייזר האחרונה", "not_found", trigger_item_dedup_of=10),
        ]
        out = reconcile_deep_search_reruns(rows)
        assert len(out) == 1
        assert out[0]["job_id"] == 156
        assert out[0]["rerun_count"] == 2

    def test_grouping_works_regardless_of_which_side_dedup_of_is_declared_on(self) -> None:
        """Symmetry: only the duplicate item ever carries `dedup_of` in the real schema, but the
        union itself must not depend on which entry in the list is the duplicate."""
        rows = [
            _entry(45, 96, "question about the duplicate item", "not_found", trigger_item_dedup_of=10),
            _entry(156, 10, "question about the canonical item", "found"),
        ]
        out = reconcile_deep_search_reruns(rows)
        assert len(out) == 1
        assert out[0]["job_id"] == 156

    def test_dedup_chain_of_three_items_merges_transitively(self) -> None:
        """A dedup_of=B, B dedup_of=C: all three investigations must end up in one group even
        though no entry directly points at the root C."""
        rows = [
            _entry(3, 30, "q30", "found"),  # C: the root, no dedup_of
            _entry(2, 20, "q20", "not_found", trigger_item_dedup_of=30),  # B dedup_of C
            _entry(1, 10, "q10", "not_found", trigger_item_dedup_of=20),  # A dedup_of B
        ]
        out = reconcile_deep_search_reruns(rows)
        assert len(out) == 1
        assert out[0]["job_id"] == 3
        assert out[0]["rerun_count"] == 3

    def test_dedup_of_target_with_no_entry_in_this_period_is_harmless(self) -> None:
        """`dedup_of` pointing at an item that has no deep-search entry of its own in this
        period must not crash or merge anything spuriously."""
        rows = [_entry(45, 96, "q", "not_found", trigger_item_dedup_of=10)]
        out = reconcile_deep_search_reruns(rows)
        assert len(out) == 1 and out[0]["job_id"] == 45 and "rerun_count" not in out[0]

    def test_unrelated_items_with_no_dedup_link_stay_separate(self) -> None:
        rows = [
            _entry(1, 10, "q1", "found"),
            _entry(2, 20, "q2", "not_found"),
        ]
        out = reconcile_deep_search_reruns(rows)
        assert len(out) == 2

    def test_dedup_grouping_composes_with_question_text_and_lineage_grouping(self) -> None:
        """All three passes (question text, lineage, dedup_of) can contribute to the same merged
        group without interfering with each other."""
        rows = [
            _entry(156, 10, "canonical question", "found"),
            _entry(150, 10, "canonical question", "not_found"),  # Pass 1: same question as 156
            _entry(45, 96, "duplicate item question", "not_found", trigger_item_dedup_of=10),  # Pass 3
            _entry(46, 96, "reworded rerun", "blocked", rerun_of_job_id=45),  # Pass 2, off job 45
        ]
        out = reconcile_deep_search_reruns(rows)
        assert len(out) == 1
        assert out[0]["job_id"] == 156  # found beats not_found/blocked
        assert out[0]["rerun_count"] == 4


class TestCollectDeepSearchDedupExtraction:
    class _FakeCursor:
        def __init__(self, rows: list[dict]):
            self._rows = rows

        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return list(self._rows)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _FakeConn:
        def __init__(self, cursor) -> None:
            self._cursor = cursor

        def cursor(self, row_factory=None):
            return self._cursor

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def test_trigger_item_dedup_of_extracted_from_the_joined_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [
            {
                "job_id": 45,
                "payload": {"item_id": 96, "question": "q"},
                "result": {"outcome": "not_found"},
                "state": "done",
                "finished_at": None,
                "trigger_item_id": 96,
                "trigger_title": "t",
                "trigger_url": "u",
                "trigger_item_dedup_of": 10,
            }
        ]
        monkeypatch.setattr(daily, "connection", lambda: self._FakeConn(self._FakeCursor(rows)))
        out = collect_deep_search()
        assert out[0]["trigger_item_dedup_of"] == 10

    def test_missing_dedup_of_defaults_to_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {
                "job_id": 156,
                "payload": {"item_id": 10, "question": "q"},
                "result": {"outcome": "found"},
                "state": "done",
                "finished_at": None,
                "trigger_item_id": 10,
                "trigger_title": "t",
                "trigger_url": "u",
                "trigger_item_dedup_of": None,
            }
        ]
        monkeypatch.setattr(daily, "connection", lambda: self._FakeConn(self._FakeCursor(rows)))
        out = collect_deep_search()
        assert out[0]["trigger_item_dedup_of"] is None
