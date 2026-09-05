"""Pydantic schemas for every structured LLM output in the pipeline."""

from __future__ import annotations

from typing import Literal

import structlog
from pydantic import BaseModel, Field, model_validator

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
    def _validate_subdomain_against_taxonomy(self) -> "ClassifyOut":
        """Q3-3 (docs/qa/findings_Q3_r1.md): ``subdomain`` must be one of the chosen ``domain``'s
        sub-keys in ``config/taxonomy.yaml`` -- the LLM otherwise sometimes invents a value (6
        rows in the QA sample had a subdomain that doesn't exist in the taxonomy at all). An
        unknown value is reset to ``""`` (the schema's own "no sub-domain" convention) with a
        warning logged, rather than rejecting the whole classification."""
        if not self.subdomain:
            return self
        try:
            from eoa.config import settings

            domains = settings().taxonomy.get("domains", {}) or {}
        except Exception as exc:  # pragma: no cover - settings() unavailable (e.g. bare unit test)
            log.debug("subdomain_taxonomy_check_skipped", error=str(exc)[:160])
            return self
        valid_subs = (domains.get(self.domain) or {}).get("sub", {}) or {}
        if self.subdomain not in valid_subs:
            log.warning(
                "classify_invalid_subdomain", domain=self.domain, subdomain=self.subdomain
            )
            self.subdomain = ""
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
    amount_usd: float | None = None
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


class ReportSection(BaseModel):
    title_he: str
    domain: str
    prose_he: str = Field(description="פרוזה רהוטה עם הפניות [n]")


class DailyReportDraft(BaseModel):
    """Report writer output. [n] refer to the numbered item list given in the prompt."""

    exec_summary_he: str = Field(
        description=(
            "3-5 משפטים בלבד, המסכמים ומקשרים בין ממצאי הסעיפים (מה השתנה, למה זה חשוב, מה לעקוב "
            "אחריו), עם [n]; אסור להעתיק משפט כלשונו מגוף אחד הסעיפים"
        )
    )
    sections: list[ReportSection]
    outlook_he: str = Field(default="", description="מבט קדימה קצר")
    open_points_he: list[str] = Field(default_factory=list, description="נקודות פתוחות להכרעת המשתמש")
