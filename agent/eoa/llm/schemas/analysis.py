"""Pydantic schemas for every structured LLM output in the pipeline."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Domain = Literal[
    "airborne_pods",
    "land_surveillance",
    "naval_surveillance",
    "air_defense",
    "c_uas",
    "computer_vision",
    "secondary",
    "out_of_scope",
]
ReportKind = Literal[
    "verified_report", "company_pr", "rumor_speculation", "academic", "tender", "patent", "regulatory"
]
Dimension = Literal["technology", "operational", "business"]
Trl = Literal["academic", "demo", "prototype", "operational", "unknown"]
Level = Literal["red", "orange", "yellow", "archive"]
Geography = Literal["US", "EU", "UK", "IL", "TR", "KR", "JP", "IN", "CN", "RU", "UA", "ME", "other"]


class EntityMention(BaseModel):
    name: str = Field(description="Canonical English name, e.g. 'Elbit Systems'")
    kind: Literal["company", "program", "system", "person", "org", "country"]


class ClassifyOut(BaseModel):
    """Stage: classify (light/resident model)."""

    domain: Domain
    subdomain: str = Field(description="taxonomy sub-key or empty")
    dimensions: list[Dimension] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list, max_length=8)
    report_kind: ReportKind
    trl: Trl = "unknown"
    geography: Geography = "other"
    entities: list[EntityMention] = Field(default_factory=list, max_length=12)
    amounts_usd: list[float] = Field(default_factory=list, description="monetary amounts mentioned, USD")
    dates: list[str] = Field(default_factory=list, description="ISO dates mentioned")
    one_line_he: str = Field(description="משפט אחד בעברית: מה קרה")
    relevance_note: str = Field(default="", description="why in/out of scope, short English")


class TriageOut(BaseModel):
    """Stage: triage. Score 1-10 and level per config thresholds."""

    score: int = Field(ge=1, le=10)
    level: Level
    novelty: int = Field(ge=1, le=5, description="1=old news, 5=first-of-its-kind")
    magnitude: int = Field(ge=1, le=5, description="1=minor, 5=market-moving")
    core_relevance: int = Field(ge=1, le=5)
    reason_he: str = Field(description="נימוק קצר בעברית, עד 2 משפטים")
    needs_deep_search: bool = False
    deep_search_question: str = Field(
        default="", description="What exactly should the investigation establish"
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
    date: str | None = Field(default=None, description="ISO date if stated")
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

    summary_he: str = Field(description="2-4 משפטים בעברית, מונחים מקצועיים באנגלית בסוגריים")
    so_what_he: str = Field(description="'מה זה אומר': השלכות תחרותיות/טכנולוגיות/מבצעיות, 1-3 משפטים")
    key_facts: list[str] = Field(
        default_factory=list, max_length=8, description="עובדות בדידות, כל אחת ניתנת לאימות במקור"
    )
    events: list[EventOut] = Field(default_factory=list)
    edges: list[EdgeOut] = Field(default_factory=list)
    uncertainty_he: str = Field(default="", description="מה לא ברור / סותר / דורש אימות")


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
    excerpt: str = Field(default="", max_length=300)


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

    exec_summary_he: str = Field(description="עד ~200 מילים: מה קרה, מה זה אומר, מה דורש תשומת לב, עם [n]")
    sections: list[ReportSection]
    outlook_he: str = Field(default="", description="מבט קדימה קצר")
    open_points_he: list[str] = Field(default_factory=list, description="נקודות פתוחות להכרעת המשתמש")
