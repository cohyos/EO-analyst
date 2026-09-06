"""Pydantic schemas for every structured LLM output in the pipeline."""

from __future__ import annotations

import re
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field, field_validator, model_validator

log = structlog.get_logger(__name__)

Domain = Literal[
    "airborne_pods",
    "land_surveillance",
    "naval_surveillance",
    "air_defense",
    "c_uas",
    "computer_vision",
    "secondary",
    "tech_dev",
    "out_of_scope",
]
ReportKind = Literal[
    "verified_report",
    "company_pr",
    "rumor_speculation",
    "academic",
    "tender",
    "patent",
    "regulatory",
    # A12 (מעקב טכנולוגי, 2026-09-06): peer-reviewed/preprint papers, conference proceedings and
    # patents/lab press releases about tech_dev subjects -- additive, distinct from "academic"
    # (which covers non-tech_dev scholarly items already in use elsewhere).
    "science",
]
Dimension = Literal["technology", "operational", "business"]
Trl = Literal["academic", "demo", "prototype", "operational", "unknown"]
Level = Literal["red", "orange", "yellow", "archive"]
Geography = Literal["US", "EU", "UK", "IL", "TR", "KR", "JP", "IN", "CN", "RU", "UA", "ME", "other"]
# A12 (מעקב טכנולוגי, 2026-09-06): only ever filled for domain == "tech_dev" items.
TechMaturity = Literal["lab", "prototype", "qualified", "fielded"]
TechActorKind = Literal["academia", "lab", "startup", "prime", "government"]


class EntityMention(BaseModel):
    name: str = Field(description="Canonical English name, e.g. 'Elbit Systems'")
    kind: Literal["company", "program", "system", "person", "org", "country"]


class ClassifyOut(BaseModel):
    """Stage: classify (light/resident model)."""

    domain: Domain = Field(
        description="out_of_scope for platform-only content with no EO/IR/CV payload substance"
    )
    subdomain: str = Field(description="taxonomy sub-key or empty")
    dimensions: list[Dimension] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list, max_length=8)
    report_kind: ReportKind
    trl: Trl = "unknown"
    geography: Geography = "other"
    entities: list[EntityMention] = Field(default_factory=list, max_length=12)
    amounts_usd: list[float] = Field(
        default_factory=list,
        description=(
            "USD amounts only, filled in ONLY when the source states USD explicitly. Never "
            "compute a currency conversion; leave empty for non-USD amounts (put the original "
            "amount+currency verbatim in relevance_note instead)."
        ),
    )
    dates: list[str] = Field(
        default_factory=list, description="ISO dates (YYYY-MM-DD) explicitly stated; empty if none"
    )
    one_line_he: str = Field(description="משפט אחד בעברית: מה קרה")
    relevance_note: str = Field(
        default="",
        max_length=200,
        description=(
            "up to 15 words, English: short reason the item is in/out of scope, OR (when a "
            "non-USD amount was found) the original amount+currency verbatim"
        ),
    )

    @model_validator(mode="after")
    def _validate_subdomain_against_taxonomy(self) -> ClassifyOut:
        """Q3-3 (docs/qa/findings_Q3_r1.md): ``subdomain`` must be one of the chosen ``domain``'s
        sub-keys in ``config/taxonomy.yaml`` -- the LLM otherwise sometimes invents a value (6
        rows in the QA sample had a subdomain that doesn't exist in the taxonomy at all).

        D1 round-1 fix (docs/qa/loop/round_1_fixes.md, ``subdomain_valid_vs_taxonomy``): a domain
        that *does* have taxonomy sub-keys must always end up with a real one -- an invalid value
        (or one left empty by the model) used to be reset to ``""``, which ``eoa.pipeline.classify``
        then persists as SQL ``NULL`` (``out.subdomain or None``), which the taxonomy validator (and
        every downstream consumer expecting a real sub-key for an in-scope item) then flags as
        invalid. Falling back to the domain's first/default sub-key instead keeps every in-scope
        item's subdomain valid by construction. ``""`` remains correct only for a domain with no
        taxonomy sub-keys at all (``out_of_scope`` and any future no-subdomain domain)."""
        try:
            from eoa.config import settings

            domains = settings().taxonomy.get("domains", {}) or {}
        except Exception as exc:  # pragma: no cover - settings() unavailable (e.g. bare unit test)
            log.debug("subdomain_taxonomy_check_skipped", error=str(exc)[:160])
            return self
        valid_subs = (domains.get(self.domain) or {}).get("sub", {}) or {}
        if not valid_subs:
            # No sub-keys defined for this domain (e.g. out_of_scope) -- "" is the only valid value.
            if self.subdomain:
                self.subdomain = ""
            return self
        if self.subdomain not in valid_subs:
            if self.subdomain:
                log.warning("classify_invalid_subdomain", domain=self.domain, subdomain=self.subdomain)
            self.subdomain = next(iter(valid_subs))
        return self


