"""R7-investigations (Round 7 of the QA loop, docs/qa/loop/round_7_fixes.md): fixes for the six
golden deep-search investigations (jobs 91, 86, 70, 48, 47, 46) that reported not_found/off_topic
with confidence 0.0-0.3, unchanged since round 3.

Root causes diagnosed from `investigation_log` + the jobs' own payloads (see the status doc for
the full table) and fixed here, each with its own test class:

  - Job 46: triage occasionally enqueues a deep-search job whose "question" is leftover
    meta-commentary ("no further search needed"), not a real question -- `_is_degenerate_question`
    / the `investigate()` short-circuit around it.
  - Job 91: 10 pages were read (including on-topic ones) across 4 rounds, but the ReAct loop never
    reached a `finish()` call before exhausting its round budget -- `_synthesize_from_reads`
    salvages an answer from what was actually read instead of discarding it, and `_act`'s
    `max_steps` is raised so this happens less in the first place.
  - Jobs 47/48/70: both job runners in `eoa.orchestrator.jobs` forward only
    `job.payload["context_he"]` verbatim into `investigate()`, with no fallback to the item's own
    already-extracted title/entities when that key is blank -- confirmed against the `items` rows
    themselves (item 10/job 47 already had `entities_mentioned = ['AeroVironment', 'US Army']`;
    item 81/job 70 already named Anduril). Out of this round's file ownership to fix at the
    caller; `_fallback_item_context` has `investigate()` look the item up itself when it has an
    `item_id` and was given no context at all.
  - Job 70: two of six page-read attempts died outright on a transient DNS blip
    ("Temporary failure in name resolution") with no retry -- `_fetch_with_retry`/
    `_is_transient_fetch_error`.
  - Cross-cutting: `_tool_search`'s `investigation_log` entries hardcoded `engine="searxng"`
    regardless of which backend (ddgs/searxng) actually served the query, and quarantined pages'
    URLs were never logged at all -- both fixed as diagnostic-accuracy issues this round's
    diagnosis itself needed and didn't have.
  - `plan_queries`'s LLM-failure fallback dumped the raw (often Hebrew) question verbatim as an
    "en" query too, instead of the already-extracted anchors.

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 EOA_SEARCH_NO_CACHE=1 .venv/Scripts/python.exe -m pytest
tests/unit/test_deep_search_round7.py -q``
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from eoa.errors import LLMOutputError
from eoa.llm.schemas.analysis import FallbackSynthesisOut, InvestigationOut, RelevanceVerdict
from eoa.search import deep_search as ds
from eoa.search.deep_search import (
    Budget,
    Investigation,
    _fetch_with_retry,
    _is_degenerate_question,
    _is_transient_fetch_error,
    _synthesize_from_reads,
    _tool_read,
    _tool_search,
    plan_queries,
)


def _budget(**overrides) -> Budget:
    base = dict(max_queries=15, max_pages=30, deadline=time.monotonic() + 3600, confidence_stop=0.8)
    base.update(overrides)
    return Budget(**base)


@pytest.fixture(autouse=True)
def _no_db(monkeypatch: pytest.MonkeyPatch):
    """These are pure-unit tests: never touch the DB/network/LLM directly. `_check_stop` (the
    per-round "did an operator hit stop?" poll) opens a real DB connection whenever `inv.job_id`
    is not `None` -- neutralized here the same way `_log`/`_learn` are, so a full `investigate()`
    call in these tests never blocks on a database that isn't there."""
    monkeypatch.setattr(ds, "_log", lambda *a, **k: None)
    monkeypatch.setattr(ds, "_learn", lambda *a, **k: None)
    monkeypatch.setattr(ds, "_check_stop", lambda *a, **k: None)


# =================================================================================================
# Job 46: degenerate/non-question guard
# =================================================================================================


class TestIsDegenerateQuestion:
    JOB46_QUESTION = "אין צורך בחיפוש נוסף, הכתבה מספקת את כל המידע הנדרש."

    def test_job46_exact_question_is_degenerate(self) -> None:
        assert _is_degenerate_question(self.JOB46_QUESTION) is True

    def test_english_equivalent_is_degenerate(self) -> None:
        q = "No further search is needed, the article already provides all the necessary information."
        assert _is_degenerate_question(q) is True

    def test_real_question_quoting_the_phrase_is_not_degenerate(self) -> None:
        """A real question that merely *quotes* the phrase amid substantial content of its own is
        not short-circuited -- only a question that is essentially nothing but the marker
        phrase(s) is."""
        q = 'מדוע הטריאז\' רשם "אין צורך בחיפוש נוסף" לגבי תוכנית ה-MOSP 5000, ומי הספקים המעורבים?'
        assert _is_degenerate_question(q) is False

    def test_ordinary_question_not_flagged(self) -> None:
        assert _is_degenerate_question("What is the unit price of the AARGM-ER missile?") is False

    def test_empty_question_not_flagged(self) -> None:
        assert _is_degenerate_question("") is False


