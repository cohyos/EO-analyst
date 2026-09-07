"""Pydantic schema for the structured product-line status & business-development report draft
(PL-backend, user request 2026-09-07).

Modeled directly on ``eoa.llm.schemas.bd_territory`` (round 3's citations-by-construction
migration for the BD-territory report): every factual claim is a
:class:`~eoa.llm.schemas.analysis.Sentence` (``{text_he, cites[]}``) rather than free prose with a
model-typed ``"[n]"`` marker -- an uncited claim is a pydantic validation error the model must fix
(``chat_structured``'s existing retry-with-error-message), not something a post-hoc QA pass has to
notice and strip.

A NEW schema module (not a change to ``eoa.llm.schemas.bd_territory``, which stays exactly as it is
for the BD-territory report -- ``eoa.report.product_line`` is the only importer of this module).
Deterministic, non-LLM data (market items/events tables, open tenders/forecasts, patents, the buyer
pipeline) is rendered by ``eoa.report.product_line`` straight from the database, per
``docs/CONVENTIONS.md`` rule 5 -- never a schema field here.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from eoa.llm.schemas.analysis import AnalystNote, Sentence, StructuredSection, validate_bluf_length

_INLINE_CITE_RE = re.compile(r"\[\s*\d+\s*\]")


class ProductLineTagResult(BaseModel):
    """Per-item output of LLM-assisted product-line tagging (R8-tagging, 2026-09-07) --
    :func:`eoa.product_lines.llm_tagging.llm_tag_batch` sends up to ~15 items per
    ``chat_structured_batch`` call (``item_id`` is injected/stripped by that helper, not a field
    here); the model is given the closed six product-line ids + their names/keywords from
    ``config/product_lines.yaml`` and asked to pick zero or more that genuinely apply to THIS
    item's title/summary alone (never inferring from other items in the same batch).

    ``line_ids`` is validated as a *plausible* list here (non-empty strings, no duplicates) but NOT
    checked against the live six-id catalog -- that check happens in
    :mod:`eoa.product_lines.llm_tagging` (which already imports ``eoa.product_lines.registry`` for
    the prompt's own id list) so this schema module stays a leaf, matching every other module in
    ``eoa.llm.schemas`` (none of them import ``eoa.product_lines``/``eoa.config``).
    ``confidence`` is the model's own confidence in this item's whole tag set; the caller only
    accepts a result at ``confidence >= eoa.product_lines.llm_tagging.LLM_TAG_MIN_CONFIDENCE``
    (0.6) -- a low-confidence guess is treated the same as "no tag" rather than persisted.
    """

    line_ids: list[str] = Field(
        default_factory=list,
        description=(
            "קווי המוצר (0 ומעלה) מתוך רשימת ה-id הסגורה שניתנה, שרלוונטיים לפריט הזה בלבד. "
            "רשימה ריקה [] אם אף קו מוצר לא רלוונטי -- אסור להמציא id שאינו ברשימה שניתנה."
        ),
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="רמת ביטחון כוללת (0-1) בתיוג קווי המוצר של הפריט הזה",
    )

    @field_validator("line_ids")
    @classmethod
    def _dedupe_and_validate(cls, v: list[str]) -> list[str]:
        cleaned = [s.strip() for s in v if s and s.strip()]
        # Preserve order while de-duplicating -- a model repeating an id twice should not be
        # treated as a schema error (harmless), just normalized.
        seen: set[str] = set()
        out: list[str] = []
        for s in cleaned:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out


def _reject_inline_citation_markers(text: str, *, field_name: str) -> str:
    if _INLINE_CITE_RE.search(text or ""):
        raise ValueError(
            f'{field_name} must not contain a literal "[n]" marker -- put the reference number(s) '
            "in a Sentence's cites field instead; the renderer emits the marker deterministically"
        )
    return text


class ProductLineRecommendedAction(BaseModel):
    """One recommended business-development action ("פעולות מומלצות") for this product line --
    mirrors ``eoa.llm.schemas.bd_territory.BdRecommendedAction`` field-for-field."""

    action_he: str = Field(description='פעולה מומלצת אחת, משפט קצר וברור, בלי "[n]" בטקסט עצמו')
    priority: str = Field(description='עדיפות: "H" (גבוהה), "M" (בינונית) או "L" (נמוכה) בלבד')
    rationale: list[Sentence] = Field(
        min_length=1,
        description="נימוק לפעולה כרשימת משפטי Sentence מצוטטים -- לפחות משפט אחד, כל אחד עם cites משלו",
    )
    owner_role_he: str = Field(description='תפקיד אחראי: "מכירות", "פיתוח עסקי" או "שיווק"')
    timing_he: str = Field(description='תזמון מוצע, למשל "מיידי" / "רבעון הקרוב" / "תוך חצי שנה"')
    target: str = Field(default="", description="הישות/התוכנית/המכרז/המתחרה שהפעולה מכוונת אליו")
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("action_he")
    @classmethod
    def _validate_action_he(cls, v: str) -> str:
        v = _reject_inline_citation_markers(v, field_name="action_he")
        if not v.strip():
            raise ValueError("action_he must not be empty")
        return v


class ProductLineAssumption(BaseModel):
    """One "הנחות והפרכות" entry -- mirrors ``eoa.llm.schemas.bd_territory.BdAssumption``."""

    assumption_he: str = Field(description="הנחת מפתח אחת שעליה מבוססת ההערכה/ההמלצות בדוח זה")
    falsifier_he: str = Field(description="מה, אם ייצפה בפועל, יפריך הנחה זו -- עדות קונקרטית וספציפית")
    cites: list[int] = Field(default_factory=list, description="הפניות תומכות אופציונליות (מותר להשאיר ריק)")

    @field_validator("assumption_he", "falsifier_he")
    @classmethod
    def _validate_text(cls, v: str) -> str:
        v = _reject_inline_citation_markers(v, field_name="assumptions[].assumption_he/falsifier_he")
        if not v.strip():
            raise ValueError("assumption_he/falsifier_he must not be empty")
        return v


class ProductLineReportDraft(BaseModel):
    """Product-line status & business-development report writer output (PL-backend). ``cites`` on
    every ``Sentence`` refer to the numbered item/event/tender/patent citation registry
    ``eoa.report.product_line`` builds for the given line and lookback window; the model never
    writes "[n]" itself -- ``eoa.report.docx_builder``/this report's own rendering helpers emit the
    marker deterministically from each ``Sentence.cites``.

    Market/procurement items, open tenders/forecasts, patents, the buyer-pipeline tiers and the
    Israeli-industry positioning table are all deterministic, non-LLM data -- never fields here,
    always rendered as extra ``docx_builder`` sections/tables built straight from the database. Only
    the market synthesis, the competitor-moves read, the recommended actions and the risk/assumption
    framing require the model's judgement."""

    bluf: list[Sentence] = Field(
        default_factory=list,
        max_length=2,
        description=(
            "שורה תחתונה (BLUF) -- 1-2 משפטי Sentence בלבד, לפני exec_summary: הפעולה הדחופה ביותר "
            "המומלצת, יחד עם מספר אחד שממחיש את גודל ההזדמנות/האיום בקו המוצר. אסור לחזור כלשונו על "
            "משפט מתוך exec_summary/market_bullets/competitor_moves."
        ),
    )
    exec_summary: list[Sentence] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "עד 8 משפטי Sentence (בדרך כלל 3-5 מספיקים) שמסכמים את התמונה הכוללת בקו המוצר (שוק, "
            "רכש, מתחרים) ואת הפעולה הדחופה ביותר המומלצת; אסור שמשפט יהיה זהה כלשונו למשפט מתוך "
            "market_bullets/competitor_moves/recommended_actions"
        ),
    )
    market_bullets: list[Sentence] = Field(
        default_factory=list,
        description="5-8 משפטי Sentence, כל אחד מסכם התפתחות אחת בשוק קו המוצר, לפי סדר החשיבות",
    )
    competitor_moves: list[Sentence] = Field(
        default_factory=list,
        description="עד 8 משפטי Sentence על מהלכים של מתחרים פעילים בקו המוצר (זכיות, השקות, שותפויות)",
    )
    sections: list[StructuredSection] = Field(
        default_factory=list, description="לא בשימוש בדוח זה (נשמר ריק) -- תואם-טיפוס בלבד עם build_docx"
    )
    recommended_actions: list[ProductLineRecommendedAction] = Field(
        default_factory=list, description="5-8 פעולות מומלצות לפיתוח עסקי/מכירה/שיווק בקו המוצר"
    )
    assumptions: list[ProductLineAssumption] = Field(
        default_factory=list, max_length=4, description="2-4 זוגות 'הנחה <-> מה יפריך אותה'"
    )
    analyst_note_he: AnalystNote | None = Field(
        default=None,
        description='"הערכת האנליסט" -- עד 3 משפטים ללא ציטוט, המקום היחיד בדוח להערכה לא-מבוססת-מקור',
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

    @field_validator("bluf")
    @classmethod
    def _check_bluf(cls, v: list[Sentence]) -> list[Sentence]:
        return validate_bluf_length(v)
