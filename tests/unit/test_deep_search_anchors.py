"""Tests for the question-anchoring + finish-time relevance gate added 2026-09-06 (job 86
regression): job 86's question was "US Air Force speeds Reaper successor timeline after Iran
losses", but its round-2 queries drifted to generic EO/IR terms ("מערכות כטב\"ם עם חיישני אופטיקה
ו-IR", "MOSP 5000 system specifications Elbit Systems") with zero connection to the question, and
it `finish`'d with outcome="found"/confidence=0.9 on an answer entirely about Elbit's MOSP 5000 --
a system never mentioned in the question at all.

Two independent guards are tested here:
  1. `extract_anchors` / `_query_anchor_ok` / `_tool_search`: every `search` call must be grounded
     in a deterministic anchor from the question/title/entities (or a declared `anchor_used`).
  2. `_relevance_gate` wired into `_act`'s `finish` handling: a `found`/`partial` finish must pass
     a deterministic anchor-mention check AND an LLM judge (mocked here), or it is rejected once
     and then force-capped to `not_found`/low confidence.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_deep_search_anchors.py -q``
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

from eoa.llm.schemas.analysis import RelevanceVerdict
from eoa.search.deep_search import (
    NOT_FOUND_MAX_CONFIDENCE,
    Budget,
    Investigation,
    _act,
    _answer_mentions_anchor,
    _is_israel_focused_query,
    _query_anchor_ok,
    extract_anchors,
)

JOB86_QUESTION = (
    'אמת והרחב את הדיווח "US Air Force speeds Reaper successor timeline after Iran losses": '
    "מי הצדדים, הלקוח, היקף/סכום, לוח זמנים ומתחרים, ומה המשמעות למוצרי EO/IR ולתעשייה הישראלית."
)
JOB86_CONTEXT_HE = (
    "כותרת הפריט: US Air Force speeds Reaper successor timeline after Iran losses\n"
    "ישויות: —\n"
    "תקציר: להערכתנו, המהלך משקף את הצורך הדחוף של חיל האוויר האמריקאי במטוסים זולים יותר."
)

# The actual round-2 queries recorded in investigation_log for job 86 (id=86) -- every one of them
# should be rejected by the anchor gate, since none mentions Reaper/Iran/USAF/the article title.
JOB86_BAD_QUERIES = [
    ("he", 'מערכות כטב"ם עם חיישני אופטיקה ו-IR'),
    ("he", 'מערכות לזיהוי אוטומטי של מטרות (ATR)'),
    ("he", "מערכות הגנה אווירית עם חיישני לייזר"),
    ("en", "MOSP 5000 system specifications Elbit Systems"),
]


def _budget(**overrides) -> Budget:
    base = dict(max_queries=15, max_pages=30, deadline=time.monotonic() + 3600, confidence_stop=0.8)
    base.update(overrides)
    return Budget(**base)


class TestExtractAnchorsEnglish:
    def test_extracts_proper_nouns_from_quoted_title(self) -> None:
        anchors = extract_anchors(JOB86_QUESTION, context_he=JOB86_CONTEXT_HE)
        assert "Reaper" in anchors
        assert "Iran" in anchors
        assert any("US Air Force speeds Reaper successor" in a for a in anchors)

    def test_extracts_acronyms(self) -> None:
        anchors = extract_anchors("What is the EO/IR sensor spec for the ATR module?")
        assert "ATR" in anchors

    def test_excludes_generic_english_stopwords(self) -> None:
        anchors = extract_anchors(JOB86_QUESTION, context_he=JOB86_CONTEXT_HE)
        assert "successor" not in [a.casefold() for a in anchors]
        assert "timeline" not in [a.casefold() for a in anchors]
        assert "losses" not in [a.casefold() for a in anchors]

    def test_entities_are_anchors(self) -> None:
        anchors = extract_anchors("מה קרה?", entities=["Rheinmetall", "GDLS"])
        assert "Rheinmetall" in anchors
        assert "GDLS" in anchors


class TestExtractAnchorsHebrew:
    def test_extracts_hebrew_content_words(self) -> None:
        anchors = extract_anchors('מה עלה בגורל התוכנית "כיפת ברזל" בעקבות ההסלמה?')
        assert any("כיפת" in a or "ברזל" in a for a in anchors)

    def test_excludes_hebrew_function_words(self) -> None:
        anchors = extract_anchors(JOB86_QUESTION, context_he=JOB86_CONTEXT_HE)
        al = [a.casefold() for a in anchors]
        assert "את" not in al
        assert "של" not in al
        assert "מה" not in al

    def test_context_title_line_becomes_whole_phrase_anchor(self) -> None:
        anchors = extract_anchors("שאלה קצרה", context_he=JOB86_CONTEXT_HE)
        assert any(a == "US Air Force speeds Reaper successor timeline after Iran losses" for a in anchors)

    def test_context_entities_line_parsed_per_company(self) -> None:
        ctx = "כותרת הפריט: משהו\nישויות: Elbit Systems, IAI\nתקציר: x"
        anchors = extract_anchors("שאלה", context_he=ctx)
        assert "Elbit Systems" in anchors
        assert "IAI" in anchors

    def test_no_anchors_when_question_and_context_are_empty(self) -> None:
        assert extract_anchors("", context_he="") == []


class TestAnswerMentionsAnchor:
    def test_matches_case_insensitively(self) -> None:
        assert _answer_mentions_anchor("הכתבה עוסקת ב-reaper ותוכניתו", ["Reaper"]) == "Reaper"

    def test_no_match_returns_none(self) -> None:
        assert _answer_mentions_anchor("תשובה על MOSP 5000 בלבד", ["Reaper", "Iran"]) is None


class TestIsraelFocusedQuery:
    def test_detects_hebrew_and_english_markers(self) -> None:
        assert _is_israel_focused_query("מה המשמעות לתעשייה הישראלית ולאלביט?")
        assert _is_israel_focused_query("Israeli defense industry implications")

    def test_ordinary_query_not_flagged(self) -> None:
        assert not _is_israel_focused_query("Reaper successor timeline Iran Air Force")


class TestQueryAnchorGate:
    """Reproduces job 86 directly: the historical bad queries must be rejected, and queries that
    actually reformulate the question must pass."""

    def _inv(self) -> Investigation:
        inv = Investigation(job_id=86, item_id=1352, question=JOB86_QUESTION)
        inv.anchors = extract_anchors(JOB86_QUESTION, context_he=JOB86_CONTEXT_HE)
        return inv

    def test_all_job86_bad_queries_rejected(self) -> None:
        inv = self._inv()
        for _lang, query in JOB86_BAD_QUERIES:
            ok, matched = _query_anchor_ok(inv, query, None)
            assert ok is False, f"expected rejection for unanchored query: {query!r}"
            assert matched is None

    def test_anchored_query_accepted(self) -> None:
        inv = self._inv()
        ok, matched = _query_anchor_ok(inv, "Reaper successor timeline Iran Air Force", None)
        assert ok is True
        assert matched

    def test_declared_anchor_used_accepted_even_without_substring_match(self) -> None:
        """A translation/synonym of an anchor is allowed via the declared `anchor_used` field --
        not independently verified, but not silently dropped either."""
        inv = self._inv()
        ok, matched = _query_anchor_ok(inv, "מל\"ט הרג'קים בין ארה\"ב לאיראן", "Reaper (בעברית: הרג'קים)")
        assert ok is True
        assert matched

    def test_no_anchors_extracted_allows_everything(self) -> None:
        """A free-typed investigation with no title/entities to anchor to must not be blocked
        outright -- nothing to enforce is not the same as everything being invalid."""
        inv = Investigation(job_id=None, item_id=None, question="?")
        assert inv.anchors == []
        ok, _ = _query_anchor_ok(inv, "anything at all", None)
        assert ok is True

    def test_israel_query_exempt_only_after_a_relevant_read(self) -> None:
        inv = self._inv()
        israel_query = "מה המשמעות לתעשייה הישראלית ולאלביט מהמעבר הזה?"
        ok_before, _ = _query_anchor_ok(inv, israel_query, None)
        assert ok_before is False  # not anchored, and no read yet -- not exempt
        inv.read_urls.append("https://example.com/reaper-article")
        # `_tool_search`'s own exemption logic checks `inv.read_urls` in addition to
        # `_query_anchor_ok`, so re-verify through the combined behaviour used at the call site.
        from eoa.search.deep_search import _is_israel_focused_query

        assert _is_israel_focused_query(israel_query) and inv.read_urls


class TestToolSearchRejection:
    """End-to-end through `_tool_search` itself: rejection must not hit the network (`search()`),
    must log the rejection, and must not consume more than one budget unit per round."""

    def _inv(self) -> Investigation:
        inv = Investigation(job_id=86, item_id=1352, question=JOB86_QUESTION)
        inv.anchors = extract_anchors(JOB86_QUESTION, context_he=JOB86_CONTEXT_HE)
        return inv

    def test_unanchored_query_rejected_without_calling_search(self, monkeypatch) -> None:
        inv = self._inv()
        budget = _budget()
        called = {"n": 0}

        def fake_search(*a, **kw):
            called["n"] += 1
            raise AssertionError("search() must not be called for a rejected query")

        monkeypatch.setattr("eoa.search.deep_search.search", fake_search)
        out = _tool_search_wrapper(inv, budget, 'מערכות כטב"ם עם חיישני אופטיקה ו-IR', "he", 1)
        assert called["n"] == 0
        assert "אינה מעוגנת" in out

    def test_rejection_logged(self, monkeypatch) -> None:
        inv = self._inv()
        budget = _budget()
        logged = []
        monkeypatch.setattr(
            "eoa.search.deep_search._log",
            lambda inv_, round_no, lang, query, **kw: logged.append(kw.get("notes", "")),
        )
        _tool_search_wrapper(inv, budget, "MOSP 5000 system specifications Elbit Systems", "en", 1)
        assert any("rejected" in n for n in logged)

    def test_rejection_charges_budget_at_most_once_per_round(self, monkeypatch) -> None:
        inv = self._inv()
        budget = _budget()
        monkeypatch.setattr("eoa.search.deep_search._log", lambda *a, **kw: None)
        for _ in range(5):
            _tool_search_wrapper(inv, budget, "מערכות הגנה אווירית עם חיישני לייזר", "he", round_no=1)
        assert budget.queries == 1  # charged once for round 1, not 5 times

        _tool_search_wrapper(inv, budget, "מערכות הגנה אווירית עם חיישני לייזר", "he", round_no=2)
        assert budget.queries == 2  # a new round gets its own single charge


def _tool_search_wrapper(inv, budget, query, lang, round_no):
    from eoa.search.deep_search import _tool_search

    return _tool_search(inv, budget, query, lang, round_no)


class TestFinishRelevanceGate:
    """`_act`'s `finish` handling: a found/partial claim must pass `_relevance_gate` (mocked
    `_judge_relevance` here to avoid a live LLM call)."""

    def _finish_call(self, outcome: str = "found", confidence: float = 0.9, answer_he: str = "") -> MagicMock:
        mock_result = MagicMock()
        mock_result.tool_calls = [
            {
                "function": {
                    "name": "finish",
                    "arguments": json.dumps(
                        {
                            "outcome": outcome,
                            "answer_he": answer_he,
                            "confidence": confidence,
                            "sources": ["https://example.com"],
                        }
                    ),
                }
            }
        ]
        mock_result.content = ""
        return mock_result

    def _inv_with_anchors_and_read(self) -> Investigation:
        inv = Investigation(job_id=86, item_id=1352, question=JOB86_QUESTION)
        inv.anchors = extract_anchors(JOB86_QUESTION, context_he=JOB86_CONTEXT_HE)
        inv.read_urls = ["https://example.com"]
        inv.hits_seen = {"https://example.com": MagicMock()}
        return inv

    def test_off_topic_answer_rejected_first_time_and_capped_second_time(self, monkeypatch) -> None:
        """Reproduces job 86's exact symptom: `found`/0.9 confidence on a MOSP 5000 answer to a
        Reaper/Iran question. First finish call must be rejected (one extra round granted);
        repeating the same off-topic answer must be force-capped to not_found/low confidence."""
        inv = self._inv_with_anchors_and_read()
        budget = _budget()
        mosp_answer = (
            "מערכת ה-MOSP 5000 היא מטע\"ד אלקטרו-אופטי רב-חיישני שפותח על ידי מפעל תממ, "
            "הכולל מציין לייזר וטכנולוגיות בינה מלאכותית."
        )

        monkeypatch.setattr(
            "eoa.search.deep_search.chat",
            lambda *a, **kw: self._finish_call(outcome="found", confidence=0.9, answer_he=mosp_answer),
        )
        monkeypatch.setattr(
            "eoa.search.deep_search._judge_relevance",
            lambda question, answer: RelevanceVerdict(verdict="no", reason="עוסק ב-MOSP 5000, לא ברעפר/איראן."),
        )

        transcript: list[dict] = []
        result_first = _act(inv, budget, transcript, round_no=2, max_steps=1)
        assert result_first is False  # rejected -- not allowed to finish yet
        assert inv.result is None
        assert inv.relevance_retry_used is True
        tool_msgs = [m for m in transcript if m.get("tool_name") == "finish"]
        assert any("שופט הרלוונטיות" in m["content"] for m in tool_msgs)

        # second attempt with the SAME off-topic answer: force-accepted, but capped.
        result_second = _act(inv, budget, transcript, round_no=2, max_steps=1)
        assert result_second is True
        assert inv.result is not None
        assert inv.result.outcome == "not_found"
        assert inv.result.confidence <= NOT_FOUND_MAX_CONFIDENCE
        assert inv.result.relevance_check is not None
        assert inv.result.relevance_check["verdict"] == "no"

    def test_on_topic_answer_accepted_without_retry(self, monkeypatch) -> None:
        inv = self._inv_with_anchors_and_read()
        budget = _budget()
        good_answer = "מטוס ה-Reaper יוחלף מוקדם יותר מהמתוכנן, בעקבות אובדנים מול איראן."

        monkeypatch.setattr(
            "eoa.search.deep_search.chat",
            lambda *a, **kw: self._finish_call(outcome="found", confidence=0.85, answer_he=good_answer),
        )
        monkeypatch.setattr(
            "eoa.search.deep_search._judge_relevance",
            lambda question, answer: RelevanceVerdict(verdict="yes", reason="עונה ישירות על השאלה."),
        )

        result = _act(inv, budget, [], round_no=2, max_steps=1)
        assert result is True
        assert inv.result is not None
        assert inv.result.outcome == "found"
        assert inv.result.relevance_check["verdict"] == "yes"

    def test_partial_judge_verdict_downgrades_found_to_partial(self, monkeypatch) -> None:
        inv = self._inv_with_anchors_and_read()
        budget = _budget()
        answer = "יש אזכור חלקי ל-Reaper אך התשובה אינה שלמה."

        monkeypatch.setattr(
            "eoa.search.deep_search.chat",
            lambda *a, **kw: self._finish_call(outcome="found", confidence=0.8, answer_he=answer),
        )
        monkeypatch.setattr(
            "eoa.search.deep_search._judge_relevance",
            lambda question, answer_: RelevanceVerdict(verdict="partial", reason="נוגע רק בחלק מהשאלה."),
        )

        result = _act(inv, budget, [], round_no=2, max_steps=1)
        assert result is True
        assert inv.result.outcome == "partial"

    def test_not_found_claims_skip_the_relevance_gate(self, monkeypatch) -> None:
        """A not_found claim isn't judged for relevance -- there's no answer to judge."""
        inv = self._inv_with_anchors_and_read()
        inv.read_urls = ["https://example.com/a", "https://example.com/b"]
        budget = _budget(queries=5, pages=3)

        judge_called = {"n": 0}

        def fake_judge(*a, **kw):
            judge_called["n"] += 1
            return RelevanceVerdict(verdict="yes", reason="")

        monkeypatch.setattr("eoa.search.deep_search._judge_relevance", fake_judge)
        monkeypatch.setattr(
            "eoa.search.deep_search.chat",
            lambda *a, **kw: self._finish_call(outcome="not_found", confidence=0.0, answer_he="לא נמצא"),
        )

        result = _act(inv, budget, [], round_no=2, max_steps=1)
        assert result is True
        assert inv.result.outcome == "not_found"
        assert judge_called["n"] == 0
        assert inv.result.relevance_check is None
