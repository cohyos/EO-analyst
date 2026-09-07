"""R8-investigations-b (docs/qa/loop/round_7_judge_b.md, D4 = 45): three fixes to the deep-search
report pipeline, all confirmed live against the golden re-run jobs (145-148, reruns of jobs
137-140) before being fixed here.

  1. **Reports don't surface fixed investigations** (`eoa.report.daily.collect_deep_search` /
     `reconcile_deep_search_reruns`): live-verified against the real jobs table that job 148
     (item 1352, partial/0.5, real content, `rerun_of_job_id=140`) and job 86 (item 1352,
     off_topic, stale) were NOT reconciled into one entry -- job 86's payload question is job
     148's question plus an appended " בהקשר: ..." (asker's own context commentary) clause, so the
     old ``reconcile_deep_search_reruns``'s exact-question grouping key put them in different
     groups and both survived into the weekly report. Fixed by (a) normalizing away a trailing
     "בהקשר:" clause before comparing questions, and (b) additionally merging groups via explicit
     ``rerun_of_job_id``/``expanded_from_job_id`` lineage (now also extracted by
     ``collect_deep_search``), independent of question-text matching.
  2. **Zero-page-read -> blank not_found** (`eoa.search.deep_search.investigate` /
     ``_force_read_top_hits``): job 145 (item 10, the exact question round-7's fixes doc showcased
     as fixed) reproduced live: 16 search rows, 56 cumulative hits, 15/15 query budget consumed,
     zero `read` calls, `outcome=not_found`/`confidence=0.0`/`pages_read=0`. Neither
     `MIN_PAGES_BEFORE_NOT_FOUND` (only fires on an explicit `finish(not_found)` call the model
     never made) nor `_synthesize_from_reads` (needs >=1 read summary) catches a loop that never
     attempted a read at all. Fixed with a last-resort safety net that force-reads the best
     remaining hit(s) before falling through to the blank not_found default.
  3. **Citation integrity** (`_low_quality_page_reason` / `_source_text_is_hedged` /
     `_downgrade_unhedged_decision_claims`): job 146 cited a Cloudflare bot-challenge interstitial
     (`title='Just a moment...'`) as its sole source, silently counted as a successful read; job
     147 asserted a personnel decision as settled fact ("בחירת נורקין על פני אבולעפיה",
     confidence 0.9) while its own cited source described an unresolved process ("expected to meet
     next week..."). Fixed with (a) a fetched-page quality gate in `_tool_read` that discards
     challenge/consent/paywall interstitials and near-empty bodies before they can ever be
     summarised or cited, and (b) a deterministic post-check that downgrades a decision-verb claim
     resting on a source that itself still hedges the matter.

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 EOA_SEARCH_NO_CACHE=1 .venv/Scripts/python.exe -m pytest
tests/unit/test_deep_search_round8.py -q``
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

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
    _force_read_top_hits,
    _low_quality_page_reason,
    _source_text_is_hedged,
    _tool_read,
)


def _budget(**overrides) -> Budget:
    base = dict(max_queries=15, max_pages=30, deadline=time.monotonic() + 3600, confidence_stop=0.8)
    base.update(overrides)
    return Budget(**base)


@pytest.fixture(autouse=True)
def _no_db(monkeypatch: pytest.MonkeyPatch):
    """Pure-unit tests: never touch the DB/network/LLM (same discipline as
    test_deep_search_round7.py's own `_no_db` fixture)."""
    monkeypatch.setattr(ds, "_log", lambda *a, **k: None)
    monkeypatch.setattr(ds, "_learn", lambda *a, **k: None)
    monkeypatch.setattr(ds, "_check_stop", lambda *a, **k: None)


# =================================================================================================
# Finding 1a: reconcile_deep_search_reruns groups a question + its appended "בהקשר:" clause
# =================================================================================================


def _entry(
    job_id: int,
    item_id: int | None,
    question: str,
    outcome: str,
    *,
    rerun_of_job_id: int | None = None,
    expanded_from_job_id: int | None = None,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "trigger_item_id": item_id,
        "question": question,
        "outcome": outcome,
        "answer_he": f"answer {job_id}",
        "rerun_of_job_id": rerun_of_job_id,
        "expanded_from_job_id": expanded_from_job_id,
    }


class TestReconcileQuestionNormalization:
    _BASE_Q = (
        'אמת והרחב את הדיווח "US Air Force speeds Reaper successor timeline after Iran losses": '
        "מי הצדדים, הלקוח, היקף/סכום, לוח זמנים ומתחרים, ומה המשמעות למוצרי EO/IR ולתעשייה הישראלית."
    )
    _WITH_CONTEXT_Q = _BASE_Q + " בהקשר: להערכתנו, המהלך משקף את הצורך הדחוף של חיל האוויר האמריקאי."

    def test_job86_and_job148_reconciled_into_one_entry(self) -> None:
        """Live-reproduced regression: job 86 (off_topic, stale) and job 148 (partial, real
        content, newer) both concern item 1352's Reaper-successor question; job 86's question
        carries an appended context clause job 148's doesn't."""
        rows = [
            # job 148 first: newer, matches collect_deep_search's finished_at DESC order
            _entry(148, 1352, self._BASE_Q, "partial"),
            _entry(86, 1352, self._WITH_CONTEXT_Q, "off_topic"),
        ]
        out = reconcile_deep_search_reruns(rows)
        assert len(out) == 1
        assert out[0]["job_id"] == 148
        assert out[0]["rerun_count"] == 2
        assert "off_topic" in out[0]["rerun_note_he"] or "מחוץ לנושא" in out[0]["rerun_note_he"]

    def test_context_clause_alone_does_not_prevent_grouping_when_first(self) -> None:
        """Order shouldn't matter: the context-bearing entry appearing first (newest) still groups
        with the bare-question entry."""
        rows = [
            _entry(200, 5, self._WITH_CONTEXT_Q, "found"),
            _entry(100, 5, self._BASE_Q, "not_found"),
        ]
        out = reconcile_deep_search_reruns(rows)
        assert len(out) == 1 and out[0]["job_id"] == 200

    def test_different_items_with_same_context_suffix_are_not_merged(self) -> None:
        rows = [
            _entry(1, 10, self._BASE_Q, "not_found"),
            _entry(2, 11, self._WITH_CONTEXT_Q, "found"),
        ]
        out = reconcile_deep_search_reruns(rows)
        assert len(out) == 2

    def test_normalize_helper_strips_only_the_context_suffix(self) -> None:
        normalized_bare = daily._normalize_question_for_grouping(self._BASE_Q)
        normalized_with_context = daily._normalize_question_for_grouping(self._WITH_CONTEXT_Q)
        assert normalized_bare == normalized_with_context
        assert "בהקשר" not in normalized_with_context

    def test_question_with_no_context_clause_normalizes_unchanged_apart_from_casefold(self) -> None:
        q = "  What   is the unit price?  "
        assert daily._normalize_question_for_grouping(q) == "what is the unit price?"


# =================================================================================================
# Finding 1b: reconcile_deep_search_reruns merges via explicit rerun/expansion lineage
# =================================================================================================


class TestReconcileLineageMerge:
    def test_rerun_of_job_id_merges_entries_with_unrelated_question_text(self) -> None:
        rows = [
            _entry(
                145,
                10,
                "Verify and expand: completely reworded rerun question",
                "not_found",
                rerun_of_job_id=137,
            ),
            _entry(137, 10, "Original, differently phrased question", "not_found"),
        ]
        out = reconcile_deep_search_reruns(rows)
        assert len(out) == 1
        assert out[0]["job_id"] == 145
        assert out[0]["rerun_count"] == 2

    def test_expanded_from_job_id_merges_entries_with_unrelated_question_text(self) -> None:
        rows = [
            _entry(90, 20, "expanded question, much longer and reworded", "found", expanded_from_job_id=55),
            _entry(55, 20, "original short question", "not_found"),
        ]
        out = reconcile_deep_search_reruns(rows)
        assert len(out) == 1
        assert out[0]["job_id"] == 90

    def test_lineage_pointer_to_a_job_not_present_is_harmless(self) -> None:
        """`rerun_of_job_id` pointing at a job that isn't in this period's `entries` (e.g. it
        failed and was excluded by `collect_deep_search`'s SQL) must not crash or merge anything
        spuriously."""
        rows = [_entry(145, 10, "q", "not_found", rerun_of_job_id=137)]
        out = reconcile_deep_search_reruns(rows)
        assert len(out) == 1 and out[0]["job_id"] == 145 and "rerun_count" not in out[0]

    def test_lineage_and_question_grouping_compose_transitively(self) -> None:
        """A three-way chain: 130 groups with 46 by identical question; 130 is also (hypothetically)
        pointed at by another entry via rerun_of_job_id -- all three end up in one group."""
        rows = [
            _entry(130, 117, "q117", "not_found"),
            _entry(46, 117, "q117", "not_found"),
            _entry(9, 117, "unrelated text entirely", "found", rerun_of_job_id=130),
        ]
        out = reconcile_deep_search_reruns(rows)
        assert len(out) == 1
        assert out[0]["job_id"] == 9  # found beats not_found
        assert out[0]["rerun_count"] == 3


# =================================================================================================
# Finding 1c: collect_deep_search extracts the lineage fields from payload
# =================================================================================================


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
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestCollectDeepSearchLineageExtraction:
    def test_rerun_of_job_id_and_expanded_from_job_id_both_extracted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [
            {
                "job_id": 148,
                "payload": {"item_id": 1352, "question": "q", "rerun_of_job_id": 140},
                "result": {"outcome": "partial"},
                "state": "done",
                "finished_at": None,
                "trigger_item_id": 1352,
                "trigger_title": "t",
                "trigger_url": "u",
            },
            {
                "job_id": 90,
                "payload": {"item_id": 20, "question": "q2", "expanded_from_job_id": 55},
                "result": {"outcome": "found"},
                "state": "done",
                "finished_at": None,
                "trigger_item_id": 20,
                "trigger_title": "t2",
                "trigger_url": "u2",
            },
        ]
        monkeypatch.setattr(daily, "connection", lambda: _FakeConn(_FakeCursor(rows)))
        out = collect_deep_search()
        by_job = {e["job_id"]: e for e in out}
        assert by_job[148]["rerun_of_job_id"] == 140
        assert by_job[148]["expanded_from_job_id"] is None
        assert by_job[90]["expanded_from_job_id"] == 55

    def test_missing_lineage_keys_default_to_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {
                "job_id": 1,
                "payload": {"item_id": 1, "question": "q"},
                "result": {"outcome": "not_found"},
                "state": "done",
                "finished_at": None,
                "trigger_item_id": 1,
                "trigger_title": "t",
                "trigger_url": "u",
            }
        ]
        monkeypatch.setattr(daily, "connection", lambda: _FakeConn(_FakeCursor(rows)))
        out = collect_deep_search()
        assert out[0]["rerun_of_job_id"] is None
        assert out[0]["expanded_from_job_id"] is None


