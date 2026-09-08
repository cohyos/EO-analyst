"""Tests for eoa.search.deep_search — budgeting and investigation logic."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

from eoa.search.deep_search import Budget, Investigation, _act, _finalize_outcome


class TestBudget:
    """Test the Budget dataclass for query/page/time constraints."""

    def test_budget_not_exhausted_with_capacity(self):
        """Budget not exhausted when within all limits."""
        budget = Budget(
            max_queries=10,
            max_pages=20,
            deadline=time.monotonic() + 3600,
            confidence_stop=0.8,
        )
        assert budget.exhausted is None

    def test_budget_exhausted_on_timeout(self):
        """exhausted is not None when deadline passed."""
        budget = Budget(
            max_queries=10,
            max_pages=20,
            deadline=time.monotonic() - 1,  # Past deadline
            confidence_stop=0.8,
        )
        assert budget.exhausted == "stopped_timeout"

    def test_budget_exhausted_on_query_and_page_limits(self):
        """exhausted when both query and page limits hit."""
        budget = Budget(
            max_queries=5,
            max_pages=10,
            deadline=time.monotonic() + 3600,
            confidence_stop=0.8,
            queries=5,
            pages=10,
        )
        assert budget.exhausted == "stopped_budget"

    def test_budget_not_exhausted_on_only_query_limit(self):
        """Not exhausted if only queries maxed but pages below limit."""
        budget = Budget(
            max_queries=5,
            max_pages=10,
            deadline=time.monotonic() + 3600,
            confidence_stop=0.8,
            queries=5,
            pages=5,
        )
        assert budget.exhausted is None

    def test_budget_not_exhausted_on_only_page_limit(self):
        """Not exhausted if only pages maxed but queries below limit."""
        budget = Budget(
            max_queries=10,
            max_pages=5,
            deadline=time.monotonic() + 3600,
            confidence_stop=0.8,
            queries=5,
            pages=5,
        )
        assert budget.exhausted is None

    def test_remaining_text_format(self):
        """remaining_text() returns human-readable budget status."""
        deadline = time.monotonic() + 600  # 10 minutes
        budget = Budget(
            max_queries=10,
            max_pages=20,
            deadline=deadline,
            confidence_stop=0.8,
            queries=3,
            pages=5,
        )
        text = budget.remaining_text()
        assert "queries 3/10" in text
        assert "pages 5/20" in text
        assert "min left" in text

    def test_remaining_text_zero_time_left(self):
        """remaining_text() shows 0 min when deadline near."""
        budget = Budget(
            max_queries=10,
            max_pages=20,
            deadline=time.monotonic() + 10,  # 10 seconds
            confidence_stop=0.8,
        )
        text = budget.remaining_text()
        assert "0 min left" in text


class TestInvestigation:
    """Test the Investigation dataclass for tracking a research session."""

    def test_investigation_initialization(self):
        """Investigation initializes with defaults."""
        inv = Investigation(job_id=1, item_id=2, question="What is it?")
        assert inv.job_id == 1
        assert inv.item_id == 2
        assert inv.question == "What is it?"
        assert inv.result is None
        assert inv.outcome == "not_found"
        assert inv.rounds_done == 0
        assert inv.read_urls == []
        assert inv.hits_seen == {}
        assert inv.stop_requested is False

    def test_investigation_tracks_urls(self):
        """read_urls list accumulates visited URLs."""
        inv = Investigation(job_id=None, item_id=None, question="test")
        inv.read_urls.append("https://example.com/1")
        inv.read_urls.append("https://example.com/2")
        assert len(inv.read_urls) == 2
        assert "https://example.com/1" in inv.read_urls

    def test_investigation_tracks_search_hits(self):
        """hits_seen dict stores SearchHit objects by URL."""
        inv = Investigation(job_id=None, item_id=None, question="test")
        hit1 = MagicMock(url="https://example.com/1", title="Title 1")
        hit2 = MagicMock(url="https://example.com/2", title="Title 2")
        inv.hits_seen["https://example.com/1"] = hit1
        inv.hits_seen["https://example.com/2"] = hit2
        assert len(inv.hits_seen) == 2


class TestActToolCalls:
    """Test _act() function's handling of tool calls."""

    def test_act_with_finish_tool_returns_true(self, monkeypatch):
        """_act() returns True when finish tool is called."""
        inv = Investigation(job_id=None, item_id=None, question="test")
        budget = Budget(
            max_queries=10,
            max_pages=20,
            deadline=time.monotonic() + 3600,
            confidence_stop=0.8,
        )
        inv.read_urls = ["https://example.com"]
        inv.hits_seen = {"https://example.com": MagicMock()}

        # Mock chat to return finish tool call
        mock_result = MagicMock()
        mock_result.tool_calls = [
            {
                "function": {
                    "name": "finish",
                    "arguments": json.dumps(
                        {
                            "outcome": "found",
                            "answer_he": "התשובה",
                            "confidence": 0.9,
                            "sources": ["https://example.com"],
                        }
                    ),
                }
            }
        ]
        mock_result.content = ""

        monkeypatch.setattr("eoa.search.deep_search.chat", lambda *a, **kw: mock_result)

        result = _act(inv, budget, [], round_no=1, max_steps=5)
        assert result is True
        assert inv.result is not None
        assert inv.result.outcome == "found"

    def test_act_filters_sources_to_read_urls(self, monkeypatch):
        """Q3-5 (docs/qa/findings_Q3_r1.md): the model's own `sources` claim in `finish` is never
        trusted -- `_act()` accepts it provisionally, but `_finalize_outcome()` (called once the
        loop ends, see `investigate()`) unconditionally overwrites `sources` with `inv.read_urls`,
        the ground truth of what was actually fetched via the `read` tool. This replaces the old
        contract where `_act()` itself intersected the model's claim against `read_urls` -- that
        intersection dropped a page that WAS read whenever the model simply forgot to list it,
        which is exactly the "14/18 jobs persisted sources: []" bug Q3-5 fixes."""
        inv = Investigation(job_id=None, item_id=None, question="test")
        budget = Budget(
            max_queries=10,
            max_pages=20,
            deadline=time.monotonic() + 3600,
            confidence_stop=0.8,
        )
        inv.read_urls = ["https://example.com/1"]  # Only this was read
        inv.hits_seen = {
            "https://example.com/1": MagicMock(),
            "https://example.com/2": MagicMock(),  # This was NOT read
        }

        mock_result = MagicMock()
        mock_result.tool_calls = [
            {
                "function": {
                    "name": "finish",
                    "arguments": json.dumps(
                        {
                            "outcome": "partial",
                            "answer_he": "חלקי",
                            "confidence": 0.6,
                            "sources": [
                                "https://example.com/1",
                                "https://example.com/2",
                                "https://example.com/3",  # Never even seen
                            ],
                        }
                    ),
                }
            }
        ]
        mock_result.content = ""

        monkeypatch.setattr("eoa.search.deep_search.chat", lambda *a, **kw: mock_result)

        result = _act(inv, budget, [], round_no=1, max_steps=5)
        assert result is True

        _finalize_outcome(inv, budget)
        # sources is exactly the set of URLs actually read -- neither the merely-seen URL nor the
        # never-seen one the model hallucinated, regardless of what `finish` claimed.
        assert inv.result.sources == ["https://example.com/1"]


