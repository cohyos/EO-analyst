"""Pydantic schema for the structured BD-territory report draft (A11, round 3, 2026-09-06).

Round-3 migration (D7 judge finding, ``docs/qa/loop/round_2_judge.md``, D7 score 35): the US BD
report was rebuilt 8 times in one day and failed citation QA every time -- root cause named by the
judge was that ``eoa.report.bd_territory`` still asked the LLM for free Hebrew prose
(``exec_summary_he`` / ``market_bullets_he`` / ``BdAction.rationale_he``, all in
``eoa.llm.schemas.reports``) and then post-hoc stripped whatever came back uncited, exactly the
free-prose pattern ``eoa.report.daily``/``eoa.report.weekly`` had already moved away from (commits
b730cfe/85c59c7) in favour of a structured, citations-by-construction schema
(``Sentence{text_he, cites[]}``): an uncited factual claim becomes a *pydantic validation error*
the model must fix (via ``chat_structured``'s existing retry-with-error-message), not something a
regex-based post-hoc QA pass has to notice and strip out of already-generated prose.

This module gives the BD territory report the same treatment -- a NEW schema module (not a change
to ``eoa.llm.schemas.reports.BdTerritoryReportDraft``/``BdAction``, which stay exactly as they were
for any other caller of that legacy free-prose shape; ``eoa.report.bd_territory`` is the only
importer of this module).

Deterministic, non-LLM data (procurement/platform events, open tenders/forecasts, active
competitors, upcoming conferences) is unchanged by this migration -- it was never a schema field to
begin with (rendered as ``eoa.report.docx_builder`` extra sections/tables built straight from the
database, per ``docs/CONVENTIONS.md`` rule 5) and stays that way. Only the parts that need the
model's judgement move to the structured shape: the market synthesis (``exec_summary``/
``market_bullets``), a read on what competitors are doing (``competitor_moves``), and the
recommended actions (each with its own cited ``rationale``).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from eoa.llm.schemas.analysis import AnalystNote, Sentence, StructuredSection

# Small, local copy of ``eoa.llm.schemas.analysis``'s own inline-citation-marker guard -- kept
# local rather than imported (that helper is underscore-private, and the established convention in
# this codebase, e.g. ``eoa.report.weekly``'s module docstring, is a small local copy over a
# cross-module private-name import).
_INLINE_CITE_RE = re.compile(r"\[\s*\d+\s*\]")


def _reject_inline_citation_markers(text: str, *, field_name: str) -> str:
    if _INLINE_CITE_RE.search(text or ""):
        raise ValueError(
            f'{field_name} must not contain a literal "[n]" marker -- put the reference number(s) '
            "in a Sentence's cites field instead; the renderer emits the marker deterministically"
        )
    return text


class BdRecommendedAction(BaseModel):
    """One recommended entry-point/action in a :class:`BdTerritoryReportDraft` ("נקודות כניסה
    ופעולות מומלצות", A11) -- round 3: ``rationale`` moves from a single free-text string
    (``eoa.llm.schemas.reports.BdAction.rationale_he``) to a list of cited
    :class:`~eoa.llm.schemas.analysis.Sentence` objects, so every factual claim behind a
    recommendation carries its own ``[n]`` citation(s) validated against the per-report citation
    registry -- an uncited rationale is a pydantic validation error, not a post-hoc QA finding.
    ``target``/``confidence`` are additive (round 3): the concrete entity/programme/tender/
    conference the action is aimed at, and the analyst's own confidence in the recommendation."""

    action_he: str = Field(description='פעולה מומלצת אחת, משפט קצר וברור, בלי "[n]" בטקסט עצמו')
    priority: str = Field(description='עדיפות: "H" (גבוהה), "M" (בינונית) או "L" (נמוכה) בלבד')
    rationale: list[Sentence] = Field(
        min_length=1,
        description=(
            "נימוק לפעולה כרשימת משפטי Sentence מצוטטים -- לפחות משפט אחד, כל אחד עם cites משלו "
            "לפריט/אירוע/מכרז/כנס קיים ברשימות שסופקו"
        ),
    )
    owner_role_he: str = Field(description='תפקיד אחראי: "מכירות", "פיתוח עסקי" או "שיווק"')
    timing_he: str = Field(description='תזמון מוצע, למשל "מיידי" / "רבעון הקרוב" / "תוך חצי שנה"')
    target: str = Field(
        default="", description="הישות/התוכנית/המכרז/הכנס שהפעולה מכוונת אליו (שם ממשי מהנתונים, אם רלוונטי)"
    )
    confidence: float = Field(
        default=0.7, ge=0.0, le=1.0, description="רמת הביטחון של האנליסט בפעולה זו, בין 0 ל-1"
    )

    @field_validator("action_he")
    @classmethod
    def _validate_action_he(cls, v: str) -> str:
        v = _reject_inline_citation_markers(v, field_name="action_he")
        if not v.strip():
            raise ValueError("action_he must not be empty")
        return v


class BdPipelineOpportunity(BaseModel):
    """One model-proposed row in the "מפת קונים / צינור הזדמנויות" (buyer map / opportunity
    pipeline) table -- round 5 B1 (``docs/REPORT_TEMPLATE_BENCHMARK.md`` sec 3.4 item 5). The
    deterministic majority of that table's rows come straight from tenders/forecasts/procurement
    events (``eoa.report.bd_territory``'s own ``_pipeline_rows_from_*`` helpers, no model
    involved); the model may add up to 3 more rows drawn from the market items it was given, each
    with its own cited ``rationale`` -- validated the same way a :class:`BdRecommendedAction`'s
    ``rationale`` is (a non-empty ``Sentence.cites`` into the citation registry)."""

    opportunity_he: str = Field(
        description=(
            "שם ההזדמנות/התוכנית/הצורך, משפט/צירוף קצר וברור (שם ממשי מהנתונים, לעולם לא כינוי "
            'גנרי כמו "מכרז X"), בלי "[n]" בטקסט עצמו'
        )
    )
    stage: str = Field(description='שלב הרכש: "RFI", "RFP", "הערכה", "החלטה" או "לאחר-זכייה" בלבד')
    buyer_he: str = Field(default="", description="גורם רוכש (לקוח/סוכנות) אם ידוע; מחרוזת ריקה אם לא ידוע")
    target_date_he: str = Field(
        default="—", description='תאריך יעד (דדליין/חלון תחזית) כטקסט קצר; "—" אם אין תאריך ידוע'
    )
    rationale: list[Sentence] = Field(
        min_length=1,
        description=(
            "נימוק להזדמנות זו כרשימת אובייקטי Sentence מצוטטים -- לפחות משפט אחד, מבוסס אך ורק על "
            "אחד מפריטי השוק שסופקו"
        ),
    )

    @field_validator("opportunity_he")
    @classmethod
    def _validate_opportunity_he(cls, v: str) -> str:
        v = _reject_inline_citation_markers(v, field_name="opportunity_he")
        if not v.strip():
            raise ValueError("opportunity_he must not be empty")
        return v


class BdAssumption(BaseModel):
    """One entry in "הנחות והפרכות" -- round 5 B5 (``docs/REPORT_TEMPLATE_BENCHMARK.md`` sec 3.4
    item 10), replacing the old free-text ``risks_assumptions_he`` for new drafts: an explicit key
    assumption behind this report's analysis, paired with the concrete evidence that would falsify
    it (Structured Analytic Techniques' "key assumptions check", per the benchmark doc sec 1 item
    6). ``risks_assumptions_he`` stays on :class:`BdTerritoryReportDraft`, unused by the current
    prompt, only so a legacy persisted draft still round-trips to a valid model."""

    assumption_he: str = Field(description="הנחת מפתח אחת שעליה מבוססת ההערכה/ההמלצות בדוח זה")
    falsifier_he: str = Field(description="מה, אם ייצפה בפועל, יפריך הנחה זו -- עדות קונקרטית וספציפית")
    cites: list[int] = Field(default_factory=list, description="הפניות תומכות אופציונליות (מותר להשאיר ריק)")

    @field_validator("assumption_he", "falsifier_he")
    @classmethod
    def _validate_text(cls, v: str) -> str:
        v = _reject_inline_citation_markers(v, field_name="assumption_he/falsifier_he")
        if not v.strip():
            raise ValueError("assumption_he/falsifier_he must not be empty")
        return v


class BdTerritoryReportDraft(BaseModel):
    """Business-development-by-territory report writer output ("דוח מיקוד לפיתוח עסקי, מכירה
    ושיווק לפי טריטוריה", A11) -- round 3 (2026-09-06) structured migration. ``cites`` on every
    ``Sentence`` refer to the numbered item/event/tender/conference citation registry
    ``eoa.report.bd_territory`` builds for the given territory and lookback window; the model
    never writes "[n]" itself -- ``eoa.report.docx_builder``/this report's own rendering helpers
    emit the marker deterministically from each ``Sentence.cites``.

    Procurement/platform events, open tenders/forecasts, active competitors and upcoming
    conferences are all deterministic, non-LLM data (per ``docs/CONVENTIONS.md`` rule 5) -- never
    fields here, always rendered as extra ``docx_builder`` sections/tables built straight from the
    database. Only the market synthesis, the competitor-moves read, the recommended actions and the
    risk/assumption framing require the model's judgement.
    """

    bluf: list[Sentence] = Field(
        default_factory=list,
        max_length=2,
        description=(
            "שורה תחתונה (BLUF) -- round 5 B/§1 item 1: 1-2 משפטי Sentence בלבד, לפני exec_summary: "
            "הפעולה הדחופה ביותר המומלצת, יחד עם מספר אחד שממחיש את גודל ההזדמנות/האיום בטריטוריה. "
            "אסור לחזור כלשונו על משפט מתוך exec_summary/market_bullets/competitor_moves."
        ),
    )
    exec_summary: list[Sentence] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "עד 8 משפטי Sentence (בדרך כלל 3-5 מספיקים) שמסכמים את התמונה הכוללת בטריטוריה (שוק, "
            "רכש, מתחרים) ואת הפעולה הדחופה ביותר המומלצת; אסור שמשפט יהיה זהה כלשונו למשפט מתוך "
            "market_bullets/competitor_moves/recommended_actions"
        ),
    )
    market_bullets: list[Sentence] = Field(
        default_factory=list,
        description=(
            "5-8 משפטי Sentence, כל אחד מסכם התפתחות אחת בשוק הטריטוריה (תוכנית, רכש, שינוי "
            "רגולטורי, מגמה טכנולוגית) לפי סדר החשיבות"
        ),
    )
    competitor_moves: list[Sentence] = Field(
        default_factory=list,
        description="עד 8 משפטי Sentence על מהלכים של מתחרים פעילים בטריטוריה (זכיות, השקות, שותפויות)",
    )
    sections: list[StructuredSection] = Field(
        default_factory=list,
        description="לא בשימוש בדוח זה (נשמר ריק) -- תואם-טיפוס בלבד עם build_docx/qa_citations",
    )
    recommended_actions: list[BdRecommendedAction] = Field(
        default_factory=list, description="5-8 פעולות מומלצות לפיתוח עסקי/מכירה/שיווק בטריטוריה"
    )
    pipeline_opportunities: list[BdPipelineOpportunity] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "עד 3 שורות נוספות ל'מפת קונים / צינור הזדמנויות' (B1), מעבר לשורות הדטרמיניסטיות "
            "שהקוד כבר בונה ממכרזים/תחזיות/אירועי רכש -- רק הזדמנויות אמיתיות מתוך פריטי השוק שסופקו, "
            "עם נימוק מצוטט לכל אחת"
        ),
    )
    assumptions: list[BdAssumption] = Field(
        default_factory=list,
        max_length=4,
        description="2-4 זוגות 'הנחה <-> מה יפריך אותה' (B5, ראה BdAssumption) -- מחליף את risks_assumptions_he",
    )
    analyst_note_he: AnalystNote | None = Field(
        default=None,
        description='"הערכת האנליסט" -- עד 3 משפטים ללא ציטוט, המקום היחיד בדוח להערכה לא-מבוססת-מקור',
    )
    risks_assumptions_he: str = Field(
        default="",
        description=(
            "שדה legacy (round 5 B5 מחליף אותו ב-assumptions -- אל תמלא שדה זה בדוחות חדשים; נשמר "
            "רק כדי שטיוטה ישנה שנשמרה עם השדה הזה עדיין תיטען לאובייקט תקין). אם בכל זאת ממולא: "
            "2-4 משפטים על סיכונים והנחות, טקסט חופשי, פטור מדרישת ציטוט, [n] מותר אך לא חובה"
        ),
    )
    system_note_he: str = Field(
        default="",
        description=(
            "הודעת מערכת דטרמיניסטית (לעולם לא נכתבת ע\"י המודל -- מוזרקת בקוד): למשל 'אין ממצאים "
            "בתקופה זו' או הודעת כשל אימות אחרי ניסיון תיקון"
        ),
    )
    open_points_he: list[str] = Field(
        default_factory=list, max_length=6, description="עד 6 נקודות פתוחות להכרעת המשתמש"
    )