class TriageOut(BaseModel):
    """Stage: triage. Score 1-10 and level per config thresholds."""

    score: int = Field(ge=1, le=10)
    level: Level
    novelty: int = Field(ge=1, le=5, description="1=old news, 5=first-of-its-kind")
    magnitude: int = Field(ge=1, le=5, description="1=minor, 5=market-moving")
    core_relevance: int = Field(
        ge=1, le=5, description="5 only if the item's main subject is an EO/IR/CV system/program on watchlist"
    )
    reason_he: str = Field(max_length=400, description="נימוק קצר בעברית, עד 2 משפטים")
    needs_deep_search: bool = False
    deep_search_question: str = Field(
        default="",
        description=(
            "Self-contained Hebrew research question the investigation should answer. Must name "
            "the specific entities/systems/programs involved and state what is unknown -- never a "
            "bare reference like 'the article' with no carried context."
        ),
    )
    deep_search_seed_en: str = Field(
        default="", description="4-8 word English search-seed phrase (entity/system names, program terms)"
    )


class EventOut(BaseModel):
    kind: Literal[
        "contract_award",
        "m_and_a",
        "partnership",
        "investment",
        "launch",
        "test",
        "deployment",
        "regulation",
        "other",
    ]
    title: str
    date: str | None = Field(
        default=None,
        description=(
            "Event date in ISO format if the source states one explicitly, or can be derived from "
            "the item's published date for a relative-day phrase (today/yesterday); null if the "
            "date cannot be determined at all — never guess"
        ),
    )
    amount_usd: float | None = Field(
        default=None,
        description=(
            "Monetary amount in FULL units (not millions): '$464.8 million' -> 464800000; null when "
            "the source states no figure"
        ),
    )
    currency: str | None = None
    parties: list[str] = Field(default_factory=list)
    customer: str | None = None
    program: str | None = None
    summary_he: str
    confidence: float = Field(ge=0, le=1)


class EdgeOut(BaseModel):
    src: str = Field(description="entity canonical name")
    dst: str
    label: Literal[
        "COMPETITOR_OF",
        "SUPPLIER_OF",
        "PARTNER_OF",
        "ACQUIRED",
        "INTEGRATES_WITH",
        "BIDS_AGAINST",
        "DERIVED_FROM",
    ]
    evidence_he: str = Field(description="משפט ראיה מהמקור")


class SoWhatRepairOut(BaseModel):
    """Round-3 D2: the one-field schema for the generic-phrase corrective pass on ``so_what_he``
    (``eoa.pipeline.analyze._repair_generic_so_what``)."""

    so_what_he: str = Field(
        description=(
            "ASSESSMENT mode, 1-3 משפטים, מתחיל ב'להערכתנו': מי מרוויח, מי נפגע, מה משתנה ולמה -- "
            "בלי נוסחאות גנריות"
        )
    )


