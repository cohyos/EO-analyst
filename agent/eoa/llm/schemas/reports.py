"""Pydantic schemas for the weekly/monthly report drafts (LLM-authored prose only).

Trend detection (``eoa.report.trends``), the competitive-landscape players map, the top-events
table, the conference lookahead/horizon, and the FR-11.4 meta-summary are all deterministic,
non-LLM data — never fields on these schemas, always rendered as extra ``docx_builder``
sections/tables built straight from the database, per rule 5 ("Never invent").
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from eoa.llm.schemas.analysis import AnalystNote, OutlookIndicator, ReportSection, Sentence, StructuredSection


class TrendParagraph(BaseModel):
    """One prose paragraph elaborating a single trend detected by ``eoa.report.trends``.

    ``title_he`` is expected to echo (or closely follow) the trend's own ``title_he`` as produced
    by ``eoa.report.trends.detect_trends`` — the model should not invent a new trend, only narrate
    the ones it was given.

    Still used by :class:`MonthlyReportDraftLegacy` (kept only for reading a report persisted
    before round 5 P1, 2026-09-06 — see that class's docstring). Round-2 (2026-09-06) migrated
    :class:`WeeklyReportDraft` to the structured :class:`WeeklyTrendSection` below; round 5 P1
    migrated :class:`MonthlyReportDraft` the same way, to :class:`MonthlyTrendSection`.
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

    ``BdTerritoryReportDraft`` below is unchanged (still legacy free-prose) — out of this scope.
    ``MonthlyReportDraft`` below migrated to the same structured shape in round 5 P1
    (2026-09-06, see that class's docstring) — its own legacy shape is kept as
    :class:`MonthlyReportDraftLegacy`.
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


class MonthlyTrendSection(BaseModel):
    """Round 5 P1 (2026-09-06, docs/REPORT_TEMPLATE_BENCHMARK.md M1+M2): structured replacement
    for the free-prose :class:`TrendParagraph`, used by :class:`MonthlyReportDraft` only. Adds
    month-over-month strength tracking (M2) on top of :class:`WeeklyTrendSection`'s shape:
    ``strength_now`` is this month's 1-5 strength (mirrors ``eoa.report.trends.detect_trends``'s
    own ``strength``); ``strength_prev`` is the *same* trend's strength in the previous monthly
    report if one was found (fed to the drafting prompt as DATA — never invented by the model);
    ``change`` records whether the trend is newly detected this month, strengthening, weakening,
    or (``eoa.report.monthly`` only, never the model — see below) no longer active.

    ``change="gone"`` entries are never written by the model: a trend with no evidence *this*
    month cannot appear in the model's own trend list at all (there is nothing to cite), so
    ``eoa.report.monthly.build_monthly`` appends a ``sentences=[]``/``strength_now=None``/
    ``change="gone"`` entry itself, deterministically, for every previous-month trend title with
    no match this month (rule 5, "never invent" — the model is never asked to assert an absence it
    wasn't shown evidence for).

    ``title_he`` is expected to echo (or closely follow) the trend's own ``title_he`` as produced
    by ``detect_trends`` for a live (non-``gone``) entry. ``domain`` is the taxonomy domain key the
    trend most closely relates to (free-form, not taxonomy-validated the way ``sections[].domain``
    is — see ``eoa.report.weekly._normalize_section_titles``, which only ever touches
    ``draft.sections``). Deliberately named ``trends`` (not ``trend_paragraphs``) on
    :class:`MonthlyReportDraft` below so it is picked up by ``eoa.report.qa_citations``'s and
    ``eoa.report.textnorm.normalize_draft``'s existing, generic ``getattr(draft, "trends", None)``
    handling (both modules are owned by other round-5 packages and are read-only here) — the same
    mechanism that already validates/normalizes :class:`WeeklyTrendSection` above, so a
    month-over-month trend's ``cites`` are checked against the registry exactly like every other
    structured sentence, with no changes needed in either of those two files.
    """

    title_he: str
    domain: str = Field(description="מזהה תחום הטקסונומיה שהמגמה שייכת אליו בעיקר")
    sentences: list[Sentence] = Field(
        default_factory=list,
        max_length=6,
        description=(
            "עד 6 משפטי Sentence המסבירים מדוע זו מגמה ומה משמעותה העסקית/טכנולוגית/מבצעית, כל אחד "
            "עם cites; ריקה רק כאשר change='gone' (מוזרק בקוד, לעולם לא נכתב על ידי המודל)"
        ),
    )
    strength_now: int | None = Field(
        default=None, ge=1, le=5, description="חוזק המגמה החודש, 1-5; null רק כאשר change='gone'"
    )
    strength_prev: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description="חוזק אותה מגמה בדוח החודשי הקודם אם סופק כנתון; null אם המגמה חדשה החודש",
    )
    change: Literal["new", "stronger", "weaker", "gone"] = Field(
        description=(
            "'new' אם המגמה לא הופיעה בדוח החודשי הקודם; 'stronger'/'weaker' לפי strength_now מול "
            "strength_prev; 'gone' -- אך ורק מוזרק בקוד (ר' לעיל), לעולם לא נכתב על ידי המודל"
        )
    )

    @model_validator(mode="after")
    def _validate_change_consistency(self) -> MonthlyTrendSection:
        if self.change == "gone":
            if self.sentences:
                raise ValueError(
                    "a 'gone' trend must not carry sentences -- there is no new evidence to cite"
                )
            if self.strength_now is not None:
                raise ValueError("a 'gone' trend must not have strength_now (no evidence this month)")
        else:
            if not self.sentences:
                raise ValueError("a trend section must include at least one Sentence unless change='gone'")
            if self.strength_now is None:
                raise ValueError("strength_now is required unless change='gone'")
        if self.change == "new" and self.strength_prev is not None:
            raise ValueError("a 'new' trend must not have strength_prev")
        if self.change in ("stronger", "weaker") and self.strength_prev is None:
            raise ValueError(f"change={self.change!r} requires strength_prev")
        if (
            self.change == "stronger"
            and self.strength_prev is not None
            and self.strength_now is not None
            and self.strength_now <= self.strength_prev
        ):
            raise ValueError("change='stronger' requires strength_now > strength_prev")
        if (
            self.change == "weaker"
            and self.strength_prev is not None
            and self.strength_now is not None
            and self.strength_now >= self.strength_prev
        ):
            raise ValueError("change='weaker' requires strength_now < strength_prev")
        return self


class MonthlyReportDraft(BaseModel):
    """Monthly report writer output ("דוח חודשי", FR-5.4: נוף תחרותי מלא ומפת שחקנים).

    Round 5 P1 (2026-09-06, docs/REPORT_TEMPLATE_BENCHMARK.md M1): migrated from the legacy
    free-prose shape (``exec_summary_he``/``sections[].prose_he``/``trend_paragraphs``/
    ``outlook_he``, kept as :class:`MonthlyReportDraftLegacy` below for reading a report persisted
    before this migration) to the same citation-by-construction structured shape the daily/weekly
    reports already use (goal 1 / round-2): every factual claim is a
    :class:`~eoa.llm.schemas.analysis.Sentence` (``text_he`` + non-empty ``cites``), so an uncited
    claim is a pydantic validation error the model must fix, not a post-hoc QA finding.

    Field names deliberately mirror :class:`WeeklyReportDraft` (``exec_summary``, ``trends``,
    ``sections``, ``outlook``, ``system_note_he``, ``analyst_note_he``, ``open_points_he``) rather
    than inventing new ones, so both ``eoa.report.qa_citations.check``
    (``_is_structured_draft``/``_check_structured``) and ``eoa.report.textnorm.normalize_draft`` --
    both owned by other round-5 packages and read-only here -- already validate/normalize this
    shape via their existing, generic duck-typed handling with zero changes needed in either file.
    ``eoa.report.docx_builder._is_legacy_prose_draft`` likewise returns ``False`` for this shape
    (no ``exec_summary_he`` attribute), so the structured renderer path used by the daily/weekly
    reports renders this draft automatically too.

    ``[n]`` refer to the numbered item list built by ``eoa.report.monthly`` for the month (the
    month's red/orange items, extended with any item that only fed a trend's evidence) -- the model
    never writes "[n]" itself; ``cites`` is what produces the marker deterministically.
    """

    exec_summary: list[Sentence] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "עד 8 משפטי Sentence (בדרך כלל 3-5 מספיקים), ברמת פרוזה של אנליסט בכיר, יחד מסכמים "
            "ומקשרים בין ממצאי הסעיפים והמגמות של החודש (נוף תחרותי, מה השתנה, מה לעקוב אחריו); "
            "אסור שמשפט כאן יהיה זהה כלשונו למשפט מתוך גוף אחד הסעיפים/פסקאות המגמה"
        ),
    )
    trends: list[MonthlyTrendSection] = Field(
        default_factory=list,
        max_length=10,
        description="עד 10 מגמות (אחת לכל מגמה שזוהתה החודש) -- ראו MonthlyTrendSection",
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
        default_factory=list, max_length=8, description="עד 8 נקודות פתוחות להכרעת המשתמש"
    )

    @field_validator("sections")
    @classmethod
    def _cap_section_sentences(cls, sections: list[StructuredSection]) -> list[StructuredSection]:
        """Defensive cap (mirrors ``WeeklyReportDraft``'s own): max 8 sentences per domain section
        (a month has more content than a week), truncated (not rejected) so a slightly-over-eager
        resident-model output doesn't burn a retry round-trip over a soft length preference the
        prompt already asks for."""
        capped = []
        for section in sections:
            if len(section.sentences) > 8:
                section = section.model_copy(update={"sentences": section.sentences[:8]})
            capped.append(section)
        return capped


class MonthlyReportDraftLegacy(BaseModel):
    """The pre-round-5-P1 monthly report writer output (free Hebrew prose with the model expected
    to type its own "[n]" markers) -- kept only so a monthly report persisted before 2026-09-06
    round 5 P1 can still be parsed back from its stored ``reports.qa_report``/``path_md`` if ever
    needed. Nothing in the live pipeline constructs this any more; see :class:`MonthlyReportDraft`
    above for the current structured shape."""

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
