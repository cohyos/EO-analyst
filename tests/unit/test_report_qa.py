"""Unit tests for eoa.report.qa_citations: sentence splitting, factual detection, citation QA."""

from __future__ import annotations

from eoa.llm.schemas.analysis import DailyReportDraft, ReportSection
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
# check()
# --------------------------------------------------------------------------

ITEMS = [{"n": 1}, {"n": 2}, {"n": 3}]


def _draft(exec_summary: str, sections=None, outlook: str = "", open_points=None) -> DailyReportDraft:
    return DailyReportDraft(
        exec_summary_he=exec_summary,
        sections=sections or [],
        outlook_he=outlook,
        open_points_he=open_points or [],
    )


def test_check_passes_when_every_factual_sentence_is_cited():
    draft = _draft("אלביט זכתה בחוזה של 50 מיליון דולר [1]. זהו חוזה משמעותי לתחום.")
    result = check(draft, ITEMS)
    assert result.passed
    assert result.errors == []


def test_check_fails_on_uncited_factual_sentence():
    draft = _draft("אלביט זכתה בחוזה של 50 מיליון דולר.")
    result = check(draft, ITEMS)
    assert not result.passed
    assert result.uncited_sentences
    assert any("ללא הפניה" in e for e in result.errors)


def test_check_fails_on_out_of_range_reference():
    draft = _draft("אלביט זכתה בחוזה של 50 מיליון דולר [99].")
    result = check(draft, ITEMS)
    assert not result.passed
    assert result.bad_refs == [99]


def test_check_checks_section_prose_too():
    section = ReportSection(
        title_he="פודים אוויריים", domain="airborne_pods", prose_he="החברה השיקה מוצר חדש בספטמבר."
    )
    draft = _draft("תקציר ללא טענות עובדתיות כלל.", sections=[section])
    result = check(draft, ITEMS)
    assert not result.passed
    assert any("פודים אוויריים" in e for e in result.errors)


def test_check_section_prose_passes_when_cited():
    section = ReportSection(
        title_he="פודים אוויריים", domain="airborne_pods", prose_he="החברה השיקה מוצר חדש בספטמבר [2]."
    )
    draft = _draft("תקציר תקין [1].", sections=[section])
    result = check(draft, ITEMS)
    assert result.passed


def test_check_outlook_exempt_from_citation_requirement():
    draft = _draft("תקציר תקין [1].", outlook="להערכתנו המגמה תימשך ברבעון הבא.")
    result = check(draft, ITEMS)
    assert result.passed


def test_check_outlook_without_assessment_marker_fails():
    draft = _draft("תקציר תקין [1].", outlook="המגמה תימשך ברבעון הבא.")
    result = check(draft, ITEMS)
    assert not result.passed
    assert any("מבט קדימה" in e for e in result.errors)


def test_check_outlook_accepts_all_marker_variants():
    for marker in ("להערכתנו", "נראה ש", "ייתכן"):
        draft = _draft("תקציר תקין [1].", outlook=f"{marker} המגמה תימשך.")
        result = check(draft, ITEMS)
        assert result.passed, f"marker {marker!r} should have been accepted"


def test_check_outlook_out_of_range_reference_still_flagged():
    draft = _draft("תקציר תקין [1].", outlook="ייתכן שהמגמה תימשך [42].")
    result = check(draft, ITEMS)
    assert not result.passed
    assert 42 in result.bad_refs


def test_check_empty_draft_passes():
    draft = _draft("")
    result = check(draft, ITEMS)
    assert result.passed


def test_check_multiple_citations_in_one_sentence_all_validated():
    draft = _draft("אלביט ורפאל חתמו הסכם משותף [1][2].")
    result = check(draft, ITEMS)
    assert result.passed