class AnalyzeOut(BaseModel):
    """Stage: analyze (resident model). Everything cites the item implicitly (single-source)."""

    summary_he: str = Field(
        description="FACT mode, 2-4 משפטים בעברית: רק מה שכתוב במפורש במקור, מונחים מקצועיים באנגלית בסוגריים"
    )
    so_what_he: str = Field(
        description=(
            "ASSESSMENT mode, 1-3 משפטים: השלכות תחרותיות/טכנולוגיות/מבצעיות. חובה להתחיל במילה 'להערכתנו'"
        )
    )
    key_facts: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="FACT mode: עד 8 עובדות בדידות, כל אחת ניתנת לאימות במקור; רשימה ריקה אם אין",
    )
    events: list[EventOut] = Field(
        default_factory=list,
        max_length=4,
        description="עד 4 אירועים משמעותיים; כל מספר (amount_usd/date) חייב להופיע במקור כלשונו; [] אם אין",
    )
    edges: list[EdgeOut] = Field(
        default_factory=list,
        max_length=6,
        description="עד 6 קשתות בין ישויות שמופיעות במפורש במקור; [] אם אין",
    )
    uncertainty_he: str = Field(default="", description="מה לא ברור / סותר / דורש אימות; מחרוזת ריקה אם אין")
    # A12 (מעקב טכנולוגי): additive, optional -- filled only when the item's domain is
    # "tech_dev" (see prompts/analyze.md); null/empty for every other domain.
    tech_maturity: TechMaturity | None = Field(
        default=None, description="tech_dev בלבד: lab/prototype/qualified/fielded"
    )
    tech_actor_kind: TechActorKind | None = Field(
        default=None, description="tech_dev בלבד: academia/lab/startup/prime/government"
    )
    tech_readiness_note_he: str = Field(
        default="", description="tech_dev בלבד: הערת בגרות קצרה בעברית; מחרוזת ריקה אם לא רלוונטי"
    )


class GuardVerdict(BaseModel):
    """Layer-2 LLM judge on suspicious content."""

    injection: bool
    confidence: float = Field(ge=0, le=1)
    kind: Literal[
        "none",
        "instruction_override",
        "role_change",
        "tool_hijack",
        "exfiltration",
        "prompt_leak",
        "persuasion",
        "other",
    ] = "none"
    excerpt: str = Field(
        default="", max_length=300, description="offending text, up to 2 sentences; empty if not injection"
    )


class QueryPlan(BaseModel):
    """Deep search: multilingual query formulation for one persistence round."""

    queries: list[dict[str, str]] = Field(description="list of {lang, query, rationale}")


class InvestigationOut(BaseModel):
    """Deep search: final answer of the ReAct loop."""

    outcome: Literal["found", "partial", "not_found"]
    answer_he: str
    confidence: float = Field(ge=0, le=1)
    sources: list[str] = Field(default_factory=list, description="URLs actually read")
    key_facts: list[str] = Field(default_factory=list)
    contradictions_he: str = ""
    what_was_tried_he: str = ""
    # 2026-09-06 (job 86 regression -- see docs/qa or the fix's own commit message): the finish-time
    # relevance gate's verdict, persisted alongside the answer so the API/UI can show *why* an
    # answer was accepted/downgraded, not just the final outcome. `None` when the gate wasn't run
    # (e.g. outcome == "not_found", or the cloud-batch path's cheaper deterministic-only check).
    relevance_check: dict[str, Any] | None = None
    # Round-4 W10 (docs/REVIEW_2026-09-06_evening.md): whether any content touched by this
    # investigation (a page it fetched locally, or -- for the cloud-delegated batch path -- its own
    # final answer text) was screened out or edited by the security guard on suspicion of a prompt
    # injection. `False`/`None` fields mean nothing was ever flagged; a `True` `security_review`
    # means the answer above is still safe to show (a flagged local page is simply dropped and
    # never contributes; a flagged cloud answer has the offending sentence(s) already stripped) but
    # an operator may want to review `security_flag_reason`/`security_flag_snippet` (the guard's
    # own `ScreenResult.kind`/`excerpt`, truncated) before treating it as fully trusted. The API/UI
    # review queue (separate work item) reads these three fields; nothing here is a new outcome or
    # blocks persistence on its own.
    security_review: bool = False
    security_flag_reason: str | None = None
    security_flag_snippet: str | None = None


