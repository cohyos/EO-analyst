"""Unit tests for eoa.report.redundancy (docs/qa/content_review/REPORT-REDUNDANCY.md, 2026-09-08
user feedback on the daily report as shown on the morning page: "the report repeats the
information overview needlessly; the repetition does not advance the consumer of the
information").

Sentence-pair fixtures below are taken verbatim (or near-verbatim, trimmed for length) from the
three live reports the feedback measurement covered: ``output/reports/daily_2026-09-08.md``,
``output/reports/weekly_2026-09-07.md``, ``output/reports/monthly_2026-09-30.md``.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_report_redundancy.py -q``
"""

from __future__ import annotations

from eoa.llm.schemas.analysis import AnalystNote, DailyReportDraft, Sentence, StructuredSection
from eoa.llm.schemas.reports import MonthlyTrendSection, WeeklyReportDraft, WeeklyTrendSection
from eoa.report import redundancy as red

# --------------------------------------------------------------------------
# is_redundant_text -- real sentence pairs from the three live reports
# --------------------------------------------------------------------------


def test_weekly_outlook_vs_indicator_exact_duplicate_is_redundant():
    """output/reports/weekly_2026-09-07.md: byte-identical sentence in 'מבט קדימה' and the
    'מעקב אינדיקטורים' table's 'אינדיקטור' column (Jaccard=1.00 in the redundancy measurement)."""
    a = (
        "נראה שחדירת אנדוריל לשוק הישראלי תאלץ תעשיות מקומיות להאיץ שיתופי פעולה בתחום הפיקוד "
        "המבוזר כדי למנוע זליגת פרויקטים אסטרטגיים לחברות זרות."
    )
    b = a
    assert red.is_redundant_text(a, b)


def test_weekly_ophir_deep_search_vs_israel_table_near_duplicate_is_redundant():
    """output/reports/weekly_2026-09-07.md: the Ophir MWIR-zoom so-what sentence appears near
    verbatim in a deep-search investigation's context AND in the 'תעשייה ישראלית' table's 'מה זה
    אומר' cell (measured Jaccard=0.68)."""
    a = (
        "להערכתנו המוצר ממצב את Ophir בפלח עדשות הזום ה-MWIR ארוכות הטווח לגלאי 10 µm SXGA, שדה "
        "שבו הביקוש גדל ככל שמערכות ISR ומגדלי תצפית גבוליים עוברים לגלאי רזולוציה גבוהה."
    )
    b = (
        "להערכתנו המוצר ממצב את Ophir בפלח עדשות הזום ה-MWIR ארוכות הטווח לגלאי 10 µm SXGA, שדה "
        "שבו הביקוש גדל ככל שמערכות ISR ומגדלי תצפית גבוליים עוברים לגלאי רזולוציה"
    )
    assert red.is_redundant_text(a, b)


def test_monthly_infrapatch_trend_vs_section_near_duplicate_is_redundant():
    """output/reports/monthly_2026-09-30.md: the InfraPatch vulnerability fact reworded between
    'מגמות החודש' and 'סקירה לפי תחום' (measured Jaccard=0.68)."""
    a = (
        "מחקר נפרד מזהיר מפני פגיעות של 86%-100% הצלחה במודלים מולטימודליים אינפרא-אדומים "
        "(IR-VLM) מול מתקפות פאצ׳ים ממוקדות."
    )
    b = (
        "מחקר InfraPatch מזהיר מפני פגיעות של מודלים מולטימודליים אינפרא-אדומים (IR-VLM) "
        "בשיעורי הצלחה של 86%-100% מול מתקפות פאצ׳ים אפורים."
    )
    assert red.is_redundant_text(a, b)


def test_monthly_redrone_trend_vs_section_different_wording_same_fact_is_redundant():
    """output/reports/monthly_2026-09-30.md: same ReDrone order restated with different supplier
    subsidiary wording (אלביט vs אלישרא) -- still the same fact, still >= 0.6 Jaccard on the
    surrounding text (measured 0.62)."""
    a = (
        "משרד ההגנה ההולנדי הזמין 6 מערכות ReDrone נוספות מאלביט, המשלבות יכולות יירוט פיזי של "
        "רחפנים לצד לוחמה אלקטרונית."
    )
    b = (
        "משרד ההגנה ההולנדי הזמין 6 מערכות ReDrone נוספות מאלישרא, המשלבות מיירטים פיזיים לצד "
        "יכולות לוחמה אלקטרונית קיימות."
    )
    assert red.is_redundant_text(a, b)