class TestInvestigateDegenerateShortCircuit:
    def test_no_search_or_llm_call_is_made(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*a, **kw):
            raise AssertionError("no search/LLM call should happen for a degenerate question")

        monkeypatch.setattr(ds, "search", _boom)
        monkeypatch.setattr(ds, "chat", _boom)
        monkeypatch.setattr(ds, "chat_structured", _boom)

        inv = ds.investigate("אין צורך בחיפוש נוסף, הכתבה מספקת את כל המידע הנדרש.", job_id=46)

        assert inv.result is not None
        assert inv.result.outcome == "not_found"
        assert inv.result.confidence == 0.0
        assert inv.result.sources == []

    def test_reports_insufficient_context_not_plain_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Zero hits ever seen (nothing was searched) is the honest `insufficient_context`
        classification, same as any other investigation with `hits_seen == {}`."""
        monkeypatch.setattr(ds, "search", lambda *a, **kw: (_ for _ in ()).throw(AssertionError()))
        inv = ds.investigate("אין צורך בחיפוש נוסף, הכתבה מספקת את כל המידע הנדרש.", job_id=46)
        assert inv.outcome == "insufficient_context"

    def test_answer_explains_the_missing_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inv = ds.investigate("אין צורך בחיפוש נוסף, הכתבה מספקת את כל המידע הנדרש.", job_id=46)
        assert "לא בוצעה חקירה" in inv.result.answer_he


# =================================================================================================
# Job 91: salvage an answer from pages that WERE read when `finish()` was never reached
# =================================================================================================


class TestSynthesizeFromReads:
    def _inv_with_reads(self, *, anchors: list[str] | None = None) -> Investigation:
        inv = Investigation(job_id=91, item_id=1352, question="q")
        inv.anchors = anchors or []
        inv.read_urls = ["https://twz.com/reaper-successor", "https://example.com/other"]
        inv.read_summaries = [
            {
                "url": "https://twz.com/reaper-successor",
                "title": "USAF wants MQ-9 Reaper successor",
                "summary": "USAF is accelerating a low-cost Reaper successor after losses over Iran.",
            },
            {
                "url": "https://example.com/other",
                "title": "Other",
                "summary": "Unrelated background on UAV market trends.",
            },
        ]
        return inv

    def test_no_reads_returns_none(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        assert _synthesize_from_reads(inv) is None

    def test_builds_partial_answer_from_summaries_without_anchors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inv = self._inv_with_reads(anchors=[])
        monkeypatch.setattr(
            ds,
            "chat_structured",
            lambda *a, **kw: FallbackSynthesisOut(
                answer_he="ה-USAF מזרז מחליף זול ל-Reaper בעקבות אובדנים מול איראן.",
                confidence=0.6,
                key_facts=["ה-USAF מזרז את התוכנית [1]"],
            ),
        )
        result = _synthesize_from_reads(inv)
        assert result is not None
        assert result.outcome == "partial"
        assert result.sources == inv.read_urls
        assert result.confidence == 0.6
        assert "USAF" in result.answer_he
        assert result.relevance_check is None  # no anchors -> gate not run

    def test_relevance_gate_runs_when_anchors_present_and_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inv = self._inv_with_reads(anchors=["Reaper", "Iran"])
        monkeypatch.setattr(
            ds,
            "chat_structured",
            lambda *a, **kw: FallbackSynthesisOut(
                answer_he="ה-Reaper יוחלף מוקדם יותר בעקבות אובדנים מול איראן.", confidence=0.55
            ),
        )
        monkeypatch.setattr(
            ds, "_judge_relevance", lambda *a, **kw: RelevanceVerdict(verdict="yes", reason="עונה על השאלה")
        )
        result = _synthesize_from_reads(inv)
        assert result.outcome == "partial"
        assert result.relevance_check is not None
        assert result.relevance_check["verdict"] == "yes"

    def test_off_topic_synthesis_downgraded_to_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Job 86's original failure mode (an off-topic pile of reads) must not slip through this
        fallback path either -- the same `_relevance_gate` a normal `finish` uses applies here."""
        inv = self._inv_with_reads(anchors=["Reaper", "Iran"])
        monkeypatch.setattr(
            ds,
            "chat_structured",
            lambda *a, **kw: FallbackSynthesisOut(
                answer_he='מערכת MOSP 5000 היא מטע"ד אלקטרו-אופטי.', confidence=0.7
            ),
        )
        monkeypatch.setattr(
            ds,
            "_judge_relevance",
            lambda *a, **kw: RelevanceVerdict(verdict="no", reason="עוסק ב-MOSP 5000, לא ב-Reaper/איראן"),
        )
        result = _synthesize_from_reads(inv)
        assert result.outcome == "not_found"

    def test_llm_failure_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inv = self._inv_with_reads()

        def _raise(*a, **kw):
            raise LLMOutputError("boom")

        monkeypatch.setattr(ds, "chat_structured", _raise)
        assert _synthesize_from_reads(inv) is None

    def test_what_was_tried_mentions_no_finish_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inv = self._inv_with_reads()
        inv.rounds_done = 4
        monkeypatch.setattr(
            ds, "chat_structured", lambda *a, **kw: FallbackSynthesisOut(answer_he="תשובה", confidence=0.3)
        )
        result = _synthesize_from_reads(inv)
        assert "4" in result.what_was_tried_he
        assert "2" in result.what_was_tried_he  # len(read_urls)


