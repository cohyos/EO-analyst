"""Unit tests for scripts/repair_classification_guards.py's pure detection helpers (Q3-2/Q3-3/
Q3-4, docs/qa/findings_Q3_r1.md). No DB, no LLM -- only the predicate functions that decide
whether a given already-persisted item row needs repair.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_repair_classification_guards.py -q``
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

if "eoa.db" not in sys.modules:
    try:
        import eoa.db  # noqa: F401
    except ImportError:
        fake_db = types.ModuleType("eoa.db")
        fake_db.connection = lambda: None  # type: ignore[attr-defined]
        fake_db.get_pool = lambda: None  # type: ignore[attr-defined]
        sys.modules["eoa.db"] = fake_db

_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "repair_classification_guards.py"
_spec = importlib.util.spec_from_file_location("repair_classification_guards", _SCRIPT_PATH)
rcg = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(rcg)


class TestInvalidSubdomain:
    def test_valid_subdomain_not_flagged(self) -> None:
        item = {"domain": "airborne_pods", "subdomain": "targeting_pods"}
        assert rcg.invalid_subdomain(item) is False

    def test_invalid_subdomain_flagged(self) -> None:
        """Regression for item 117: subdomain='c_ua_0' does not exist under c_uas."""
        item = {"domain": "c_uas", "subdomain": "c_ua_0"}
        assert rcg.invalid_subdomain(item) is True

    def test_empty_subdomain_not_flagged(self) -> None:
        assert rcg.invalid_subdomain({"domain": "c_uas", "subdomain": ""}) is False
        assert rcg.invalid_subdomain({"domain": "c_uas", "subdomain": None}) is False


class TestShouldGateNoEoir:
    def test_item_117_style_gated(self) -> None:
        item = {
            "domain": "c_uas",
            "entities_mentioned": [],
            "title": "AI deepfake video fools viewers",
            "clean_text": "A deepfake AI video went viral on social media.",
        }
        assert rcg.should_gate_no_eoir(item) is True

    def test_already_out_of_scope_not_gated(self) -> None:
        item = {"domain": "out_of_scope", "entities_mentioned": [], "title": "x", "clean_text": "y"}
        assert rcg.should_gate_no_eoir(item) is False

    def test_has_entities_not_gated(self) -> None:
        item = {"domain": "c_uas", "entities_mentioned": ["Elbit"], "title": "x", "clean_text": "y"}
        assert rcg.should_gate_no_eoir(item) is False

    def test_has_eoir_vocabulary_not_gated(self) -> None:
        item = {
            "domain": "airborne_pods",
            "entities_mentioned": [],
            "title": "New targeting pod",
            "clean_text": "The electro-optical targeting pod features a FLIR sensor.",
        }
        assert rcg.should_gate_no_eoir(item) is False


class TestParseStatedScore:
    def test_parses_score_with_equals(self) -> None:
        assert rcg.parse_stated_score("סכום 11 → `score=8` → רמה orange.") == 8

    def test_parses_score_with_backtick_space(self) -> None:
        assert rcg.parse_stated_score("הסכום הוא 9, מה שמתורגם ל-`score` 5, ולכן הרמה היא orange.") == 5

    def test_no_score_mention_returns_none(self) -> None:
        assert rcg.parse_stated_score("נימוק כלשהו ללא ציון score.") is None

    def test_empty_reason(self) -> None:
        assert rcg.parse_stated_score("") is None


class TestTriageInconsistent:
    def test_item_10_style_stated_score_disagrees(self) -> None:
        """Regression for item 10: reason states score=5 but persisted score is 8."""
        item = {
            "score": 8,
            "triage_reason": "הסכום הוא 9, מה שמתורגם ל-`score` 5, ולכן הרמה היא orange.",
        }
        assert rcg.triage_inconsistent(item) is True

    def test_item_67_style_conflicting_level_word(self) -> None:
        """Regression for item 67: score=8 is numerically consistent with its own stated sum
        (11 -> 8), but the reason's own conclusion names "orange" while score=8 is red-level."""
        item = {
            "score": 8,
            "triage_reason": "סכום הרכיבים 11 → `score=8` → רמה orange.",
        }
        assert rcg.triage_inconsistent(item) is True

    def test_consistent_item_not_flagged(self) -> None:
        item = {"score": 1, "triage_reason": "סכום הרכיבים 3 → `score`=1 → רמת `archive`."}
        assert rcg.triage_inconsistent(item) is False

    def test_no_score_not_flagged(self) -> None:
        assert rcg.triage_inconsistent({"score": None, "triage_reason": "x"}) is False
