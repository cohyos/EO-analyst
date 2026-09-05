"""Tests for the deep-search outcome/finish-rigor fixes (U11/F17/F18, docs/REVIEW_2026-09-05.md).

- F18: a `not_found` investigation must never report high confidence (investigation #46 reported
  confidence 1.0 on a `not_found`, which is meaningless to the analyst).
- U11: `not_found`/`stopped_budget`/`insufficient_context` were conflated into one string; the
  API/UI need to tell "ran out of budget" apart from "genuinely searched and found nothing" apart
  from "there was nothing to search at all".
- U11: the model should not be allowed to `finish` with `not_found` before spending the persistence
  protocol's minimum search effort (>= 3 queries, >= 2 page reads), unless the search turned up
  zero hits at all (nothing left to read).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_deep_search_outcomes.py -q``
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

from eoa.llm.schemas.analysis import InvestigationOut
from eoa.search.deep_search import (
    MIN_PAGES_BEFORE_NOT_FOUND,
    MIN_QUERIES_BEFORE_NOT_FOUND,
    NOT_FOUND_MAX_CONFIDENCE,
    PARTIAL_MIN_SOURCES_FOR_HIGH_CONFIDENCE,
    PARTIAL_SINGLE_SOURCE_MAX_CONFIDENCE,
    UNVERIFIED_PREFIX_HE,
    Budget,
    Investigation,
    _act,
    _finalize_outcome,
)


def _budget(**overrides) -> Budget:
    base = dict(max_queries=15, max_pages=30, deadline=time.monotonic() + 3600, confidence_stop=0.8)
    base.update(overrides)
    return Budget(**base)


class TestFinalizeOutcomeConfidenceClamp:
    def test_not_found_confidence_clamped(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.result = InvestigationOut(outcome="not_found", answer_he="לא נמצא", confidence=1.0, sources=[])
        _finalize_outcome(inv, _budget())
        assert inv.result.confidence <= NOT_FOUND_MAX_CONFIDENCE

    def test_found_confidence_not_touched(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.result = InvestigationOut(outcome="found", answer_he="נמצא", confidence=0.95, sources=["https://x"])
        _finalize_outcome(inv, _budget())
        assert inv.result.confidence == 0.95


class TestFinalizeOutcomeClassification:
    def test_missing_result_becomes_honest_not_found(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        _finalize_outcome(inv, _budget())
        assert inv.result is not None
        assert inv.result.outcome == "not_found"
        assert inv.result.confidence == 0.0

    def test_zero_hits_is_insufficient_context_not_not_found(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.result = InvestigationOut(outcome="not_found", answer_he="לא נמצא", confidence=0.0, sources=[])
        assert inv.hits_seen == {}
        _finalize_outcome(inv, _budget(queries=5, pages=0))
        assert inv.outcome == "insufficient_context"

    def test_hits_seen_but_genuinely_not_found_stays_not_found(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.hits_seen = {"https://x": MagicMock()}
        inv.result = InvestigationOut(outcome="not_found", answer_he="לא נמצא", confidence=0.0, sources=[])
        budget = _budget(queries=5, pages=3)  # budget not exhausted
        _finalize_outcome(inv, budget)
        assert inv.outcome == "not_found"

    def test_budget_exhausted_overrides_not_found(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.hits_seen = {"https://x": MagicMock()}
        inv.result = InvestigationOut(outcome="not_found", answer_he="לא נמצא", confidence=0.0, sources=[])
        budget = _budget(queries=15, pages=30)  # exhausted (queries>=max and pages>=max)
        _finalize_outcome(inv, budget)
        assert inv.outcome == "stopped_budget"

    def test_found_outcome_is_kept_verbatim(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.result = InvestigationOut(outcome="found", answer_he="נמצא", confidence=0.9, sources=["https://x"])
        _finalize_outcome(inv, _budget())
        assert inv.outcome == "found"

    def test_budget_accounting_fields_populated(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.rounds_done = 3
        inv.result = InvestigationOut(outcome="found", answer_he="נמצא", confidence=0.9, sources=["https://x"])
        budget = _budget(queries=7, pages=4)
        _finalize_outcome(inv, budget)
        assert inv.queries_used == 7
        assert inv.max_queries == 15
        assert inv.pages_used == 4
        assert inv.max_pages == 30
        assert inv.stopped_reason == "found"


class TestFinalizeOutcomeSourcesGroundTruth:
    """Q3-5 (docs/qa/findings_Q3_r1.md): `sources` always ends up as exactly the URLs actually
    fetched via the `read` tool (`inv.read_urls`), regardless of outcome and regardless of what
    the model's own `finish` call reported -- fixes the "14/18 deep-search jobs persisted
    sources: []" bug (a page WAS read, but the model's own `sources` list omitted or
    mis-formatted it, and the old code trusted that list instead of the tool-call record)."""

    def test_read_urls_populate_sources_even_when_model_omitted_them(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.read_urls = ["https://example.com/a"]
        inv.result = InvestigationOut(outcome="partial", answer_he="נמצא חלקית", confidence=0.5, sources=[])
        _finalize_outcome(inv, _budget())
        assert inv.result.sources == ["https://example.com/a"]

    def test_hallucinated_source_never_read_is_dropped(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.read_urls = ["https://example.com/a"]
        inv.result = InvestigationOut(
            outcome="partial",
            answer_he="נמצא חלקית",
            confidence=0.5,
            sources=["https://example.com/a", "https://example.com/never-read"],
        )
        _finalize_outcome(inv, _budget())
        assert inv.result.sources == ["https://example.com/a"]

    def test_not_found_outcome_still_gets_actually_read_sources(self) -> None:
        """A `not_found` conclusion can still rest on pages that were read (they just didn't
        answer the question) -- those reads must still be recorded as sources."""
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.read_urls = ["https://example.com/a", "https://example.com/b"]
        inv.result = InvestigationOut(outcome="not_found", answer_he="לא נמצא", confidence=0.0, sources=[])
        _finalize_outcome(inv, _budget())
        assert inv.result.sources == ["https://example.com/a", "https://example.com/b"]

    def test_no_reads_yields_empty_sources(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.result = InvestigationOut(outcome="not_found", answer_he="לא נמצא", confidence=0.0, sources=[])
        _finalize_outcome(inv, _budget())
        assert inv.result.sources == []


class TestFinalizeOutcomePartialConfidenceCap:
    """Q3-5: a `partial` outcome needs >= 2 independently read sources to justify confidence
    above :data:`PARTIAL_SINGLE_SOURCE_MAX_CONFIDENCE`; zero sources gets the
    :data:`UNVERIFIED_PREFIX_HE` prefix so the claim never reads as verified."""

    def test_single_source_partial_confidence_capped(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.read_urls = ["https://example.com/a"]
        inv.result = InvestigationOut(outcome="partial", answer_he="נמצא", confidence=0.9, sources=[])
        _finalize_outcome(inv, _budget())
        assert inv.result.confidence <= PARTIAL_SINGLE_SOURCE_MAX_CONFIDENCE

    def test_two_sources_partial_confidence_not_capped(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.read_urls = ["https://example.com/a", "https://example.com/b"]
        assert len(inv.read_urls) >= PARTIAL_MIN_SOURCES_FOR_HIGH_CONFIDENCE
        inv.result = InvestigationOut(outcome="partial", answer_he="נמצא", confidence=0.9, sources=[])
        _finalize_outcome(inv, _budget())
        assert inv.result.confidence == 0.9

    def test_zero_source_partial_gets_unverified_prefix(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.result = InvestigationOut(outcome="partial", answer_he="חרב ברזל פותחה בשיתוף רפאל", confidence=0.5, sources=[])
        _finalize_outcome(inv, _budget())
        assert inv.result.answer_he.startswith(UNVERIFIED_PREFIX_HE)
        assert inv.result.confidence <= PARTIAL_SINGLE_SOURCE_MAX_CONFIDENCE

    def test_unverified_prefix_not_doubled(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.result = InvestigationOut(
            outcome="partial", answer_he=f"{UNVERIFIED_PREFIX_HE}כבר קיים", confidence=0.5, sources=[]
        )
        _finalize_outcome(inv, _budget())
        assert inv.result.answer_he == f"{UNVERIFIED_PREFIX_HE}כבר קיים"

    def test_partial_with_sources_not_prefixed(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.read_urls = ["https://example.com/a"]
        inv.result = InvestigationOut(outcome="partial", answer_he="נמצא חלקית", confidence=0.5, sources=[])
        _finalize_outcome(inv, _budget())
        assert not inv.result.answer_he.startswith(UNVERIFIED_PREFIX_HE)

    def test_found_outcome_never_gets_unverified_prefix_or_cap(self) -> None:
        inv = Investigation(job_id=1, item_id=1, question="q")
        inv.result = InvestigationOut(outcome="found", answer_he="נמצא בוודאות", confidence=0.95, sources=[])
        _finalize_outcome(inv, _budget())
        assert inv.result.confidence == 0.95
        assert not inv.result.answer_he.startswith(UNVERIFIED_PREFIX_HE)


class TestActNotFoundRigor:
    def _finish_call(self, outcome: str = "not_found", confidence: float = 0.0) -> MagicMock:
        mock_result = MagicMock()
        mock_result.tool_calls = [
            {
                "function": {
                    "name": "finish",
                    "arguments": json.dumps(
                        {"outcome": outcome, "answer_he": "לא נמצא", "confidence": confidence, "sources": []}
                    ),
                }
            }
        ]
        mock_result.content = ""
        return mock_result

    def test_rejects_early_not_found_when_hits_exist(self, monkeypatch) -> None:
        inv = Investigation(job_id=None, item_id=None, question="test")
        inv.hits_seen = {"https://x": MagicMock()}  # there IS something to keep searching
        budget = _budget(queries=1, pages=0)  # well below the minimum-effort bar

        monkeypatch.setattr("eoa.search.deep_search.chat", lambda *a, **kw: self._finish_call())
        result = _act(inv, budget, [], round_no=1, max_steps=1)

        assert result is False  # not allowed to finish yet
        assert inv.result is None

    def test_allows_immediate_not_found_when_zero_hits(self, monkeypatch) -> None:
        inv = Investigation(job_id=None, item_id=None, question="test")
        assert inv.hits_seen == {}  # nothing was ever found -- honest to stop immediately
        budget = _budget(queries=1, pages=0)

        monkeypatch.setattr("eoa.search.deep_search.chat", lambda *a, **kw: self._finish_call())
        result = _act(inv, budget, [], round_no=1, max_steps=1)

        assert result is True
        assert inv.result is not None
        assert inv.result.outcome == "not_found"

    def test_allows_not_found_once_minimum_effort_met(self, monkeypatch) -> None:
        inv = Investigation(job_id=None, item_id=None, question="test")
        inv.hits_seen = {"https://x": MagicMock()}
        budget = _budget(queries=MIN_QUERIES_BEFORE_NOT_FOUND, pages=MIN_PAGES_BEFORE_NOT_FOUND)

        monkeypatch.setattr("eoa.search.deep_search.chat", lambda *a, **kw: self._finish_call())
        result = _act(inv, budget, [], round_no=1, max_steps=1)

        assert result is True
        assert inv.result is not None

    def test_rejection_message_names_the_missing_effort(self, monkeypatch) -> None:
        inv = Investigation(job_id=None, item_id=None, question="test")
        inv.hits_seen = {"https://x": MagicMock()}
        budget = _budget(queries=1, pages=0)
        transcript: list[dict] = []

        monkeypatch.setattr("eoa.search.deep_search.chat", lambda *a, **kw: self._finish_call())
        _act(inv, budget, transcript, round_no=1, max_steps=1)

        tool_msgs = [m for m in transcript if m.get("tool_name") == "finish"]
        assert tool_msgs
        assert "not_found requires at least" in tool_msgs[-1]["content"]
