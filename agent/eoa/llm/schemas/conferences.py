"""Pydantic schemas for structured LLM output used by ``eoa.conferences`` (FR-12)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ConferenceExtract(BaseModel):
    """Stage: ``tracker.verify_conference``. Extracted from up to two official-looking pages.

    Only the fields the page text actually supports should be non-null; the caller writes a
    field back to the DB only when ``confidence >= 0.6`` (per FR-12.3). Never invent a date or
    URL that is not explicitly present in the DATA.
    """

    found: bool = Field(description="Whether the pages are actually about the named conference/year")
    cancelled: bool = Field(default=False, description="True if the pages explicitly say it was cancelled")
    start_date: str | None = Field(default=None, description="ISO date YYYY-MM-DD, only if explicit")
    end_date: str | None = Field(default=None, description="ISO date YYYY-MM-DD, only if explicit")
    city: str | None = None
    venue: str | None = None
    registration_opens: str | None = Field(default=None, description="ISO date YYYY-MM-DD")
    early_bird_deadline: str | None = Field(default=None, description="ISO date YYYY-MM-DD")
    cfp_deadline: str | None = Field(default=None, description="ISO date YYYY-MM-DD")
    cost_range: str | None = None
    registration_url: str | None = None
    entry_conditions: str | None = None
    key_exhibitors: list[str] = Field(default_factory=list, max_length=15)
    confidence: float = Field(ge=0, le=1)


class ConferenceCandidate(BaseModel):
    """One proposed new conference from ``tracker.discover_new``."""

    name: str = Field(description="Official conference/exhibition name")
    dates: str | None = Field(default=None, description="Dates as stated in the source (free text or ISO)")
    city: str | None = None
    url: str | None = None
    rationale_he: str = Field(
        default="", description="עברית קצרה: למה זה רלוונטי לתחומי EO/IR/C-UAS/הגנה אווירית"
    )


class ConferenceCandidates(BaseModel):
    """Stage: ``tracker.discover_new``. Proposed new conferences found via search."""

    candidates: list[ConferenceCandidate] = Field(default_factory=list, max_length=20)