# =================================================================================================
# Finding 2: an investigation never ends with zero reads while hits exist (_force_read_top_hits)
# =================================================================================================


def _hit(url: str, score: float = 0.0) -> SearchHit:
    return SearchHit(url=url, title="Title", snippet="snippet", engine="ddgs", score=score)


class TestForceReadTopHits:
    def test_noop_when_reads_already_exist(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.read_summaries = [{"url": "https://x", "title": "t", "summary": "s"}]
        inv.hits_seen = {"https://y": _hit("https://y")}
        budget = _budget()
        _force_read_top_hits(inv, budget)
        assert budget.pages == 0  # never touched -- already satisfied

    def test_noop_when_no_hits(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        budget = _budget()
        _force_read_top_hits(inv, budget)
        assert inv.read_summaries == []
        assert budget.pages == 0

    def test_reads_the_best_scoring_hit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inv = Investigation(job_id=145, item_id=10, question="q")
        inv.hits_seen = {
            "https://low.example.com": _hit("https://low.example.com", score=0.2),
            "https://best.example.com": _hit("https://best.example.com", score=0.9),
            "https://mid.example.com": _hit("https://mid.example.com", score=0.5),
        }
        budget = _budget()
        attempted: list[str] = []

        def fake_fetch(url):
            attempted.append(url)
            return {"text": "x" * 500, "title": "Real article"}

        monkeypatch.setattr("eoa.fetch.remote.fetch_remote", fake_fetch)
        monkeypatch.setattr(
            "eoa.security.guard.screen", lambda *a, **kw: MagicMock(verdict="clean", kind=None, excerpt="")
        )
        monkeypatch.setattr(ds, "_summarise_page", lambda inv_, text, url: "summary")

        _force_read_top_hits(inv, budget)

        assert attempted == ["https://best.example.com"]
        assert inv.read_urls == ["https://best.example.com"]
        assert budget.pages == 1

    def test_falls_back_to_next_hit_when_top_hit_is_quarantined(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inv = Investigation(job_id=145, item_id=10, question="q")
        inv.hits_seen = {
            "https://bad.example.com": _hit("https://bad.example.com", score=0.9),
            "https://good.example.com": _hit("https://good.example.com", score=0.5),
        }
        budget = _budget()

        def fake_fetch(url):
            return {"text": "x" * 500, "title": "t"}

        # the top-scored hit (read first) is quarantined; the next one is clean
        calls = {"n": 0}

        def fake_screen(text, title, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return MagicMock(verdict="quarantined", kind="x", excerpt="")
            return MagicMock(verdict="clean", kind=None, excerpt="")

        monkeypatch.setattr("eoa.fetch.remote.fetch_remote", fake_fetch)
        monkeypatch.setattr("eoa.security.guard.screen", fake_screen)
        monkeypatch.setattr(ds, "_summarise_page", lambda inv_, text, url: "summary")

        _force_read_top_hits(inv, budget)

        assert inv.read_urls == ["https://good.example.com"]
        assert budget.pages == 2  # both attempts charged

    def test_stops_after_max_attempts_when_every_hit_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inv = Investigation(job_id=145, item_id=10, question="q")
        inv.hits_seen = {
            f"https://h{i}.example.com": _hit(f"https://h{i}.example.com", score=1.0 - i * 0.1)
            for i in range(6)
        }
        budget = _budget()
        monkeypatch.setattr("eoa.fetch.remote.fetch_remote", lambda url: {"text": "x" * 500, "title": "t"})
        monkeypatch.setattr(
            "eoa.security.guard.screen",
            lambda *a, **kw: MagicMock(verdict="quarantined", kind="x", excerpt=""),
        )
        _force_read_top_hits(inv, budget, max_attempts=3)
        assert inv.read_summaries == []
        assert budget.pages == 3

    def test_respects_remaining_page_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.hits_seen = {"https://x.example.com": _hit("https://x.example.com", score=1.0)}
        budget = _budget(max_pages=1)
        budget.pages = 1  # already exhausted
        called = {"n": 0}
        monkeypatch.setattr(
            "eoa.fetch.remote.fetch_remote", lambda url: called.update(n=called["n"] + 1) or {}
        )
        _force_read_top_hits(inv, budget)
        assert called["n"] == 0

    def test_investigate_calls_force_read_when_round_loop_ends_with_zero_reads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Integration-style: `investigate()` itself reaches the safety net when `_act` never
        reads anything, mirroring job 145's exact symptom."""

        def fake_tool_search(inv, budget, query, lang, round_no, anchor_used=None):
            inv.hits_seen.setdefault(
                "https://found.example.com", _hit("https://found.example.com", score=0.8)
            )
            budget.queries += 1
            return "seeded"

        def fake_act(inv, budget, transcript, round_no, max_steps=12, tools=None):
            # the model calls `search` (already seeded above) but never `read` or `finish`
            budget.queries += 1
            return False  # never finished

        monkeypatch.setattr(ds, "plan_queries", lambda *a, **kw: [{"query": "q", "lang": "en"}])
        monkeypatch.setattr(ds, "_tool_search", fake_tool_search)
        monkeypatch.setattr(ds, "_act", fake_act)
        monkeypatch.setattr("eoa.fetch.remote.fetch_remote", lambda url: {"text": "x" * 500, "title": "Real"})
        monkeypatch.setattr(
            "eoa.security.guard.screen", lambda *a, **kw: MagicMock(verdict="clean", kind=None, excerpt="")
        )
        monkeypatch.setattr(ds, "_summarise_page", lambda inv_, text, url: "a real summary")
        monkeypatch.setattr(
            ds,
            "chat_structured",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no LLM in unit tests")),
        )

        inv = ds.investigate("Verify and expand: some question", item_id=10, job_id=145, max_rounds=1)

        assert inv.read_urls == ["https://found.example.com"]
        assert inv.result is not None
        # a forced read produced real content -- must not be the blank not_found default
        assert inv.result.answer_he != "לא נמצא מידע מספק במסגרת התקציב."


# =================================================================================================
# Finding 3a: fetched-page quality gate discards challenge/consent/paywall interstitials
# =================================================================================================


class TestLowQualityPageGate:
    def test_cloudflare_challenge_page_is_flagged(self) -> None:
        reason = _low_quality_page_reason(
            "Just a moment...\nEnable JavaScript and cookies to continue" + " x" * 250, "Just a moment..."
        )
        assert reason is not None
        assert "interstitial" in reason

    def test_short_body_is_flagged_even_without_a_known_signature(self) -> None:
        reason = _low_quality_page_reason("a short page with no real content", "Some Title")
        assert reason is not None
        assert "too short" in reason

    def test_realistic_article_passes(self) -> None:
        body = (
            "AeroVironment announced today that it has received a $465 million production contract "
            "from the U.S. Army for its directed-energy laser system. " * 5
        )
        assert _low_quality_page_reason(body, "AeroVironment wins Army laser contract") is None

    def test_access_denied_page_is_flagged(self) -> None:
        body = "Access Denied\nYou don't have permission to access this resource." + " x" * 250
        assert _low_quality_page_reason(body, "403") is not None

    def test_cookie_wall_is_flagged(self) -> None:
        body = "We use cookies to personalize content and ads. " * 20
        assert _low_quality_page_reason(body, "Cookie notice") is not None

    def test_tool_read_discards_low_quality_page_and_never_counts_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """job 146's exact bug: a Cloudflare interstitial must never be summarised/cited."""
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

        def _screen_should_not_be_called(*a, **kw):
            raise AssertionError("security guard must not run on a page already discarded as low-quality")

        monkeypatch.setattr("eoa.security.guard.screen", _screen_should_not_be_called)

        out = _tool_read(inv, budget, "https://cf.example.com", round_no=1)

        assert "discarded" in out
        assert inv.read_urls == []
        assert inv.read_summaries == []
        assert budget.pages == 1  # the fetch attempt still costs budget


# =================================================================================================
# Finding 3b: decision-verb claims resting on a hedged source are downgraded
# =================================================================================================


class TestSourceTextIsHedged:
    def test_hebrew_hedge_markers(self) -> None:
        assert _source_text_is_hedged("שלושת המועמדים צפויים להיפגש בשבוע הבא")
        assert _source_text_is_hedged("החברה שוקלת את האפשרות")
        assert _source_text_is_hedged("ההחלטה טרם התקבלה")

    def test_english_hedge_markers(self) -> None:
        assert _source_text_is_hedged("The board is expected to decide next week")
        assert _source_text_is_hedged("They are considering several candidates")
        assert _source_text_is_hedged("No decision has been made not yet")
        assert _source_text_is_hedged("The company may announce a winner soon")

    def test_no_hedge_markers_returns_false(self) -> None:
        assert not _source_text_is_hedged("The contract was awarded to AeroVironment on March 3.")

    def test_empty_text_returns_false(self) -> None:
        assert not _source_text_is_hedged("")


class TestDowngradeUnhedgedDecisionClaims:
    def _inv_with_result(self, answer_he: str, outcome: str = "found") -> Investigation:
        inv = Investigation(job_id=147, item_id=81, question="q")
        inv.read_urls = ["https://globes.example.com"]
        inv.result = InvestigationOut(
            outcome=outcome, answer_he=answer_he, confidence=0.9, sources=inv.read_urls
        )
        return inv

    def test_downgrades_sentence_with_decision_verb_when_source_is_hedged(self) -> None:
        inv = self._inv_with_result("החברה הודיעה כי נבחר עמירם נורקין לתפקיד. פרטים נוספים יפורסמו בהמשך.")
        inv.hedged_read_urls = ["https://globes.example.com"]
        _downgrade_unhedged_decision_claims(inv)
        assert "נבחר" not in inv.result.answer_he
        assert "טרם אושרה סופית" in inv.result.answer_he
        assert "פרטים נוספים יפורסמו בהמשך" in inv.result.answer_he  # unrelated sentence kept
        assert "נבחר עמירם נורקין" in inv.result.contradictions_he  # original claim recorded

    def test_noop_when_no_source_flagged_as_hedged(self) -> None:
        inv = self._inv_with_result("החברה הודיעה כי נבחר עמירם נורקין לתפקיד.")
        original = inv.result.answer_he
        _downgrade_unhedged_decision_claims(inv)
        assert inv.result.answer_he == original
        assert inv.result.contradictions_he == ""

    def test_noop_when_flagged_source_was_not_actually_cited(self) -> None:
        inv = self._inv_with_result("החברה הודיעה כי נבחר עמירם נורקין לתפקיד.")
        inv.hedged_read_urls = ["https://unrelated.example.com"]  # not in inv.read_urls
        original = inv.result.answer_he
        _downgrade_unhedged_decision_claims(inv)
        assert inv.result.answer_he == original

    def test_noop_when_no_decision_verb_present(self) -> None:
        inv = self._inv_with_result("החברה פרסמה עדכון כללי על התוכנית.")
        inv.hedged_read_urls = ["https://globes.example.com"]
        original = inv.result.answer_he
        _downgrade_unhedged_decision_claims(inv)
        assert inv.result.answer_he == original

    def test_noop_when_outcome_is_not_found(self) -> None:
        inv = self._inv_with_result("נבחר עמירם נורקין לתפקיד.", outcome="not_found")
        inv.hedged_read_urls = ["https://globes.example.com"]
        original = inv.result.answer_he
        _downgrade_unhedged_decision_claims(inv)
        assert inv.result.answer_he == original

    def test_noop_when_result_is_none(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.hedged_read_urls = ["https://x"]
        _downgrade_unhedged_decision_claims(inv)  # must not raise
        assert inv.result is None

    def test_multiple_flagged_sentences_collapse_into_one_placeholder(self) -> None:
        inv = self._inv_with_result("נבחר נורקין לתפקיד. בנוסף, זכה בפרס נוסף על כך. זהו מידע רקע כללי.")
        inv.hedged_read_urls = ["https://globes.example.com"]
        _downgrade_unhedged_decision_claims(inv)
        placeholder_count = inv.result.answer_he.count("טרם אושרה סופית")
        assert placeholder_count == 1
        assert "זהו מידע רקע כללי" in inv.result.answer_he

    def test_job147_repro_end_to_end(self) -> None:
        """Reproduces job 147's actual reported bug: a confident settled-fact claim about which
        candidate was chosen, resting on a source that itself said the process was still open."""
        answer = (
            "בהתאם לדיווח, הוחלט למנות את עמירם נורקין לראש הפעילות בישראל, ולא את אבולעפיה. "
            "המינוי נעשה במסגרת הרחבת פעילות החברה באזור."
        )
        inv = self._inv_with_result(answer)
        inv.hedged_read_urls = ["https://globes.example.com"]
        _downgrade_unhedged_decision_claims(inv)
        assert "הוחלט" not in inv.result.answer_he
        assert "המינוי נעשה במסגרת הרחבת פעילות החברה באזור" in inv.result.answer_he
        assert inv.result.contradictions_he
