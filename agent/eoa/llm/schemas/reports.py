"""Pydantic schemas for the weekly/monthly report drafts (LLM-authored prose only).

Trend detection (``eoa.report.trends``), the competitive-landscape players map, the top-events
table, the conference lookahead/horizon, and the FR-11.4 meta-summary are all deterministic,
non-LLM data — never fields on these schemas, always rendered as extra ``docx_builder``
sections/tables built straight from the database, per rule 5 ("Never invent").
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from eoa.llm.schemas.analysis import ReportSection


class TrendParagraph(BaseModel):
    """One prose paragraph elaborating a single trend detected by ``eoa.report.trends``.

    ``title_he`` is expected to echo (or closely follow) the trend's own ``title_he`` as produced
    by ``eoa.report.trends.detect_trends`` — the model should not invent a new trend, only narrate
    the ones it was given.
    """

    title_he: str
    prose_he: str = Field(description="פרוזה רהוטה עם הפניות [n] לפריטי המקור שביססו את המגמה")


class WeeklyReportDraft(BaseModel):
    """Weekly report writer output ("דוח שבועי", FR-5.4). ``[n]`` refer to the numbered item list
    given in the prompt (the week's red/orange items, extended with any item that only fed a
    trend's evidence)."""

    exec_summary_he: str = Field(
        description="עד כ-250 מילים: מה קרה השבוע, מה המגמות המרכזיות, ומה דורש תשומת לב, עם [n]"
    )
    trend_paragraphs: list[TrendParagraph] = Field(
        default_factory=list, description="פסקה אחת לכל מגמה שזוהתה השבוע (מגמות השבוע)"
    )
    sections: list[ReportSection] = Field(default_factory=list, description="פרקים לפי תחום")
    outlook_he: str = Field(default="", description="מבט קדימה קצר, פותח במילת הערכה מפורשת")
    open_points_he: list[str] = Field(default_factory=list, description="נקודות פתוחות להכרעת המשתמש")


class MonthlyReportDraft(BaseModel):
    """Monthly report writer output ("דוח חודשי", FR-5.4: נוף תחרותי מלא ומפת שחקנים). ``[n]``
    refer to the numbered item list given in the prompt (the month's red/orange items, extended
    with any item that only fed a trend's evidence)."""

    exec_summary_he: str = Field(
        description="עד כ-300 מילים: נוף תחרותי, מגמות מרכזיות של החודש, ומה דורש תשומת לב, עם [n]"
    )
    trend_paragraphs: list[TrendParagraph] = Field(
        default_factory=list, description="פסקה אחת לכל מגמה שזוהתה החודש"
    )
    sections: list[ReportSection] = Field(default_factory=list, description="פרקים לפי תחום")
    outlook_he: str = Field(default="", description="מבט קדימה קצר, פותח במילת הערכה מפורשת")
    open_points_he: list[str] = Field(default_factory=list, description="נקודות פתוחות להכרעת המשתמש")
