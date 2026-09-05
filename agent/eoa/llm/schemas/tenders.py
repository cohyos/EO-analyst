"""Pydantic schemas for structured LLM output used by ``eoa.tenders`` (section 5.2 / FR-5.2)."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field


class TenderExtract(BaseModel):
    """Stage: ``eoa.tenders.scan._llm_classify``. LLM relevance/summary classification of one raw
    notice, run *before* any DB write.

    ``relevant``/``relevance`` gate persistence itself (see ``scan.py``'s module docstring):
    ``relevance <= 2`` -> the notice is not stored at all; ``== 3`` -> stored with
    ``status='unknown'``; ``>= 4`` -> stored normally. When this call is unavailable/fails, the
    deterministic two-signal-gate verdict (keyword-hit relevance/matched_terms) is used instead --
    a stalled model degrades to "still ingested on the gate's own strength", never blocks
    persistence outright (only an actual "not relevant" verdict from the model does that).

    F2 (2026-09-05): ``published_at``/``deadline``/``agency``/``country``/``notice_type`` were
    added so ``eoa.tenders.scan`` can fill in dates/geography for search-hit-derived notices
    (TED/Contracts Finder already carry these structurally; a generic SearXNG hit does not) and so
    the ``_initial_status`` rubric can tell an actually-awarded notice from an open one. All five
    default to "unknown"/``None`` -- per the prompt's "never guess" rule, an absent fact in the
    source text must come back empty, not fabricated.
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
    published_at: dt.date | None = Field(
        default=None, description="תאריך פרסום ההודעה, אם מצוין בטקסט במפורש; אחרת null (אסור לנחש)"
    )
    deadline: dt.date | None = Field(
        default=None, description="תאריך אחרון להגשה/דדליין, אם מצוין בטקסט במפורש; אחרת null (אסור לנחש)"
    )
    agency: str | None = Field(default=None, description="הגורם המזמין/המפרסם, אם מצוין; אחרת null")
    country: str | None = Field(
        default=None,
        description="מדינת הרוכש, קוד ISO-2 או 'EU'/'NATO', אם ניתן להסיק מהטקסט בבירור; אחרת null (אסור לנחש)",
    )
    notice_type: Literal["rfi", "rfp", "rfq", "sources_sought", "tender", "award", "other"] = Field(
        default="other", description="סוג ההודעה כפי שמשתמע מהטקסט"
    )


class TenderForecastOut(BaseModel):
    """Stage: ``eoa.tenders.forecast.forecast_tenders``. LLM writes only the Hebrew rationale --
    likelihood/window/candidate_vendors are computed deterministically by the rubric in
    ``forecast.py`` and never depend on this call. Must cite the trigger item(s) as ``[item N]``
    per the numbering given in the prompt; never invent a fact not present in the DATA block."""

    rationale_he: str = Field(
        description="נימוק קצר בעברית (2-4 משפטים) לתחזית המכרז, עם הפניות [item N] לפריטי המקור"
    )