def test_daily_aerovironment_question_vs_forecast_rationale_matched_by_number_and_org():
    """output/reports/daily_2026-09-08.md: the deep-search investigation QUESTION and the tender-
    forecast rationale cell share the distinctive $464.8M figure and the AeroVironment org token,
    even though their wording (and word-level Jaccard, ~0.24) differs -- the number+org rule
    (spec: 'same numbers + same organisation') is what catches this pair."""
    a = (
        "מהם פרטי חוזה הייצור של AeroVironment בסך 464.8 מיליון דולר עבור מערכת הלייזר LOCUST X3, "
        "כולל מספר היחידות המוזמנות, לוח הזמנים לאספקה וזהות משתמשי הקצה הצבאיים?"
    )
    b = (
        "צבא ארה״ב מקדם באופן פעיל תוכנית לייזר אנרגיה מכוונת (E-HEL) לנטרול כטב״מים, כאשר חוזה "
        "הייצור הראשון בהיקף 464.8 מיליון דולר הוענק לחברת AeroVironment עבור מערכת Locust X3."
    )
    assert red.is_redundant_text(a, b)


def test_unrelated_daily_sentences_are_not_redundant():
    """Negative control: two genuinely distinct facts from the same daily report must not match."""
    a = "צבא ארה״ב מדווח כי נשק לייזר של AeroVironment בעלות 3 דולר לירי צמצם ב-75% את טיסות רחפני הקרטלים בגבול הדרומי."
    b = "ARMMO Defense Technologies מספרד חשפה בתערוכת Eurosatory כלי שיט בלתי מאויש מדגם ARW39CAT-A."
    assert not red.is_redundant_text(a, b)


def test_shared_bare_year_alone_is_not_a_distinctive_number_match():
    """Two sentences that only share a calendar year (and no org) must not be flagged -- years are
    endemic in this corpus and would otherwise swamp the number+org rule with false positives."""
    a = "בשנת 2026 נחתם חוזה חדש עם ספק אירופאי."
    b = "בשנת 2026 פורסמה תחזית מכרזים נפרדת לגמרי בתחום אחר."
    assert not red.is_redundant_text(a, b)


def test_shared_org_without_shared_number_is_not_redundant():
    a = "אלביט מערכות זכתה בחוזה חדש בארה״ב."
    b = "אלביט מערכות פתחה מפעל בסרביה ללא קשר לחוזה האמריקאי."
    assert not red.is_redundant_text(a, b)


# --------------------------------------------------------------------------
# apply_redundancy_pass -- DailyReportDraft (no trends field)
# --------------------------------------------------------------------------


def _daily_draft(**overrides):
    base = dict(
        bluf=[Sentence(text_he="אלביט זכתה בחוזה של 50 מיליון דולר להספקת מערכות ReDrone להולנד.", cites=[1])],
        exec_summary=[],
        sections=[],
        analyst_note_he=None,
    )
    base.update(overrides)
    return DailyReportDraft(**base)


def test_exec_summary_sentence_restating_bluf_is_dropped_and_cites_merged_into_bluf():
    draft = _daily_draft(
        exec_summary=[
            Sentence(text_he="אלביט זכתה בחוזה של 50 מיליון דולר מהולנד להספקת ReDrone.", cites=[2]),
            Sentence(text_he="זהו פיתוח נפרד לגמרי, ללא קשר לחוזה ההולנדי.", cites=[3]),
        ],
    )
    new_draft, result = red.apply_redundancy_pass(draft, report_kind="daily")

    assert [s.text_he for s in new_draft.exec_summary] == ["זהו פיתוח נפרד לגמרי, ללא קשר לחוזה ההולנדי."]
    assert result.n_dropped == 1
    assert result.dropped[0].section_label == "תקציר מנהלים"
    assert result.dropped[0].kept_section_label == "שורה תחתונה"
    # the dropped sentence's own citation (2) is merged into the surviving BLUF sentence
    assert new_draft.bluf[0].cites == [1, 2]


