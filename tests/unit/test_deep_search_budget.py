"""Tests for eoa.search.deep_search — budgeting and investigation logic."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from eoa.search.deep_search import Budget, Investigation, _act
from eoa.llm.schemas.analysis import InvestigationOut


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
                    "arguments": json.dumps({
                        "outcome": "found",
                        "answer_he": "התשובה",
                        "confidence": 0.9,
                        "sources": ["https://example.com"],
                    }),
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
        """_act() finish only includes sources that were read."""
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
                    "arguments": json.dumps({
                        "outcome": "partial",
                        "answer_he": "חלקי",
                        "confidence": 0.6,
                        "sources": [
                            "https://example.com/1",
                            "https://example.com/2",
                            "https://example.com/3",  # Never even seen
                        ],
                    }),
                }
            }
        ]
        mock_result.content = ""

        monkeypatch.setattr("eoa.search.deep_search.chat", lambda *a, **kw: mock_result)

        result = _act(inv, budget, [], round_no=1, max_steps=5)
        assert result is True
        # Only the URL that was read or in hits_seen should be kept
        assert "https://example.com/1" in inv.result.sources
        assert "https://example.com/2" in inv.result.sources  # In hits_seen
        assert "https://example.com/3" not in inv.result.sources  # Not in either


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