class RelevanceVerdict(BaseModel):
    """Deep search finish-time relevance judge (2026-09-06, job 86 regression): job 86's queries
    drifted from "US Air Force speeds Reaper successor timeline after Iran losses" to generic EO/IR
    terms and `finish`'d with a `found`/0.9-confidence answer about Elbit's MOSP 5000 -- a system
    never mentioned in the question. This judge is asked, independently of the investigating
    model's own claimed confidence, whether a proposed answer actually addresses the question."""

    verdict: Literal["yes", "partial", "no"]
    reason: str = Field(default="", max_length=300, description="one short sentence, Hebrew or English")


class ReportSection(BaseModel):
    title_he: str
    domain: str
    prose_he: str = Field(description="פרוזה רהוטה עם הפניות [n]")


# ---------------------------------------------------------------------------------------------
# Goal 1 (2026-09-06, citation discipline by construction): the daily report draft moves from
# free-text prose (with the model expected to type its own "[n]" markers) to a structured
# sentence-per-claim schema, so an uncited factual claim is a *pydantic validation error* the
# model must fix (via ``chat_structured``'s existing retry-with-error-message, per
# docs/CONVENTIONS.md rule 2), not something a post-hoc regex QA pass has to notice and strip.
# ``eoa.report.qa_citations.check`` still verifies each ``cites`` entry is a *valid* registry
# number (that part needs the runtime item list, so it stays outside the schema itself) and the
# executive-summary-copies-a-section-sentence duplicate rule. ``eoa.report.docx_builder`` is what
# actually emits the "[n]" markers now -- deterministically, from ``cites`` -- so the model never
# writes citation brackets in its own prose at all.
#
# Only ``DailyReportDraft`` moves to this structure. ``ReportSection`` above (free-text
# ``prose_he``) is kept exactly as-is and continues to serve ``MonthlyReportDraft`` /
# ``BdTerritoryReportDraft`` (eoa/llm/schemas/reports.py) and the weekly report's
# ``trend_paragraphs`` -- out of this goal's scope, unchanged.
# ---------------------------------------------------------------------------------------------

_INLINE_CITE_RE = re.compile(r"\[\s*\d+\s*\]")
ASSESSMENT_MARKERS_HE = ("להערכתנו", "נראה ש", "ייתכן")


def _reject_inline_citation_markers(text: str, *, field_name: str) -> str:
    if _INLINE_CITE_RE.search(text or ""):
        raise ValueError(
            f'{field_name} must not contain a literal "[n]" marker -- put the reference number(s) '
            "in the cites field instead; the renderer emits the marker deterministically"
        )
    return text


class Sentence(BaseModel):
    """One sourced factual claim. ``cites`` must be non-empty -- an unsourced sentence has no
    business being a :class:`Sentence` at all; unsourced analyst judgement belongs in
    :class:`AnalystNote` instead (goal 1). ``eoa.report.qa_citations.check`` additionally verifies
    every ``cites`` entry is a real registry number for the given report (schema-independent since
    that range varies per report run)."""

    text_he: str = Field(description='משפט עובדתי בודד בעברית -- בלי "[n]" בטקסט עצמו')
    cites: list[int] = Field(
        min_length=1,
        description="מספרי ההפניה [n] של הפריטים התומכים במשפט -- שדה זה, לא הטקסט, הוא שמוליד את ה-[n] בדוח",
    )

    @field_validator("text_he")
    @classmethod
    def _validate_text(cls, v: str) -> str:
        v = _reject_inline_citation_markers(v, field_name="text_he")
        if not v.strip():
            raise ValueError("text_he must not be empty")
        return v


