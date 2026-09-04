"""Pydantic schemas for structured LLM output used by ``eoa.tenders`` (section 5.2 / FR-5.2)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TenderExtract(BaseModel):
    """Stage: ``eoa.tenders.scan._llm_enrich``. LLM relevance/summary pass on one raw notice.

    The deterministic keyword-hit relevance/matched_terms computed in ``scan.py`` are always
    written first and never depend on this call succeeding (FR-9 / "never invent"); this is a
    best-effort enrichment that overwrites them only when it succeeds with reasonable confidence.
    """

    relevant: bool = Field(description="Whether this notice is genuinely about EO/IR/CV defense systems")
    relevance: int = Field(ge=0, le=10, description="0=not relevant, 10=core EO/IR/C-UAS procurement")
    summary_he: str = Field(default="", description="סיכום קצר בעברית (1-3 משפטים) של מהות המכרז/הבקשה")
    matched_terms: list[str] = Field(
        default_factory=list, max_length=10, description="מונחי EO/IR/CV שזוהו בטקסט (אנגלית)"
    )
    entities: list[str] = Field(
        default_factory=list, max_length=10, description="שמות חברות/תוכניות/מערכות שמוזכרים"
    )
    confidence: float = Field(ge=0, le=1)


class TenderForecastOut(BaseModel):
    """Stage: ``eoa.tenders.forecast.forecast_tenders``. LLM writes only the Hebrew rationale --
    likelihood/window/candidate_vendors are computed deterministically by the rubric in
    ``forecast.py`` and never depend on this call. Must cite the trigger item(s) as ``[item N]``
    per the numbering given in the prompt; never invent a fact not present in the DATA block."""

    rationale_he: str = Field(
        description="נימוק קצר בעברית (2-4 משפטים) לתחזית המכרז, עם הפניות [item N] לפריטי המקור"
    )