class TestInvestigateFallbackWiring:
    """Exercises the wiring inside `investigate()` itself: when the round loop ends with
    `inv.result is None` but pages were read, `_synthesize_from_reads` must be consulted before
    `_finalize_outcome`'s blank-default branch runs; when there is nothing to read from, or the
    synthesis crashes, the existing blank-not_found behaviour must be unaffected.
    """

    def _run_one_round_that_reads_but_never_finishes(self, monkeypatch: pytest.MonkeyPatch) -> Investigation:
        monkeypatch.setattr(ds, "plan_queries", lambda *a, **kw: [])

        def fake_act(inv, budget, transcript, round_no, **kw):
            inv.read_urls.append("https://example.com/a")
            inv.read_summaries.append({"url": "https://example.com/a", "title": "A", "summary": "some facts"})
            return False  # never finishes

        monkeypatch.setattr(ds, "_act", fake_act)
        return ds.investigate("שאלה כלשהי עם עוגן", job_id=1, max_rounds=1)

    def test_synthesis_result_used_when_reads_exist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sentinel = InvestigationOut(
            outcome="partial",
            answer_he="תשובה משוחזרת מהקריאות",
            confidence=0.4,
            sources=["https://example.com/a"],
        )
        monkeypatch.setattr(ds, "_synthesize_from_reads", lambda inv: sentinel)
        inv = self._run_one_round_that_reads_but_never_finishes(monkeypatch)
        assert "תשובה משוחזרת מהקריאות" in inv.result.answer_he
        assert inv.result.outcome == "partial"

    def test_synthesis_crash_falls_back_to_blank_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(inv):
            raise RuntimeError("synthesis exploded")

        monkeypatch.setattr(ds, "_synthesize_from_reads", _boom)
        inv = self._run_one_round_that_reads_but_never_finishes(monkeypatch)
        # `_finalize_outcome`'s existing "no result" default still fires -- never crashes the
        # investigation, and never silently invents an answer.
        assert inv.result is not None
        assert inv.result.outcome == "not_found"

    def test_synthesis_not_called_without_any_reads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ds, "plan_queries", lambda *a, **kw: [])
        monkeypatch.setattr(ds, "_act", lambda *a, **kw: False)

        def _boom(inv):
            raise AssertionError("must not be called when nothing was ever read")

        monkeypatch.setattr(ds, "_synthesize_from_reads", _boom)
        inv = ds.investigate("שאלה כלשהי", job_id=1, max_rounds=1)
        assert inv.result is not None
        assert inv.result.outcome == "not_found"
        assert inv.result.confidence == 0.0


# =================================================================================================
# Job 70: one retry on a transient fetch error (DNS blip), never on a permanent failure
# =================================================================================================