class TestActBudgetExhaustion:
    """Test _act() behavior when budget is exhausted."""

    def test_act_appends_exhaustion_message_to_transcript(self, monkeypatch):
        """When budget exhausted, _act() appends a budget message to transcript."""
        inv = Investigation(job_id=None, item_id=None, question="test")
        budget = Budget(
            max_queries=10,
            max_pages=20,
            deadline=time.monotonic() - 1,  # Past deadline
            confidence_stop=0.8,
        )

        transcript = []

        mock_result = MagicMock()
        mock_result.tool_calls = []
        mock_result.content = "ok"

        monkeypatch.setattr("eoa.search.deep_search.chat", lambda *a, **kw: mock_result)
        monkeypatch.setattr("eoa.search.deep_search._check_stop", lambda *a: None)

        _act(inv, budget, transcript, round_no=1, max_steps=5)
        # Should have added a user message about budget exhaustion
        assert any("תקציב" in str(msg.get("content", "")) for msg in transcript)

    def test_act_stops_after_one_grace_step_past_deadline_instead_of_cycling_to_max_steps(
        self, monkeypatch
    ):
        """PD-fix-3 (2026-09-08, item 5): reproduces why a 600s (dossier.topic_time_cap_s) topic cap
        blew out to 17 minutes live -- before this fix, `_act` KEPT calling `chat()` (and processing
        whatever tool calls the model made) for every remaining step up to `max_steps` once the
        budget/deadline was exhausted, merely re-appending the "budget exhausted" nudge each time
        rather than actually stopping. The model here never calls `finish` no matter how many turns
        it gets -- with the fix, `chat()` (each call standing in for a real, possibly slow LLM/tool
        round-trip) is invoked at most twice total (the step already in flight when exhaustion was
        first detected, plus exactly one grace turn), never cycling through all of `max_steps`."""
        inv = Investigation(job_id=None, item_id=None, question="test")
        budget = Budget(
            max_queries=10,
            max_pages=20,
            deadline=time.monotonic() - 1,  # already past deadline before _act even starts
            confidence_stop=0.8,
        )

        call_count = {"n": 0}
        mock_result = MagicMock()
        mock_result.tool_calls = []  # never finishes
        mock_result.content = "still working"

        def fake_chat(*a, **kw):
            call_count["n"] += 1
            return mock_result

        monkeypatch.setattr("eoa.search.deep_search.chat", fake_chat)
        monkeypatch.setattr("eoa.search.deep_search._check_stop", lambda *a: None)

        max_steps = 12  # the real _DEFAULT_ACT_MAX_STEPS
        result = _act(inv, budget, [], round_no=1, max_steps=max_steps)

        assert result is False  # never finished
        assert call_count["n"] <= 2  # bounded by the one-grace-turn fix, not max_steps