def test_domain_review_sentence_restating_exec_summary_is_dropped():
    draft = _daily_draft(
        bluf=[Sentence(text_he="עדשת זום MWIR חדשה הוצגה על ידי Ophir בתערוכה אירופאית.", cites=[9])],
        exec_summary=[Sentence(text_he="אלביט זכתה בחוזה של 50 מיליון דולר מהולנד להספקת ReDrone.", cites=[2])],
        sections=[
            StructuredSection(
                title_he="נגד כטב\"מים",
                domain="c_uas",
                sentences=[
                    Sentence(text_he="ההולנדים הזמינו מאלביט מערכות ReDrone ב-50 מיליון דולר.", cites=[3]),
                    Sentence(text_he="מידע חדש לגמרי שלא הופיע בשום סעיף אחר, על מכ\"ם נפרד.", cites=[4]),
                ],
            )
        ],
    )
    new_draft, result = red.apply_redundancy_pass(draft, report_kind="daily")

    remaining = [s.text_he for s in new_draft.sections[0].sentences]
    assert remaining == ["מידע חדש לגמרי שלא הופיע בשום סעיף אחר, על מכ\"ם נפרד."]
    assert result.n_dropped == 1
    assert result.dropped[0].section_label.startswith("סקירה לפי תחום")


def test_domain_review_section_emptied_out_gets_pointer_sentence_with_merged_cites():
    draft = _daily_draft(
        exec_summary=[Sentence(text_he="אלביט זכתה בחוזה של 50 מיליון דולר מהולנד להספקת ReDrone.", cites=[2])],
        sections=[
            StructuredSection(
                title_he="נגד כטב\"מים",
                domain="c_uas",
                sentences=[
                    Sentence(text_he="ההולנדים הזמינו מאלביט מערכות ReDrone ב-50 מיליון דולר.", cites=[3]),
                ],
            )
        ],
    )
    new_draft, result = red.apply_redundancy_pass(draft, report_kind="daily")

    section = new_draft.sections[0]
    assert len(section.sentences) == 1
    assert section.sentences[0].text_he == red.POINTER_SENTENCE_HE
    assert section.sentences[0].cites == [3]
    assert result.pointer_sections == ['סקירה לפי תחום > נגד כטב"מים']


def test_analyst_note_sentence_restating_bluf_is_dropped_no_cites_to_merge():
    draft = _daily_draft(
        analyst_note_he=AnalystNote(
            sentences_he=[
                "אלביט זכתה בחוזה של 50 מיליון דולר להספקת מערכות ReDrone להולנד.",
                "הערכה חדשה ומקורית שלא הופיעה בשום מקום אחר בדוח.",
            ]
        ),
    )
    new_draft, result = red.apply_redundancy_pass(draft, report_kind="daily")

    assert new_draft.analyst_note_he.sentences_he == ["הערכה חדשה ומקורית שלא הופיעה בשום מקום אחר בדוח."]
    assert result.n_dropped == 1
    assert result.dropped[0].section_label == "הערכת האנליסט"


def test_non_redundant_draft_is_returned_unchanged_and_nothing_dropped():
    draft = _daily_draft(
        exec_summary=[Sentence(text_he="זהו פיתוח נפרד לגמרי, ללא שום קשר לפריט המוביל.", cites=[9])],
    )
    new_draft, result = red.apply_redundancy_pass(draft, report_kind="daily")

    assert result.n_dropped == 0
    assert result.pointer_sections == []
    assert [s.text_he for s in new_draft.exec_summary] == [s.text_he for s in draft.exec_summary]


# --------------------------------------------------------------------------
# apply_redundancy_pass -- WeeklyReportDraft (trends tier, priority over sections)
# --------------------------------------------------------------------------


def test_weekly_trends_outrank_sections_and_gain_the_merged_citation():
    draft = WeeklyReportDraft(
        bluf=[Sentence(text_he="הולנד מזמינה מערכות נוספות מאלביט.", cites=[1])],
        exec_summary=[],
        trends=[
            WeeklyTrendSection(
                title_he="נגד כטב\"מים",
                sentences=[
                    Sentence(
                        text_he="משרד ההגנה ההולנדי הזמין 6 מערכות ReDrone נוספות מאלביט, המשלבות יכולות יירוט פיזי לצד לוחמה אלקטרונית.",
                        cites=[2],
                    )
                ],
            )
        ],
        sections=[
            StructuredSection(
                title_he="נגד כטב\"מים",
                domain="c_uas",
                sentences=[
                    Sentence(
                        text_he="משרד ההגנה ההולנדי הזמין 6 מערכות ReDrone נוספות מאלישרא, המשלבות מיירטים פיזיים לצד יכולות לוחמה אלקטרונית קיימות.",
                        cites=[3],
                    )
                ],
            )
        ],
    )
    new_draft, result = red.apply_redundancy_pass(draft, report_kind="weekly")

    assert result.n_dropped == 1
    assert result.dropped[0].section_label.startswith("סקירה לפי תחום")
    assert result.dropped[0].kept_section_label.startswith("מגמות")
    trend_sentence = new_draft.trends[0].sentences[0]
    assert trend_sentence.cites == [2, 3]
    # emptied section got its pointer sentence
    assert new_draft.sections[0].sentences[0].text_he == red.POINTER_SENTENCE_HE
    assert new_draft.sections[0].sentences[0].cites == [3]