class TestTransientFetchRetry:
    def test_dns_failure_message_is_transient(self) -> None:
        exc = OSError("[Errno -3] Temporary failure in name resolution")
        assert _is_transient_fetch_error(exc) is True

    def test_connection_reset_is_transient(self) -> None:
        assert _is_transient_fetch_error(Exception("Connection reset by peer")) is True

    def test_404_is_not_transient(self) -> None:
        assert _is_transient_fetch_error(Exception("404 Not Found")) is False

    def test_robots_disallow_is_not_transient(self) -> None:
        assert _is_transient_fetch_error(Exception("robots.txt disallows fetching https://x")) is False

    def test_retries_once_on_transient_error_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"n": 0}

        def fake_fetch(url):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("[Errno -3] Temporary failure in name resolution")
            return {"text": "ok", "title": "T"}

        monkeypatch.setattr("eoa.fetch.remote.fetch_remote", fake_fetch)
        monkeypatch.setattr("time.sleep", lambda *a, **kw: None)
        page = _fetch_with_retry("https://example.com")
        assert calls["n"] == 2
        assert page["text"] == "ok"

    def test_never_retries_a_permanent_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"n": 0}

        def fake_fetch(url):
            calls["n"] += 1
            raise ValueError("404 Not Found")

        monkeypatch.setattr("eoa.fetch.remote.fetch_remote", fake_fetch)
        with pytest.raises(ValueError):
            _fetch_with_retry("https://example.com")
        assert calls["n"] == 1

    def test_raises_when_retry_also_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_fetch(url):
            raise OSError("Connection reset by peer")

        monkeypatch.setattr("eoa.fetch.remote.fetch_remote", fake_fetch)
        monkeypatch.setattr("time.sleep", lambda *a, **kw: None)
        with pytest.raises(OSError):
            _fetch_with_retry("https://example.com")


class TestToolReadUsesRetryAndLogsQuarantine:
    def _inv(self) -> Investigation:
        inv = Investigation(job_id=70, item_id=81, question="q")
        inv.hits_seen = {"https://example.com/a": MagicMock()}
        return inv

    def test_successful_read_after_one_transient_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inv = self._inv()
        budget = _budget()
        calls = {"n": 0}

        def fake_fetch(url):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("[Errno -3] Temporary failure in name resolution")
            return {"text": "some page text", "title": "Title"}

        monkeypatch.setattr("eoa.fetch.remote.fetch_remote", fake_fetch)
        monkeypatch.setattr("time.sleep", lambda *a, **kw: None)
        monkeypatch.setattr(
            "eoa.security.guard.screen", lambda *a, **kw: MagicMock(verdict="clean", kind=None, excerpt="")
        )
        monkeypatch.setattr(ds, "_summarise_page", lambda inv_, text, url: "summary text")

        out = _tool_read(inv, budget, "https://example.com/a", round_no=1)

        assert "summary text" in out
        assert inv.read_urls == ["https://example.com/a"]
        assert inv.read_summaries == [
            {"url": "https://example.com/a", "title": "Title", "summary": "summary text"}
        ]

    def test_quarantined_page_logs_the_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inv = self._inv()
        budget = _budget()
        monkeypatch.setattr("eoa.fetch.remote.fetch_remote", lambda url: {"text": "bad", "title": "T"})
        monkeypatch.setattr(
            "eoa.security.guard.screen",
            lambda *a, **kw: MagicMock(verdict="quarantined", kind="instruction_override", excerpt="x"),
        )
        logged: list[dict] = []
        monkeypatch.setattr(
            ds,
            "_log",
            lambda inv_, round_no, lang, query, **kw: logged.append(kw),
        )

        _tool_read(inv, budget, "https://example.com/a", round_no=1)

        assert any(entry.get("url") == "https://example.com/a" for entry in logged)
        assert inv.read_urls == []  # never counted as a real read


# =================================================================================================
# Jobs 47/48/70: fall back to the item's own title/entities when the caller passed no context_he
# =================================================================================================