class TestActLlmLegOverride:
    """PD-cloud-tools (2026-09-09): ``_act``'s ``llm_leg`` -> a ``chain_override`` built once
    (via ``eoa.llm.chain.build_chain_with_leg_override``) and passed to every ``chat()`` call this
    loop makes, so a dossier run's per-run leg is tried first for the ReAct tool-calling turns."""

    def test_llm_leg_given_builds_and_forwards_chain_override(self, monkeypatch):
        from eoa.config import ChainEntryCfg

        inv = Investigation(job_id=None, item_id=None, question="test")
        budget = Budget(max_queries=10, max_pages=20, deadline=time.monotonic() + 3600, confidence_stop=0.8)
        inv.read_urls = ["https://example.com"]
        inv.hits_seen = {"https://example.com": MagicMock()}

        override_chain = [ChainEntryCfg(provider="codex", model="gpt-6-astra"), ChainEntryCfg(provider="ollama")]
        built_for: dict[str, str] = {}

        def fake_build_override(role: str, leg: str) -> list:
            built_for["role"] = role
            built_for["leg"] = leg
            return override_chain

        monkeypatch.setattr("eoa.llm.chain.build_chain_with_leg_override", fake_build_override)

        mock_result = MagicMock()
        mock_result.tool_calls = [
            {
                "function": {
                    "name": "finish",
                    "arguments": json.dumps(
                        {"outcome": "found", "answer_he": "x", "confidence": 0.9, "sources": ["https://example.com"]}
                    ),
                }
            }
        ]
        mock_result.content = ""
        seen_kwargs: dict = {}

        def fake_chat(role, transcript, **kw):
            seen_kwargs.update(kw)
            return mock_result

        monkeypatch.setattr("eoa.search.deep_search.chat", fake_chat)
        _act(inv, budget, [], round_no=1, max_steps=5, llm_leg="codex:gpt-6-astra")
        assert built_for == {"role": "investigator", "leg": "codex:gpt-6-astra"}
        assert seen_kwargs["chain_override"] == override_chain

    def test_llm_leg_none_passes_no_chain_override(self, monkeypatch):
        inv = Investigation(job_id=None, item_id=None, question="test")
        budget = Budget(max_queries=10, max_pages=20, deadline=time.monotonic() + 3600, confidence_stop=0.8)
        inv.read_urls = ["https://example.com"]
        inv.hits_seen = {"https://example.com": MagicMock()}

        mock_result = MagicMock()
        mock_result.tool_calls = [
            {
                "function": {
                    "name": "finish",
                    "arguments": json.dumps(
                        {"outcome": "found", "answer_he": "x", "confidence": 0.9, "sources": ["https://example.com"]}
                    ),
                }
            }
        ]
        mock_result.content = ""
        seen_kwargs: dict = {}

        def fake_chat(role, transcript, **kw):
            seen_kwargs.update(kw)
            return mock_result

        monkeypatch.setattr("eoa.search.deep_search.chat", fake_chat)
        _act(inv, budget, [], round_no=1, max_steps=5)  # no llm_leg
        assert seen_kwargs["chain_override"] is None
