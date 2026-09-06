"""Unit tests for goal 1 (2026-09-06, citation discipline by construction): the structured
sentence-per-claim schema additions in ``eoa.llm.schemas.analysis`` -- ``Sentence``, ``AnalystNote``,
``StructuredSection``, ``OutlookIndicator`` and the resulting ``DailyReportDraft``.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_report_schema.py -q``
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from eoa.llm.schemas.analysis import (
    AnalystNote,
    DailyReportDraft,
    OutlookIndicator,
    Sentence,
    StructuredSection,
)

# --------------------------------------------------------------------------
# Sentence
# --------------------------------------------------------------------------


def test_sentence_with_cites_is_valid():
    s = Sentence(text_he="אלביט זכתה בחוזה.", cites=[1, 2])
    assert s.cites == [1, 2]


def test_sentence_empty_cites_rejected():
    with pytest.raises(ValidationError):
        Sentence(text_he="משפט ללא מקור.", cites=[])


def test_sentence_missing_cites_rejected():
    with pytest.raises(ValidationError):
        Sentence(text_he="משפט ללא מקור.")  # type: ignore[call-arg]


def test_sentence_inline_citation_marker_rejected():
    with pytest.raises(ValidationError):
        Sentence(text_he="אלביט זכתה בחוזה [1].", cites=[1])


def test_sentence_empty_text_rejected():
    with pytest.raises(ValidationError):
        Sentence(text_he="   ", cites=[1])


# --------------------------------------------------------------------------
# AnalystNote
# --------------------------------------------------------------------------


def test_analyst_note_allows_up_to_three_sentences():
    note = AnalystNote(sentences_he=["א.", "ב.", "ג."])
    assert len(note.sentences_he) == 3


def test_analyst_note_rejects_more_than_three_sentences():
    with pytest.raises(ValidationError):
        AnalystNote(sentences_he=["א.", "ב.", "ג.", "ד."])


def test_analyst_note_rejects_inline_citation_marker():
    with pytest.raises(ValidationError):
        AnalystNote(sentences_he=["זה מבוסס על [1]."])


def test_analyst_note_empty_is_valid():
    assert AnalystNote(sentences_he=[]).sentences_he == []


# --------------------------------------------------------------------------
# StructuredSection
# --------------------------------------------------------------------------


def test_structured_section_all_sentences_cited_is_valid():
    section = StructuredSection(
        title_he="פודים אוויריים",
        domain="airborne_pods",
        sentences=[Sentence(text_he="החברה זכתה בחוזה.", cites=[1])],
    )
    assert len(section.sentences) == 1


def test_structured_section_empty_sentences_is_valid():
    """A section with no sentences at all (all filtered out upstream) must not itself be an
    error -- the caller decides whether to render an empty section."""
    section = StructuredSection(title_he="ריק", domain="c_uas", sentences=[])
    assert section.sentences == []


# --------------------------------------------------------------------------
# OutlookIndicator
# --------------------------------------------------------------------------


def test_outlook_indicator_cited_without_assessment_is_valid():
    ind = OutlookIndicator(text_he="מכרז חדש צפוי להתפרסם.", cites=[1])
    assert not ind.is_assessment


def test_outlook_indicator_assessment_with_marker_is_valid():
    ind = OutlookIndicator(text_he="להערכתנו המכרז יתפרסם ברבעון הבא.", cites=[], is_assessment=True)
    assert ind.is_assessment


@pytest.mark.parametrize("marker", ["להערכתנו", "נראה ש", "ייתכן"])
def test_outlook_indicator_accepts_all_assessment_markers(marker):
    ind = OutlookIndicator(text_he=f"{marker} זה יקרה.", cites=[], is_assessment=True)
    assert ind.is_assessment


def test_outlook_indicator_empty_cites_without_assessment_flag_rejected():
    with pytest.raises(ValidationError):
        OutlookIndicator(text_he="זה יקרה.", cites=[], is_assessment=False)


def test_outlook_indicator_assessment_without_marker_rejected():
    with pytest.raises(ValidationError):
        OutlookIndicator(text_he="זה יקרה.", cites=[], is_assessment=True)


def test_outlook_indicator_inline_citation_marker_rejected():
    with pytest.raises(ValidationError):
        OutlookIndicator(text_he="זה יקרה [1].", cites=[1])


# --------------------------------------------------------------------------
# DailyReportDraft
# --------------------------------------------------------------------------


def test_daily_report_draft_exec_summary_requires_cites_per_sentence():
    with pytest.raises(ValidationError):
        DailyReportDraft(exec_summary=[Sentence(text_he="x", cites=[])])


def test_daily_report_draft_minimal_valid_construction():
    draft = DailyReportDraft(
        exec_summary=[Sentence(text_he="תקציר.", cites=[1])],
        sections=[
            StructuredSection(
                title_he="כללי", domain="c_uas", sentences=[Sentence(text_he="פרוזה.", cites=[1])]
            )
        ],
        analyst_note_he=AnalystNote(sentences_he=["להערכתנו זה חשוב."]),
        outlook=[OutlookIndicator(text_he="להערכתנו זה יימשך.", cites=[], is_assessment=True)],
        open_points_he=["שאלה פתוחה."],
    )
    assert draft.exec_summary[0].cites == [1]
    assert draft.analyst_note_he.sentences_he == ["להערכתנו זה חשוב."]


def test_daily_report_draft_defaults_are_empty():
    draft = DailyReportDraft()
    assert draft.exec_summary == []
    assert draft.sections == []
    assert draft.analyst_note_he is None
    assert draft.outlook == []
    assert draft.open_points_he == []
    assert draft.system_note_he == ""
