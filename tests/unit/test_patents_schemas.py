"""Tests for eoa.llm.schemas.patents (2026-09-06 goal: citation discipline "by construction" for
the patent-survey narrative -- mirrors tests/unit/test_analysis_schemas.py's style for
eoa.llm.schemas.analysis.Sentence/OutlookIndicator, if that file exists; otherwise a fresh,
self-contained suite)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from eoa.llm.schemas.patents import (
    GENERAL_KNOWLEDGE_LABEL_HE,
    AssigneeProfile,
    PatentBizAction,
    PatentCiteSentence,
    PatentSurveyDraft,
)


def _sentence(text: str = "משפט לדוגמה.", cites: list[int] | None = None) -> PatentCiteSentence:
    return PatentCiteSentence(text_he=text, cites=cites if cites is not None else [1])


class TestPatentCiteSentence:
    def test_valid_cited_sentence(self):
        s = PatentCiteSentence(text_he="חברת X רשמה פטנט חדש.", cites=[1, 2])
        assert s.cites == [1, 2]

    def test_empty_cites_without_general_knowledge_rejected(self):
        with pytest.raises(ValidationError):
            PatentCiteSentence(text_he="משפט ללא הפניה.", cites=[])

    def test_general_knowledge_with_empty_cites_allowed(self):
        s = PatentCiteSentence(
            text_he=f"{GENERAL_KNOWLEDGE_LABEL_HE} הקשר היסטורי כללי.",
            cites=[],
            is_general_knowledge=True,
        )
        assert s.cites == []

    def test_general_knowledge_without_label_prefix_rejected(self):
        with pytest.raises(ValidationError):
            PatentCiteSentence(text_he="הקשר היסטורי כללי בלי תווית.", cites=[], is_general_knowledge=True)

    def test_inline_numeric_marker_rejected(self):
        with pytest.raises(ValidationError):
            PatentCiteSentence(text_he="משפט עם ציטוט [1] מוטבע.", cites=[1])

    def test_inline_p_marker_rejected(self):
        with pytest.raises(ValidationError):
            PatentCiteSentence(text_he="משפט עם ציטוט [P1] מוטבע.", cites=[1])

    def test_empty_text_rejected(self):
        with pytest.raises(ValidationError):
            PatentCiteSentence(text_he="   ", cites=[1])


class TestAssigneeProfile:
    def test_requires_at_least_one_chain_sentence(self):
        with pytest.raises(ValidationError):
            AssigneeProfile(
                assignee_name="Anduril",
                tech_product_chain=[],
                implications_he=[_sentence()],
            )

    def test_requires_at_least_one_implication_sentence(self):
        with pytest.raises(ValidationError):
            AssigneeProfile(
                assignee_name="Anduril",
                tech_product_chain=[_sentence()],
                implications_he=[],
            )

    def test_recent_activity_may_be_empty(self):
        profile = AssigneeProfile(
            assignee_name="Anduril",
            tech_product_chain=[_sentence()],
            recent_activity=[],
            implications_he=[_sentence()],
        )
        assert profile.recent_activity == []


class TestPatentBizAction:
    def test_valid_action(self):
        action = PatentBizAction(action_he="לפנות ללקוח.", rationale_he="נימוק קצר.", rationale_cites=[1])
        assert action.rationale_cites == [1]

    def test_empty_action_text_rejected(self):
        with pytest.raises(ValidationError):
            PatentBizAction(action_he="", rationale_he="נימוק.")

    def test_inline_marker_in_rationale_rejected(self):
        with pytest.raises(ValidationError):
            PatentBizAction(action_he="לפנות ללקוח.", rationale_he="נימוק עם [1] מוטבע.")


def _profile(name: str = "Anduril") -> AssigneeProfile:
    return AssigneeProfile(
        assignee_name=name,
        tech_product_chain=[_sentence()],
        implications_he=[_sentence()],
    )


def _draft(**overrides) -> dict:
    base = dict(
        exec_summary=[_sentence()],
        landscape=[_sentence()],
        assignee_profiles=[_profile()],
        business_implications=[
            PatentBizAction(action_he="פעולה 1.", rationale_he="נימוק 1.", rationale_cites=[1]),
            PatentBizAction(action_he="פעולה 2.", rationale_he="נימוק 2.", rationale_cites=[1]),
            PatentBizAction(action_he="פעולה 3.", rationale_he="נימוק 3.", rationale_cites=[1]),
        ],
    )
    base.update(overrides)
    return base


class TestPatentSurveyDraft:
    def test_minimal_valid_draft(self):
        draft = PatentSurveyDraft(**_draft())
        assert len(draft.assignee_profiles) == 1
        assert len(draft.business_implications) == 3

    def test_requires_at_least_one_assignee_profile(self):
        with pytest.raises(ValidationError):
            PatentSurveyDraft(**_draft(assignee_profiles=[]))

    def test_rejects_more_than_five_assignee_profiles(self):
        with pytest.raises(ValidationError):
            PatentSurveyDraft(**_draft(assignee_profiles=[_profile(f"A{i}") for i in range(6)]))

    def test_requires_at_least_three_business_actions(self):
        with pytest.raises(ValidationError):
            PatentSurveyDraft(
                **_draft(
                    business_implications=[
                        PatentBizAction(action_he="פעולה.", rationale_he="נימוק.", rationale_cites=[1])
                    ]
                )
            )

    def test_rejects_more_than_six_business_actions(self):
        actions = [
            PatentBizAction(action_he=f"פעולה {i}.", rationale_he="נימוק.", rationale_cites=[1])
            for i in range(7)
        ]
        with pytest.raises(ValidationError):
            PatentSurveyDraft(**_draft(business_implications=actions))

    def test_optional_sections_default_empty(self):
        draft = PatentSurveyDraft(**_draft())
        assert draft.tech_clusters == []
        assert draft.white_spaces == []
        assert draft.israel_position == []
        assert draft.outlook == []
        assert draft.open_points_he == []