class AnalystNote(BaseModel):
    """'הערכת האנליסט' -- the ONE place in the daily report where unsourced analyst judgement is
    allowed (goal 1): at most 3 short sentences, no citations required. Rendered in italics under
    an explicit label so it can never be mistaken for a sourced factual claim."""

    sentences_he: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("sentences_he")
    @classmethod
    def _validate_sentences(cls, v: list[str]) -> list[str]:
        return [_reject_inline_citation_markers(s, field_name="analyst_note_he.sentences_he") for s in v]


class StructuredSection(BaseModel):
    """A daily-report domain section as a list of sourced sentences rather than free prose (goal
    1) -- see the module-level note above for why only ``DailyReportDraft`` uses this."""

    title_he: str
    domain: str
    sentences: list[Sentence] = Field(default_factory=list, description="משפטי הסעיף, כל אחד עם cites")


class OutlookIndicator(BaseModel):
    """One forward-looking indicator in 'מבט קדימה' (goal 4): either a sourced claim (``cites``
    non-empty) or the analyst's own forward assessment (``cites`` may be empty, but only when
    ``is_assessment=True`` and ``text_he`` opens with an explicit assessment marker) -- every
    indicator is one or the other, never an uncited claim silently passed off as sourced."""

    text_he: str
    cites: list[int] = Field(default_factory=list)
    is_assessment: bool = Field(default=False, description="True אם זו הערכת האנליסט (לא ציטוט ישיר של מקור)")

    @model_validator(mode="after")
    def _validate(self) -> OutlookIndicator:
        _reject_inline_citation_markers(self.text_he, field_name="outlook.text_he")
        if not self.text_he.strip():
            raise ValueError("outlook indicator text_he must not be empty")
        if not self.cites and not self.is_assessment:
            raise ValueError("an outlook indicator with empty cites must set is_assessment=true")
        if self.is_assessment and not any(self.text_he.startswith(m) for m in ASSESSMENT_MARKERS_HE):
            raise ValueError(
                "an analyst-assessment outlook indicator must open with an explicit marker "
                f"({'/'.join(ASSESSMENT_MARKERS_HE)})"
            )
        return self


class DailyReportDraft(BaseModel):
    """Report writer output (goal 1: citation discipline by construction). ``cites``/``sentences``
    refer to the numbered item list given in the prompt; the model never writes "[n]" itself."""

    exec_summary: list[Sentence] = Field(
        default_factory=list,
        description=(
            "3-5 משפטים בלבד, המסכמים ומקשרים בין ממצאי הסעיפים (מה השתנה, למה זה חשוב, מה לעקוב "
            "אחריו); כל משפט עם cites משלו. אסור שמשפט יהיה זהה (כלשונו) למשפט מתוך גוף אחד הסעיפים"
        ),
    )
    sections: list[StructuredSection] = Field(default_factory=list)
    system_note_he: str = Field(
        default="",
        description=(
            "הודעת מערכת דטרמיניסטית (לעולם לא נכתבת ע\"י המודל -- מוזרקת בקוד): למשל 'אין ממצאים "
            "בתקופה זו' או הודעת כשל אימות אחרי ניסיון תיקון -- מוצגת כפרוזה רגילה, ללא תווית ובלי "
            "דרישת cites, ומובחנת מ-analyst_note_he (הערכה של האנליסט/המודל)"
        ),
    )
    analyst_note_he: AnalystNote | None = Field(
        default=None,
        description='"הערכת האנליסט" -- עד 3 משפטים ללא ציטוט, המקום היחיד בדוח להערכה לא-מבוססת-מקור',
    )
    outlook: list[OutlookIndicator] = Field(
        default_factory=list,
        description="2-3 אינדיקטורים קונקרטיים למעקב ב'מבט קדימה', כל אחד מצוטט או מסומן כהערכת אנליסט",
    )
    open_points_he: list[str] = Field(default_factory=list, description="נקודות פתוחות להכרעת המשתמש")
