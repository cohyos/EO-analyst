"""Unit tests for eoa.report.qa_citations: sentence splitting, factual detection, citation QA."""

from __future__ import annotations

from eoa.llm.schemas.analysis import (
    AnalystNote,
    DailyReportDraft,
    OutlookIndicator,
    ReportSection,
    Sentence,
    StructuredSection,
)
from eoa.llm.schemas.reports import MonthlyReportDraftLegacy
from eoa.report.qa_citations import check, is_factual, split_sentences

# --------------------------------------------------------------------------
# split_sentences
# --------------------------------------------------------------------------


def test_split_sentences_basic():
    text = "אלביט מערכות זכתה בחוזה. הערך הכולל הוא 50 מיליון דולר."
    assert split_sentences(text) == [
        "אלביט מערכות זכתה בחוזה.",
        "הערך הכולל הוא 50 מיליון דולר.",
    ]


def test_split_sentences_handles_question_and_exclamation():
    text = "האם זו פריצת דרך? כן, ללא ספק!"
    assert split_sentences(text) == ["האם זו פריצת דרך?", "כן, ללא ספק!"]


def test_split_sentences_handles_colon_boundary():
    text = "הנתונים ברורים: החברה גדלה. הרבעון הבא צפוי להיות חזק."
    sentences = split_sentences(text)
    assert sentences[0] == "הנתונים ברורים:"
    assert sentences[-1] == "הרבעון הבא צפוי להיות חזק."


def test_split_sentences_ignores_decimal_point_mid_number():
    text = "המחיר עלה ל-3.5 מיליון דולר ברבעון האחרון."
    sentences = split_sentences(text)
    assert len(sentences) == 1
    assert sentences[0] == text


def test_split_sentences_ignores_dr_abbreviation():
    text = 'הדובר הוא ד"ר. הוא פרסם מאמר חדש בנושא.'
    assert split_sentences(text) == ['הדובר הוא ד"ר. הוא פרסם מאמר חדש בנושא.']


def test_split_sentences_ignores_country_abbreviation():
    text = 'החברה ממוקמת בארה"ב. היא מעסיקה 500 עובדים.'
    assert split_sentences(text) == ['החברה ממוקמת בארה"ב. היא מעסיקה 500 עובדים.']


def test_split_sentences_empty_text():
    assert split_sentences("") == []
    assert split_sentences("   \n  ") == []


def test_split_sentences_no_trailing_punctuation():
    text = "משפט אחד ללא סימן פיסוק בסוף"
    assert split_sentences(text) == [text]


# --------------------------------------------------------------------------
# is_factual
# --------------------------------------------------------------------------


def test_is_factual_true_for_digit():
    assert is_factual("החברה גייסה 10 מיליון דולר.")


def test_is_factual_true_for_currency_sign():
    assert is_factual("העסקה הוערכה ב-$5 מיליון.")


def test_is_factual_true_for_latin_entity():
    assert is_factual('Elbit Systems הכריזה על שת"פ חדש.')


def test_is_factual_true_for_month_name():
    assert is_factual("האירוע צפוי להתקיים בספטמבר הקרוב.")


def test_is_factual_true_for_announcement_verb():
    assert is_factual("החברה זכתה במכרז המרכזי.")


def test_is_factual_false_for_plain_sentence():
    assert not is_factual("זהו משפט רגיל ללא עובדות מיוחדות כלל.")


# --------------------------------------------------------------------------
# check() -- goal 1 (2026-09-06) structured DailyReportDraft: Sentence.cites is enforced by the
# schema itself (see test_report_schema.py), so check() only has to validate the *registry range*
# of each cites entry, plus the F5 duplicate-sentence rule -- there is no "uncited sentence" case
# to test here any more.
# --------------------------------------------------------------------------

ITEMS = [{"n": 1}, {"n": 2}, {"n": 3}]


def _draft(
    summary_cites: list[int] | None = (1,),
    sections=None,
    outlook: list[OutlookIndicator] | None = None,
    open_points=None,
    summary_text: str = "תקציר תקין.",
) -> DailyReportDraft:
    return DailyReportDraft(
        exec_summary=[Sentence(text_he=summary_text, cites=list(summary_cites))] if summary_cites else [],
        sections=sections or [],
        outlook=outlook or [],
        open_points_he=open_points or [],
    )


def test_check_passes_when_every_sentence_has_valid_cites():
    result = check(_draft(), ITEMS)
    assert result.passed
    assert result.errors == []


