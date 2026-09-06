"""Pydantic schemas for structured LLM output used by ``eoa.patents`` (A14, patent/IP tracking)."""

from __future__ import annotations

from pydantic import BaseModel, Field


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


class PatentSurveySynthesisOut(BaseModel):
    """Stage: ``eoa.patents.survey.build_patent_survey``. LLM synthesis over a numbered set of
    patent records (same ``[n]`` citation-registry convention as the daily/weekly/monthly report
    drafts, see ``eoa.report.qa_citations``) -- landscape narrative only; the numeric
    clustering/timeline/white-space tables are computed deterministically in ``survey.py`` and
    never depend on this call."""

    overview_he: str = Field(description="סקירה כללית של נוף הפטנטים בנושא, 4-8 משפטים, עם הפניות [n]")
    leaders_he: str = Field(description="ניתוח השחקנים המובילים ומיצובם, 3-5 משפטים, עם הפניות [n]")
    israel_position_he: str = Field(
        description="מיצוב התעשייה הישראלית בנושא זה (חברות ישראליות, פערים, הזדמנויות), 2-4 משפטים"
    )
    white_spaces_he: str = Field(
        description="פערים/הזדמנויות לבנות (צירופי CPC/שחקן שאינם מכוסים), 2-4 משפטים"
    )
    outlook_he: str = Field(description="מבט קדימה קצר על כיוון ההתפתחות הטכנולוגית הצפויה, 2-3 משפטים")
