"""Pydantic schemas for structured LLM output used by ``eoa.patents`` (A14, patent/IP tracking)."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, model_validator

# --------------------------------------------------------------------------
# per-patent claims analysis (eoa.patents.analyze)
# --------------------------------------------------------------------------


class PatentClaimsOut(BaseModel):
    """Stage: ``eoa.patents.analyze._llm_analyze_one``. LLM reading of one patent's
    title+abstract (+ any claims text available) -- never invents a fact not present in the
    source text; an unclear/insufficient abstract yields a short, explicit "אין מספיק מידע"
    rather than a fabricated summary."""

    claims_summary_he: str = Field(
        description="סיכום תביעות הפטנט בעברית, 3-5 משפטים: מה הפטנט מגן עליו בפועל, מה החידוש הטכני"
    )
    subdomain: str = Field(
        default="",
        description="מפתח תת-תחום מתוך taxonomy.yaml (domains.tech_dev.sub), או ריק אם לא ברור",
    )
    so_what_he: str = Field(
        description="השלכה עסקית/טכנולוגית קצרה בעברית: מה המשמעות עבור מוצרי EO/IR ועבור התעשייה הישראלית"
    )


# --------------------------------------------------------------------------
# survey synthesis (eoa.patents.survey.build_patent_survey) -- goal (2026-09-06):
#
# structured, per-sentence citation discipline "by construction" (same idea as
# eoa.llm.schemas.analysis.Sentence/OutlookIndicator for the daily report): the model never
# writes a literal "[n]"/"[Pn]" marker itself, it names the registry numbers it relies on in a
# dedicated field, and the renderer (eoa.patents.survey, duck-typed against
# eoa.report.docx_builder's structured-draft path) emits the marker deterministically. This
# replaces the old free-prose PatentSurveySynthesisOut, whose un-cited paragraphs had no
# mechanism to stop a truncated/uncited/English-pasted sentence from reaching the report.
#
# Two distinct citation registries feed one shared numbering space (see survey.py's registry
# builder): patent records (numbered first, off the same ``patents`` table rows the deterministic
# tables use) and, immediately after them, database records -- items/events that document an
# assignee's real-world activity (contracts, partnerships, products, programs). A sentence's
# ``cites`` are plain registry numbers into that combined list; which half of the registry a given
# number falls in is a rendering-time fact (survey.py knows the split point), not something the
# model needs to track -- it only ever sees one flat numbered list per prompt data block.
# --------------------------------------------------------------------------

_INLINE_CITE_RE = re.compile(r"\[\s*P?\d+\s*\]", re.IGNORECASE)
GENERAL_KNOWLEDGE_LABEL_HE = "ידע כללי (לא מאומת במאגר):"


def _reject_inline_citation_markers(text: str, *, field_name: str) -> str:
    if _INLINE_CITE_RE.search(text or ""):
        raise ValueError(
            f'{field_name} must not contain a literal "[n]"/"[Pn]" marker -- put the reference '
            "number(s) in the cites field instead; the renderer emits the marker deterministically"
        )
    return text


class PatentCiteSentence(BaseModel):
    """One sourced factual claim about the patent landscape or an assignee's business activity.
    ``cites`` must be non-empty *unless* ``is_general_knowledge`` is set -- the one escape hatch
    for LLM background knowledge that isn't backed by anything in this project's own database or
    patent registry (per the user's 2026-09-06 request: general knowledge is allowed, but must be
    labelled as such, never silently presented as a sourced finding). A general-knowledge sentence
    is enforced (at validation time, not just by prompt instruction) to open with
    :data:`GENERAL_KNOWLEDGE_LABEL_HE` so the renderer/reader can never mistake it for a cited
    claim even if the surrounding prose is skimmed."""

    text_he: str = Field(description='משפט עובדתי בודד בעברית -- בלי "[n]"/"[Pn]" בטקסט עצמו')
    cites: list[int] = Field(
        default_factory=list,
        description="מספרי הרשומות התומכות במשפט (פטנטים ורשומות מאגר חולקים רצף מספור אחד)",
    )
    is_general_knowledge: bool = Field(
        default=False,
        description=(
            'True אם המשפט מבוסס על ידע כללי של המודל ולא על המאגר/רשימת הפטנטים -- במקרה זה '
            f'הטקסט חייב להתחיל במילים "{GENERAL_KNOWLEDGE_LABEL_HE}" ו-cites יכול להיות ריק'
        ),
    )

    @model_validator(mode="after")
    def _validate(self) -> PatentCiteSentence:
        _reject_inline_citation_markers(self.text_he, field_name="text_he")
        if not self.text_he.strip():
            raise ValueError("text_he must not be empty")
        if self.is_general_knowledge:
            if not self.text_he.startswith(GENERAL_KNOWLEDGE_LABEL_HE):
                raise ValueError(
                    f'a general-knowledge sentence must open with "{GENERAL_KNOWLEDGE_LABEL_HE}"'
                )
        elif not self.cites:
            raise ValueError("cites must be non-empty unless is_general_knowledge=true")
        return self


class AssigneeProfile(BaseModel):
    """A business-depth "profile מקצה" for one of the survey's top assignees (2026-09-06 request):
    an explicit technology -> product -> program chain grounded in this project's own database
    (canonical entity, watchlist products/programs, recent contracts/partnerships), not just a
    patent count. ``canonical_entity_he`` is filled in by the renderer (from
    ``eoa.pipeline.entity_normalize.resolve_canonical``) before the prompt is sent, so the model is
    only ever asked to reason over data actually supplied to it -- never to guess a canonical name
    on its own."""

    assignee_name: str = Field(description="שם המקצה כפי שמופיע ברשומות הפטנט")
    tech_product_chain: list[PatentCiteSentence] = Field(
        min_length=1,
        description=(
            "שרשרת מפורשת טכנולוגיה -> מוצר -> תוכנית, למשל: פטנטי עיבוד-על-החיישן/ראייה "
            "ממוחשבת <-> Lattice/Roadrunner/Anvil <-> תוכניות Replicator/C-UAS"
        ),
    )
    recent_activity: list[PatentCiteSentence] = Field(
        default_factory=list,
        description="פעילות עדכנית מהמאגר (חוזים/שותפויות/מוצרים/תוכניות) -- ריק אם אין נתונים",
    )
    implications_he: list[PatentCiteSentence] = Field(
        min_length=1, description="מה זה אומר למתחרים ולתעשייה הביטחונית הישראלית"
    )


class PatentBizAction(BaseModel):
    """One recommended action for OUR company (``bd_report.our_company``/``perspective_he``,
    mirrors ``eoa.llm.schemas.reports.BdAction``'s perspective discipline) in "השלכות עסקיות
    והמלצות" -- 3-6 concrete actions, per the user's 2026-09-06 request."""

    action_he: str = Field(description="פעולה קונקרטית אחת עבור החברה שלנו")
    rationale_he: str = Field(description="נימוק קצר לפעולה")
    rationale_cites: list[int] = Field(
        default_factory=list, description="מספרי רשומות תומכות בנימוק, אם קיימות"
    )

    @model_validator(mode="after")
    def _validate(self) -> PatentBizAction:
        _reject_inline_citation_markers(self.action_he, field_name="action_he")
        _reject_inline_citation_markers(self.rationale_he, field_name="rationale_he")
        if not self.action_he.strip() or not self.rationale_he.strip():
            raise ValueError("action_he/rationale_he must not be empty")
        return self


class PatentSurveyDraft(BaseModel):
    """Stage: ``eoa.patents.survey.build_patent_survey``. Structured LLM synthesis over a numbered
    set of patent records + (for the top assignees) their recent database activity -- citation
    discipline by construction (see the module note above), replacing the old free-prose
    ``PatentSurveySynthesisOut``. The numeric clustering/timeline/white-space *tables* are still
    computed deterministically in ``survey.py`` and never depend on this call; this schema is the
    narrative layer only, rendered via ``eoa.report.docx_builder``'s existing goal-1
    structured-draft path (``eoa.patents.survey`` assembles a small duck-typed wrapper -- see its
    module docstring -- so ``docx_builder.py`` itself needed no changes)."""

    exec_summary: list[PatentCiteSentence] = Field(
        min_length=1, description="תקציר מנהלים מצוטט, 3-6 משפטים"
    )
    landscape: list[PatentCiteSentence] = Field(
        min_length=1, description="נוף הפטנטים: היקף, ציר זמן, מוקדי טריטוריה, 3-6 משפטים"
    )
    tech_clusters: list[PatentCiteSentence] = Field(
        default_factory=list, description="אשכולות טכנולוגיה לפי CPC, מתורגם לעברית, 2-6 משפטים"
    )
    assignee_profiles: list[AssigneeProfile] = Field(
        min_length=1,
        max_length=5,
        description="פרופילי המקצים המובילים (2-5), עם שרשרת טכנולוגיה->מוצר->תוכנית",
    )
    white_spaces: list[PatentCiteSentence] = Field(
        default_factory=list, description="חורים והזדמנויות (white space), 2-4 משפטים"
    )
    israel_position: list[PatentCiteSentence] = Field(
        default_factory=list, description="עמדת התעשייה הישראלית בנושא, 2-4 משפטים"
    )
    business_implications: list[PatentBizAction] = Field(
        min_length=3, max_length=6, description="3-6 המלצות פעולה קונקרטיות עבור החברה שלנו"
    )
    outlook: list[PatentCiteSentence] = Field(
        default_factory=list, description="מבט קדימה קצר, 2-3 משפטים"
    )
    open_points_he: list[str] = Field(default_factory=list, description="נקודות פתוחות")