def test_check_fails_on_out_of_range_reference():
    result = check(_draft(summary_cites=[99]), ITEMS)
    assert not result.passed
    assert result.bad_refs == [99]


def test_check_checks_section_sentences_too():
    section = StructuredSection(
        title_he="פודים אוויריים",
        domain="airborne_pods",
        sentences=[Sentence(text_he="החברה השיקה מוצר חדש בספטמבר.", cites=[42])],
    )
    result = check(_draft(sections=[section]), ITEMS)
    assert not result.passed
    assert any("פודים אוויריים" in e for e in result.errors)
    assert 42 in result.bad_refs


def test_check_section_sentences_pass_when_cites_valid():
    section = StructuredSection(
        title_he="פודים אוויריים",
        domain="airborne_pods",
        sentences=[Sentence(text_he="החברה השיקה מוצר חדש בספטמבר.", cites=[2])],
    )
    result = check(_draft(sections=[section]), ITEMS)
    assert result.passed


def test_check_outlook_assessment_indicator_exempt_from_citation_requirement():
    outlook = [OutlookIndicator(text_he="להערכתנו המגמה תימשך ברבעון הבא.", cites=[], is_assessment=True)]
    result = check(_draft(outlook=outlook), ITEMS)
    assert result.passed


def test_check_outlook_out_of_range_reference_still_flagged():
    outlook = [OutlookIndicator(text_he="המגמה תימשך.", cites=[42])]
    result = check(_draft(outlook=outlook), ITEMS)
    assert not result.passed
    assert 42 in result.bad_refs


def test_check_empty_draft_passes():
    result = check(_draft(summary_cites=None), ITEMS)
    assert result.passed


def test_check_multiple_citations_in_one_sentence_all_validated():
    result = check(_draft(summary_cites=[1, 2]), ITEMS)
    assert result.passed


def test_check_analyst_note_over_three_sentences_rejected_by_schema():
    """The schema itself (AnalystNote.sentences_he, max_length=3) is the primary guard; check()
    still defends the invariant in case a draft was constructed some other way."""
    with_error = None
    try:
        AnalystNote(sentences_he=["א.", "ב.", "ג.", "ד."])
    except Exception as exc:
        with_error = exc
    assert with_error is not None


# --------------------------------------------------------------------------
# F5: exec-summary sentences duplicated verbatim from a section (structured schema)
# --------------------------------------------------------------------------


def test_check_fails_when_summary_copies_section_sentence_verbatim():
    text = "אלביט מערכות זכתה בחוזה בהיקף 50 מיליון דולר לאספקת פודי כיוון."
    section = StructuredSection(
        title_he="פודים אוויריים", domain="airborne_pods", sentences=[Sentence(text_he=text, cites=[1])]
    )
    draft = _draft(sections=[section], summary_text=text)
    result = check(draft, ITEMS)
    assert not result.passed
    assert result.duplicate_sentences
    assert any("מועתק כלשונו" in e for e in result.errors)


def test_check_passes_when_summary_paraphrases_section():
    """A summary sentence that overlaps in subject matter but isn't a verbatim (normalised) copy
    must not be flagged -- only exact duplication is a problem."""
    section = StructuredSection(
        title_he="פודים אוויריים",
        domain="airborne_pods",
        sentences=[
            Sentence(text_he="אלביט מערכות זכתה בחוזה בהיקף 50 מיליון דולר לאספקת פודי כיוון.", cites=[1])
        ],
    )
    draft = _draft(
        sections=[section], summary_text="אלביט מערכות זכתה בחוזה משמעותי לאספקת פודי כיוון החודש."
    )
    result = check(draft, ITEMS)
    assert result.passed
    assert not result.duplicate_sentences


def test_check_fails_when_two_sections_share_a_verbatim_sentence():
    """D6 round-1 fix (docs/qa/loop/round_1_fixes.md, no_duplicate_sentences): the structured path
    used to compare only exec-summary sentences against sections -- a sentence duplicated across
    two *different* sections went undetected."""
    text = "אלביט מערכות זכתה בחוזה בהיקף 50 מיליון דולר לאספקת פודי כיוון."
    section_a = StructuredSection(
        title_he="פודים אוויריים", domain="airborne_pods", sentences=[Sentence(text_he=text, cites=[1])]
    )
    section_b = StructuredSection(
        title_he="תעשייה ישראלית", domain="secondary", sentences=[Sentence(text_he=text, cites=[2])]
    )
    draft = _draft(sections=[section_a, section_b], summary_text="תקציר שאינו קשור לכלל.")
    result = check(draft, ITEMS)
    assert not result.passed
    assert result.duplicate_sentences == [text]
    assert any("תעשייה ישראלית" in e and "פודים אוויריים" in e for e in result.errors)


