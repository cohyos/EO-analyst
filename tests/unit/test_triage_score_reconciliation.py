"""Unit tests for Q3-4 (docs/qa/findings_Q3_r1.md): triage `score` vs component consistency.

``eoa.pipeline.triage._reconcile_score`` checks that ``score`` matches the deterministic
sum-to-score table applied to ``(novelty, magnitude, core_relevance)``, retries once on mismatch,
and falls back to the deterministic value (logging ``triage_score_reconciled``) if the retry still
disagrees.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_triage_score_reconciliation.py -q``
"""

from __future__ import annotations

import pytest

from eoa.errors import LLMOutputError
from eoa.llm.schemas.analysis import TriageOut
from eoa.pipeline import triage


class TestExpectedScoreTable:
    @pytest.mark.parametrize(
        "novelty,magnitude,core_relevance,expected",
        [
            (1, 1, 1, 1),  # sum 3
            (1, 1, 2, 1),  # sum 4
            (1, 2, 2, 2),  # sum 5
            (2, 2, 2, 2),  # sum 6
            (2, 2, 3, 3),  # sum 7
            (2, 3, 3, 4),  # sum 8
            (3, 3, 3, 5),  # sum 9
            (3, 3, 4, 6),  # sum 10
            (3, 4, 4, 7),  # sum 11
            (4, 4, 4, 8),  # sum 12
            (4, 4, 5, 9),  # sum 13
            (4, 5, 5, 10),  # sum 14
            (5, 5, 5, 10),  # sum 15
        ],
    )
    def test_table_matches_triage_md(self, novelty: int, magnitude: int, core_relevance: int, expected: int) -> None:
        assert triage._expected_score(novelty, magnitude, core_relevance) == expected

    def test_known_examples_from_triage_md(self) -> None:
        # (red) core_relevance=5, magnitude=5, novelty=5 -> sum 15 -> score=10
        assert triage._expected_score(novelty=5, magnitude=5, core_relevance=5) == 10
        # (orange) core_relevance=5, magnitude=2, novelty=2 -> sum 9 -> score=5
        assert triage._expected_score(novelty=2, magnitude=2, core_relevance=5) == 5
        # (archive) core_relevance=1, magnitude=1, novelty=1 -> sum 3 -> score=1
        assert triage._expected_score(novelty=1, magnitude=1, core_relevance=1) == 1


class TestScoreMatchesComponents:
    def test_matching_score_true(self) -> None:
        out = TriageOut(score=10, level="red", novelty=5, magnitude=5, core_relevance=5, reason_he="x")
        assert triage._score_matches_components(out) is True

    def test_mismatched_score_false(self) -> None:
        """ids 10/67 style: reason narrates "orange" territory but score is red-level."""
        out = TriageOut(score=9, level="red", novelty=2, magnitude=2, core_relevance=5, reason_he="x")
        assert triage._score_matches_components(out) is False


class TestReasonConflictingLevel:
    def test_reason_conclusion_conflicts_with_expected_level(self) -> None:
        """Regression for item 67: reason concludes 'רמה orange' but score=8 maps to red."""
        reason = "סכום הרכיבים 11 → `score=8` → רמה orange."
        assert triage._reason_conflicting_level(reason, "red") == "orange"

    def test_reason_conclusion_matches_expected_level(self) -> None:
        reason = "סכום הרכיבים 9 → `score=5` → רמה orange."
        assert triage._reason_conflicting_level(reason, "orange") is None

    def test_no_conclusion_phrase_returns_none(self) -> None:
        assert triage._reason_conflicting_level("נימוק כלשהו בלי מסקנת רמה מפורשת.", "red") is None

    def test_english_level_word_recognized(self) -> None:
        assert triage._reason_conflicting_level("Sum is high, level: orange", "red") == "orange"

    def test_empty_reason(self) -> None:
        assert triage._reason_conflicting_level("", "red") is None