class TestFallbackItemContext:
    @staticmethod
    def _patch_db(monkeypatch: pytest.MonkeyPatch, row: dict | None) -> None:
        import eoa.db as db_module

        class _FakeConn:
            def execute(self, sql, params=None):
                return self

            def fetchone(self):
                return row

        class _FakeCtx:
            def __enter__(self):
                return _FakeConn()

            def __exit__(self, *exc_info):
                return False

        monkeypatch.setattr(db_module, "connection", lambda: _FakeCtx())

    def test_builds_title_entities_summary_lines(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_db(
            monkeypatch,
            {
                "title": "US Army launches laser production with $465M contract award",
                "entities_mentioned": ["AeroVironment", "US Army"],
                "summary_he": "צבא ארצות הברית העניק חוזה ייצור בסך 464.8 מיליון דולר לחברת AeroVironment",
            },
        )
        ctx = ds._fallback_item_context(10)
        assert "כותרת הפריט: US Army launches laser production with $465M contract award" in ctx
        assert "ישויות: AeroVironment, US Army" in ctx
        assert "תקציר:" in ctx and "AeroVironment" in ctx

    def test_missing_row_returns_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_db(monkeypatch, None)
        assert ds._fallback_item_context(999) == ""

    def test_db_failure_returns_empty_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import eoa.db as db_module

        def _boom():
            raise RuntimeError("no db in this test")

        monkeypatch.setattr(db_module, "connection", _boom)
        assert ds._fallback_item_context(1) == ""

    def test_partial_row_omits_missing_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_db(monkeypatch, {"title": "T", "entities_mentioned": [], "summary_he": None})
        assert ds._fallback_item_context(1) == "כותרת הפריט: T"


class TestInvestigateUsesFallbackContextWhenBlank:
    def test_fallback_context_feeds_extract_anchors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ds, "_fallback_item_context", lambda item_id: "כותרת הפריט: X\nישויות: Anduril, Rafael"
        )
        monkeypatch.setattr(ds, "plan_queries", lambda *a, **kw: [])
        monkeypatch.setattr(ds, "_act", lambda *a, **kw: False)

        inv = ds.investigate("שאלה בלי הקשר", item_id=81, job_id=None, max_rounds=1)

        assert "Anduril" in inv.anchors
        assert "Rafael" in inv.anchors

    def test_fallback_not_used_when_context_already_given(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(item_id):
            raise AssertionError("must not be called when context_he was already provided")

        monkeypatch.setattr(ds, "_fallback_item_context", _boom)
        monkeypatch.setattr(ds, "plan_queries", lambda *a, **kw: [])
        monkeypatch.setattr(ds, "_act", lambda *a, **kw: False)

        ds.investigate("שאלה", item_id=81, job_id=None, context_he="כבר יש הקשר כאן", max_rounds=1)

    def test_fallback_not_attempted_without_item_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(item_id):
            raise AssertionError("must not be called without an item_id")

        monkeypatch.setattr(ds, "_fallback_item_context", _boom)
        monkeypatch.setattr(ds, "plan_queries", lambda *a, **kw: [])
        monkeypatch.setattr(ds, "_act", lambda *a, **kw: False)

        ds.investigate("שאלה", item_id=None, job_id=None, max_rounds=1)


# =================================================================================================
# Cross-cutting: accurate `engine` label in `_tool_search`'s investigation_log entries
# =================================================================================================


class TestToolSearchEngineLabel:
    def _inv(self) -> Investigation:
        return Investigation(job_id=1, item_id=1, question="q")

    def test_logs_the_actual_serving_engine_not_hardcoded_searxng(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from eoa.search.provider import SearchHit, SearchResponse

        hit = SearchHit(url="https://example.com/a", title="T", snippet="S", engine="ddgs")
        monkeypatch.setattr(ds, "search", lambda *a, **kw: SearchResponse("q", "en", [hit]))
        monkeypatch.setattr("eoa.security.heuristics.scan_heuristics", lambda *a, **kw: MagicMock(score=0.0))
        logged: list[dict] = []
        monkeypatch.setattr(ds, "_log", lambda *a, **kw: logged.append(kw))

        _tool_search(self._inv(), _budget(), "some query", "en", round_no=1)

        summary_calls = [c for c in logged if c.get("results_n") == 1]
        assert summary_calls
        assert summary_calls[-1]["engine"] == "ddgs"

    def test_zero_hits_falls_back_to_configured_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.config import settings
        from eoa.search.provider import SearchResponse

        monkeypatch.setattr(ds, "search", lambda *a, **kw: SearchResponse("q", "en", []))
        logged: list[dict] = []
        monkeypatch.setattr(ds, "_log", lambda *a, **kw: logged.append(kw))

        _tool_search(self._inv(), _budget(), "some query", "en", round_no=1)

        assert logged[-1]["engine"] == settings().search.provider


# =================================================================================================
# `plan_queries`'s LLM-failure fallback: anchors, not the raw question, seed the retry
# =================================================================================================


class TestPlanQueriesFallback:
    def test_fallback_uses_anchors_not_raw_question(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*a, **kw):
            raise LLMOutputError("boom")

        monkeypatch.setattr(ds, "chat_structured", _raise)
        queries = plan_queries(
            "שאלה ארוכה ומורכבת שאינה מתאימה כשאילתת חיפוש כמות שהיא",
            1,
            ["he", "en"],
            "",
            anchors=["Reaper", "Iran"],
        )
        assert queries
        for q in queries:
            assert q["query"] == "Reaper Iran"

    def test_fallback_uses_raw_question_when_no_anchors_at_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*a, **kw):
            raise LLMOutputError("boom")

        monkeypatch.setattr(ds, "chat_structured", _raise)
        queries = plan_queries("question with nothing to anchor", 1, ["en"], "", anchors=[])
        assert queries[0]["query"] == "question with nothing to anchor"