def test_check_passes_when_two_sections_have_distinct_sentences():
    section_a = StructuredSection(
        title_he="א", domain="d", sentences=[Sentence(text_he="משפט ראשון עם תוכן ייחודי לגמרי.", cites=[1])]
    )
    section_b = StructuredSection(
        title_he="ב", domain="d", sentences=[Sentence(text_he="משפט שני עם תוכן שונה לחלוטין.", cites=[2])]
    )
    draft = _draft(sections=[section_a, section_b], summary_text="תקציר כללי בלבד.")
    result = check(draft, ITEMS)
    assert result.passed
    assert not result.duplicate_sentences


def test_check_duplicate_detection_ignores_short_sentences():
    """A trivial short sentence repeating by coincidence must not be flagged as a duplicate --
    only substantial (>= 4 word) overlaps count."""
    section = StructuredSection(
        title_he="סעיף", domain="d", sentences=[Sentence(text_he="להערכתנו זה חשוב.", cites=[1])]
    )
    draft = _draft(sections=[section], summary_text="להערכתנו זה חשוב.")
    result = check(draft, ITEMS)
    assert not result.duplicate_sentences


# --------------------------------------------------------------------------
# check() -- legacy free-prose shape (weekly/monthly/bd_territory), unchanged behaviour: dispatch
# must still work for a draft that has `exec_summary_he` (not `exec_summary`).
# --------------------------------------------------------------------------


def test_check_legacy_shape_still_supported():
    section = ReportSection(
        title_he="פודים אוויריים", domain="airborne_pods", prose_he="החברה השיקה מוצר חדש בספטמבר [2]."
    )
    draft = MonthlyReportDraftLegacy(
        exec_summary_he="תקציר תקין [1].", sections=[section], outlook_he="", open_points_he=[]
    )
    result = check(draft, ITEMS)
    assert result.passed


def test_check_legacy_shape_still_flags_uncited_sentence():
    draft = MonthlyReportDraftLegacy(exec_summary_he="אלביט זכתה בחוזה של 50 מיליון דולר.", sections=[])
    result = check(draft, ITEMS)
    assert not result.passed
    assert result.uncited_sentences


def test_check_legacy_shape_flags_duplicate_sentence_across_two_sections():
    """D6 round-1 fix (docs/qa/loop/round_1_fixes.md): the legacy free-prose path (weekly/monthly/
    bd_territory) never compared two *sections* against each other, only exec-summary-vs-sections
    -- e.g. two of weekly's trend paragraphs restating the same sentence went undetected."""
    text = "החברה השיקה מוצר חדש בספטמבר [2]."
    section_a = ReportSection(title_he="פודים אוויריים", domain="airborne_pods", prose_he=text)
    section_b = ReportSection(title_he="תעשייה ישראלית", domain="secondary", prose_he=text)
    draft = MonthlyReportDraftLegacy(
        exec_summary_he="תקציר שאינו קשור לכלל [1].",
        sections=[section_a, section_b],
        outlook_he="",
        open_points_he=[],
    )
    result = check(draft, ITEMS)
    assert not result.passed
    assert result.duplicate_sentences == [text]
    assert any("תעשייה ישראלית" in e and "פודים אוויריים" in e for e in result.errors)


def test_check_legacy_shape_extra_sections_cross_checked_for_duplicates():
    """Monthly's trend paragraphs (``extra_sections``) must be cross-checked against each other and
    against ``draft.sections``, not only against the exec summary."""
    text = "מגמת שוק חדשה זוהתה החודש בתחום הרחפנים."
    draft = MonthlyReportDraftLegacy(
        exec_summary_he="תקציר כללי בלבד [1].", sections=[], outlook_he="", open_points_he=[]
    )
    result = check(draft, ITEMS, extra_sections=[("מגמה א", text), ("מגמה ב", text)])
    assert not result.passed
    assert result.duplicate_sentences == [text]
    assert any("מגמה ב" in e and "מגמה א" in e for e in result.errors)
