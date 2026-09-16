"""Unit tests for CR-platform-opportunity (2026-09-16): ``eoa.pipeline.triage.
_apply_platform_opportunity_floor`` -- guarantees ``level >= yellow`` for an item
``eoa.pipeline.classify.apply_platform_opportunity_gate`` tagged
``platform_integration_opportunity``, regardless of how low the model's own
novelty/magnitude/core_relevance components scored it.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_triage_platform_opportunity.py -q``
"""

from __future__ import annotations

from eoa.config import settings
from eoa.llm.schemas.analysis import TriageOut
from eoa.pipeline.opportunity_signals import TAG
from eoa.pipeline.triage import _apply_platform_opportunity_floor, level_for


def _out(score: int) -> TriageOut:
    return TriageOut(score=score, level=level_for(score), novelty=1, magnitude=1, core_relevance=1, reason_he="x")


class TestApplyPlatformOpportunityFloor:
    def test_tagged_item_below_yellow_is_floored(self) -> None:
        item = {"id": 20162, "tags": [TAG]}
        out = _apply_platform_opportunity_floor(item, _out(1))
        yellow_min = settings().triage.levels["yellow"]
        assert out.score == yellow_min
        assert level_for(out.score) == "yellow"

    def test_tagged_item_already_at_or_above_yellow_is_untouched(self) -> None:
        item = {"id": 22760, "tags": [TAG]}
        out = _apply_platform_opportunity_floor(item, _out(7))
        assert out.score == 7  # never lowered

    def test_untagged_item_is_a_noop(self) -> None:
        item = {"id": 999, "tags": ["some_other_tag"]}
        out = _apply_platform_opportunity_floor(item, _out(1))
        assert out.score == 1

    def test_missing_tags_field_is_a_noop(self) -> None:
        item = {"id": 999}
        out = _apply_platform_opportunity_floor(item, _out(2))
        assert out.score == 2

    def test_never_lowers_a_high_score(self) -> None:
        item = {"id": 22760, "tags": [TAG]}
        out = _apply_platform_opportunity_floor(item, _out(10))
        assert out.score == 10