class TestReconcileScore:
    def _item(self) -> dict:
        return {"id": 10, "title": "Test item", "domain": "air_defense", "clean_text": "content"}

    def test_matching_score_returns_unchanged_without_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = []
        monkeypatch.setattr(triage, "chat_structured", lambda *a, **k: calls.append(1))

        out = TriageOut(score=10, level="red", novelty=5, magnitude=5, core_relevance=5, reason_he="x")
        result = triage._reconcile_score(self._item(), out)
        assert result is out
        assert calls == []

    def test_mismatch_triggers_retry_and_uses_retried_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fixed = TriageOut(score=5, level="orange", novelty=2, magnitude=2, core_relevance=5, reason_he="fixed")

        def fake_chat_structured(role, schema, messages, **kwargs):
            assert any("סותרים" in m["content"] for m in messages if m["role"] == "user")
            return fixed

        monkeypatch.setattr(triage, "chat_structured", fake_chat_structured)

        # score=9 doesn't match sum(2+2+5=9 -> expected score=5)
        out = TriageOut(score=9, level="red", novelty=2, magnitude=2, core_relevance=5, reason_he="orig")
        result = triage._reconcile_score(self._item(), out)
        assert result is fixed
        assert result.score == 5

    def test_item_67_style_conflicting_reason_level_triggers_retry_even_when_score_matches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression for item 67: score IS numerically consistent with its components (sum
        11 -> score 8), but reason_he's own conclusion says "orange" while score=8 is red-level --
        must still trigger a retry."""
        fixed = TriageOut(
            score=8, level="red", novelty=3, magnitude=5, core_relevance=3, reason_he="סכום 11 -> score=8 -> רמה red."
        )
        calls = []

        def fake_chat_structured(role, schema, messages, **kwargs):
            calls.append(1)
            return fixed

        monkeypatch.setattr(triage, "chat_structured", fake_chat_structured)

        out = TriageOut(
            score=8,
            level="red",
            novelty=3,
            magnitude=5,
            core_relevance=3,
            reason_he="סכום הרכיבים 11 → `score=8` → רמה orange.",
        )
        result = triage._reconcile_score(self._item(), out)
        assert len(calls) == 1  # retried despite the numerically-consistent score
        assert result is fixed

    def test_retry_still_mismatched_uses_deterministic_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        still_bad = TriageOut(score=9, level="red", novelty=2, magnitude=2, core_relevance=5, reason_he="still bad")
        monkeypatch.setattr(triage, "chat_structured", lambda *a, **k: still_bad)

        out = TriageOut(score=9, level="red", novelty=2, magnitude=2, core_relevance=5, reason_he="orig")
        result = triage._reconcile_score(self._item(), out)
        assert result.score == 5  # deterministic sum-to-score for (2,2,5)

    def test_retry_failure_falls_back_to_deterministic_score_on_original(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_error(*a, **k):
            raise LLMOutputError("boom")

        monkeypatch.setattr(triage, "chat_structured", raise_error)

        out = TriageOut(score=9, level="red", novelty=2, magnitude=2, core_relevance=5, reason_he="orig")
        result = triage._reconcile_score(self._item(), out)
        assert result is out
        assert result.score == 5


class TestTriageItemAndBatchIntegration:
    def test_triage_item_reconciles_before_setting_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mismatched = TriageOut(score=9, level="red", novelty=2, magnitude=2, core_relevance=5, reason_he="orig")
        monkeypatch.setattr(triage, "chat_structured", lambda *a, **k: mismatched)

        item = {"id": 67, "title": "Test", "domain": "air_defense", "clean_text": "x"}
        out = triage.triage_item(item)
        assert out.score == 5
        assert out.level == triage.level_for(5)

    def test_triage_batch_reconciles_each_mismatched_item(self, monkeypatch: pytest.MonkeyPatch) -> None:
        item10 = {"id": 10, "title": "Item 10", "domain": "air_defense", "clean_text": "x"}
        item67 = {"id": 67, "title": "Item 67", "domain": "c_uas", "clean_text": "y"}

        batch_result = {
            10: TriageOut(score=9, level="red", novelty=2, magnitude=2, core_relevance=5, reason_he="orig10"),
            67: TriageOut(score=10, level="red", novelty=5, magnitude=5, core_relevance=5, reason_he="orig67"),
        }
        monkeypatch.setattr(triage, "chat_structured_batch", lambda *a, **k: batch_result)
        # per-item reconciliation retry for item 10 only (67 already matches: 5+5+5=15 -> score=10)
        reconcile_calls = []

        def fake_chat_structured(role, schema, messages, **kwargs):
            reconcile_calls.append(1)
            return TriageOut(score=5, level="orange", novelty=2, magnitude=2, core_relevance=5, reason_he="fixed10")

        monkeypatch.setattr(triage, "chat_structured", fake_chat_structured)

        results = triage.triage_batch([item10, item67])
        assert len(reconcile_calls) == 1  # only item 10 needed reconciliation
        assert results[10].score == 5
        assert results[67].score == 10
