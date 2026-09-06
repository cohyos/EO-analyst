"""Pydantic schemas for the weekly/monthly report drafts (LLM-authored prose only).

Trend detection (``eoa.report.trends``), the competitive-landscape players map, the top-events
table, the conference lookahead/horizon, and the FR-11.4 meta-summary are all deterministic,
non-LLM data — never fields on these schemas, always rendered as extra ``docx_builder``
sections/tables built straight from the database, per rule 5 ("Never invent").
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from eoa.llm.schemas.analysis import AnalystNote, OutlookIndicator, ReportSection, Sentence, StructuredSection


class TrendParagraph(BaseModel):
    """One prose paragraph elaborating a single trend detected by ``eoa.report.trends``.

    ``title_he`` is expected to echo (or closely follow) the trend's own ``title_he`` as produced
    by ``eoa.report.trends.detect_trends`` — the model should not invent a new trend, only narrate
    the ones it was given.

    Still used by :class:`MonthlyReportDraft` (unchanged, legacy free-prose shape). Round-2
    (2026-09-06) migrated :class:`WeeklyReportDraft` only to the structured
    :class:`WeeklyTrendSection` below — see that class's docstring for why.
    """

    title_he: str
    prose_he: str = Field(description="פרוזה רהוטה עם הפניות [n] לפריטי המקור שביססו את המגמה")


class WeeklyTrendSection(BaseModel):
    """Round-2 (2026-09-06) structured replacement for :class:`TrendParagraph`, used by
    :class:`WeeklyReportDraft` only. ``title_he`` is expected to echo (or closely follow) the
    trend's own ``title_he`` as produced by ``eoa.report.trends.detect_trends`` — the model should
    not invent a new trend, only narrate the ones it was given. ``sentences`` is capped at 5
    (``eoa.report.qa_citations._check_structured`` validates every ``cites`` entry and cross-checks
    for verbatim duplication exactly like a ``StructuredSection``, since both are appended to the
    same duplicate-detection group list)."""

    title_he: str
    sentences: list[Sentence] = Field(
        default_factory=list,
        max_length=5,
        description=(
            "עד 5 משפטי Sentence המסבירים מדוע זו מגמה ומה משמעותה העסקית/טכנולוגית/מבצעית, כל אחד "
            "עם cites לראיות שסופקו לאותה מגמה"
        ),
    )


class WeeklyReportDraft(BaseModel):
    """Weekly report writer output ("דוח שבועי", FR-5.4). ``cites``/``sentences`` refer to the
    numbered item list given in the prompt (the week's red/orange items, extended with any item
    that only fed a trend's evidence) — the model never writes "[n]" itself.

    Round-2 (2026-09-06): migrated from free-prose (``exec_summary_he``/``sections[].prose_he``/
    ``trend_paragraphs``) to the daily report's structured, sentence-per-claim shape (goal 1) —
    two live rebuilds ran away to 24k- then 54k-char JSON and hit EOF mid-string on the old
    free-prose schema; splitting every claim into its own bounded ``Sentence`` object keeps each
    individual field small regardless of total report length, and turns an uncited claim into a
    pydantic validation error the model must fix instead of a post-hoc QA finding. ``[n]`` markers
    are never written by the model — ``eoa.report.docx_builder`` (sections/exec_summary/outlook/
    analyst_note, duck-typed, unchanged) and ``eoa.report.weekly``'s own small local helper (for
    ``trends``, still rendered via the ``extra_sections`` hook) emit them deterministically from
    each ``Sentence.cites``.

    ``MonthlyReportDraft``/``BdTerritoryReportDraft`` below are unchanged (still legacy free-prose)
    — out of round-2's scope.
    """

    exec_summary: list[Sentence] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "עד 8 משפטי Sentence (3-5 בדרך כלל מספיקים) המסכמים ומקשרים בין ממצאי הסעיפים והמגמות "
            "של השבוע (מה השתנה, למה זה חשוב, מה לעקוב אחריו); אסור שמשפט יהיה זהה כלשונו למשפט "
            "מתוך סעיף/פסקת מגמה"
        ),
    )
    trends: list[WeeklyTrendSection] = Field(
        default_factory=list,
        max_length=8,
        description="עד 8 מגמות (אחת לכל מגמה שזוהתה השבוע) — ראו WeeklyTrendSection",
    )
    sections: list[StructuredSection] = Field(default_factory=list, description="פרקים לפי תחום")
    system_note_he: str = Field(
        default="",
        description=(
            "הודעת מערכת דטרמיניסטית (לעולם לא נכתבת ע\"י המודל -- מוזרקת בקוד): למשל 'אין ממצאים "
            "בתקופה זו' או הודעת כשל אימות אחרי ניסיון תיקון -- מוצגת כפרוזה רגילה, ללא תווית ובלי "
            "דרישת cites"
        ),
    )
    analyst_note_he: AnalystNote | None = Field(
        default=None,
        description='"הערכת האנליסט" -- עד 3 משפטים ללא ציטוט, המקום היחיד בדוח להערכה לא-מבוססת-מקור',
    )
    outlook: list[OutlookIndicator] = Field(
        default_factory=list,
        max_length=4,
        description="2-4 אינדיקטורים קונקרטיים למעקב ב'מבט קדימה', כל אחד מצוטט או מסומן כהערכת אנליסט",
    )
    open_points_he: list[str] = Field(
        default_factory=list, max_length=6, description="עד 6 נקודות פתוחות להכרעת המשתמש"
    )

    @field_validator("sections")
    @classmethod
    def _cap_section_sentences(cls, sections: list[StructuredSection]) -> list[StructuredSection]:
        """Defensive cap (round-2): max 6 sentences per domain section, truncated (not rejected) so
        a slightly-over-eager resident-model output doesn't burn a retry round-trip on the shared,
        GPU-contended resident model over a soft length preference — the prompt already asks for
        this cap, this only defends against it being ignored."""
        capped = []
        for section in sections:
            if len(section.sentences) > 6:
                section = section.model_copy(update={"sentences": section.sentences[:6]})
            capped.append(section)
        return capped


class MonthlyReportDraft(BaseModel):
    """Monthly report writer output ("דוח חודשי", FR-5.4: נוף תחרותי מלא ומפת שחקנים). ``[n]``
    refer to the numbered item list given in the prompt (the month's red/orange items, extended
    with any item that only fed a trend's evidence)."""

    exec_summary_he: str = Field(
        description=(
            "3-5 משפטים בלבד, המסכמים ומקשרים בין ממצאי הסעיפים והמגמות של החודש (נוף תחרותי, מה "
            "השתנה, מה לעקוב אחריו), עם [n]; אסור להעתיק משפט כלשונו מגוף אחד הסעיפים/פסקאות המגמה"
        )
    )
    trend_paragraphs: list[TrendParagraph] = Field(
        default_factory=list, description="פסקה אחת לכל מגמה שזוהתה החודש"
    )
    sections: list[ReportSection] = Field(default_factory=list, description="פרקים לפי תחום")
    outlook_he: str = Field(default="", description="מבט קדימה קצר, פותח במילת הערכה מפורשת")
    open_points_he: list[str] = Field(default_factory=list, description="נקודות פתוחות להכרעת המשתמש")


class BdAction(BaseModel):
    """One recommended entry-point/action in a :class:`BdTerritoryReportDraft` ("נקודות כניסה
    ופעולות מומלצות", A11) -- structured so the report can render a table (priority/owner/timing)
    while ``rationale_he`` still goes through the same ``[n]`` citation QA gate as any other
    LLM-authored prose (``eoa.report.qa_citations.check``'s ``extra_sections`` hook)."""

    action_he: str = Field(description="פעולה מומלצת אחת, משפט קצר וברור")
    priority: str = Field(description='עדיפות: "H" (גבוהה), "M" (בינונית) או "L" (נמוכה) בלבד')
    rationale_he: str = Field(description="נימוק לפעולה, משפט אחד עד שניים, עם הפניות [n] לכל טענה עובדתית")
    owner_role_he: str = Field(description='תפקיד אחראי: "מכירות", "פיתוח עסקי" או "שיווק"')
    timing_he: str = Field(description='תזמון מוצע, למשל "מיידי" / "רבעון הקרוב" / "תוך חצי שנה"')


class BdTerritoryReportDraft(BaseModel):
    """Business-development-by-territory report writer output ("דוח מיקוד לפיתוח עסקי, מכירה
    ושיווק לפי טריטוריה", A11). ``[n]`` refer to the numbered item/event/tender citation registry
    built by ``eoa.report.bd_territory`` for the given territory and lookback window.

    Procurement/platform events, open tenders/forecasts, active competitors and upcoming
    conferences are all deterministic, non-LLM data (per docs/CONVENTIONS.md rule 5, same
    convention as ``eoa.report.trends``/the players map in this module's sibling schemas) --
    never fields here, always rendered as extra ``docx_builder`` sections/tables built straight
    from the database. Only the market synthesis, the recommended actions and the risk/assumption
    framing require the model's judgement.
    """

    exec_summary_he: str = Field(
        description=(
            "3-5 משפטים בלבד המסכמים את התמונה הכוללת בטריטוריה (שוק, רכש, מתחרים) ואת הפעולה "
            "הדחופה ביותר המומלצת, עם [n]; אסור להעתיק משפט כלשונו מתוך הבולטים או הפעולות המומלצות"
        )
    )
    market_bullets_he: list[str] = Field(
        default_factory=list,
        description=(
            "5-8 בולטים, כל אחד משפט מלא אחד (מסתיים בנקודה) המסכם התפתחות אחת בשוק הטריטוריה "
            "לפי תחום, עם [n]"
        ),
    )
    sections: list[ReportSection] = Field(
        default_factory=list,
        description="לא בשימוש בדוח זה (נשמר ריק) -- תואם-טיפוס בלבד עם build_docx/qa_citations",
    )
    recommended_actions: list[BdAction] = Field(
        default_factory=list, description="5-8 פעולות מומלצות לפיתוח עסקי/מכירה/שיווק בטריטוריה"
    )
    risks_assumptions_he: str = Field(
        default="",
        description="סיכונים והנחות בבניית הדוח (כיסוי מקורות, פערי מידע וכו'), [n] היכן שרלוונטי",
    )
    outlook_he: str = Field(
        default="", description="לא בשימוש בדוח זה (נשמר ריק) -- תואם-טיפוס בלבד עם build_docx"
    )
    open_points_he: list[str] = Field(default_factory=list, description="נקודות פתוחות להכרעת המשתמש")