def test_monthly_gone_trend_with_no_sentences_is_left_untouched():
    """A MonthlyTrendSection with change='gone' carries sentences=[] by construction
    (eoa.report.monthly._gone_trend_sections) -- the pass must pass it through unchanged, never
    treat it as 'emptied out by this pass' (which would wrongly add a pointer sentence and break
    the schema's own 'gone trends carry no sentences' validator)."""
    from eoa.llm.schemas.reports import MonthlyReportDraft

    gone_trend = MonthlyTrendSection(
        title_he="מגמה שנעלמה", domain="c_uas", sentences=[], strength_now=None, strength_prev=3, change="gone"
    )
    draft = MonthlyReportDraft(
        bluf=[Sentence(text_he="עובדה כלשהי.", cites=[1])],
        exec_summary=[],
        trends=[gone_trend],
        sections=[],
    )
    new_draft, result = red.apply_redundancy_pass(draft, report_kind="monthly")

    assert new_draft.trends[0].change == "gone"
    assert new_draft.trends[0].sentences == []
    assert result.pointer_sections == []


# --------------------------------------------------------------------------
# narrative_citation_numbers
# --------------------------------------------------------------------------


def test_narrative_citation_numbers_collects_bluf_and_exec_summary_only():
    draft = DailyReportDraft(
        bluf=[Sentence(text_he="א", cites=[1, 2])],
        exec_summary=[Sentence(text_he="ב", cites=[3])],
        sections=[
            StructuredSection(
                title_he="ת", domain="d", sentences=[Sentence(text_he="ג", cites=[99])]
            )
        ],
    )
    assert red.narrative_citation_numbers(draft) == {1, 2, 3}


# --------------------------------------------------------------------------
# filter_facts_against_narrative -- deep-search key_facts vs the kept narrative pool
# --------------------------------------------------------------------------


def test_filter_facts_against_narrative_drops_only_the_restated_fact():
    """output/reports/weekly_2026-09-07.md-style fixture: one deep-search key fact restates a
    sentence already kept in the narrative (the Ophir so-what pair above); a second, distinct fact
    survives."""
    narrative = [
        "להערכתנו המוצר ממצב את Ophir בפלח עדשות הזום ה-MWIR ארוכות הטווח לגלאי 10 µm SXGA, שדה "
        "שבו הביקוש גדל ככל שמערכות ISR ומגדלי תצפית גבוליים עוברים לגלאי רזולוציה גבוהה."
    ]
    facts = [
        "המוצר ממצב את Ophir בפלח עדשות זום MWIR ארוכות טווח לגלאי 10 מיקרון SXGA, בשוק שבו הביקוש גדל.",
        "העדשה מיועדת למוקד נייד עד 1200 מ\"מ וניתנת להרחבה עד למרחקים ארוכים יותר באמצעות מתאמים.",
    ]
    kept, n_dropped = red.filter_facts_against_narrative(facts, narrative)

    assert n_dropped == 1
    assert kept == [facts[1]]


def test_filter_facts_against_narrative_keeps_everything_when_nothing_overlaps():
    narrative = ["עובדה לגמרי לא קשורה על תחום אחר."]
    facts = ["עובדה ראשונה חדשה.", "עובדה שנייה חדשה וגם היא ללא קשר."]
    kept, n_dropped = red.filter_facts_against_narrative(facts, narrative)
    assert n_dropped == 0
    assert kept == facts


def test_filter_facts_against_narrative_handles_empty_inputs():
    assert red.filter_facts_against_narrative([], ["x"]) == ([], 0)
    assert red.filter_facts_against_narrative(["y"], []) == (["y"], 0)
