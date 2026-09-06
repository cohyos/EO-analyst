"""`GET /api/tenders`, `/api/tenders/forecasts` -- section 5.2 / FR-5.2 tender/RFI/RFP tracking
(eoa.tenders). Also W2b: `POST`/`GET /api/tenders/{id}/feedback` -- the operator 👍/👎 relevance
feedback loop (eoa.tenders.feedback) that self-tunes the intake threshold and per-source scan
priority."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from eoa.api import services
from eoa.api.errors import not_found

router = APIRouter(tags=["tenders"])


@router.get("/tenders")
def list_tenders(
    status: str | None = Query(None),
    country: str | None = Query(None),
    q: str | None = Query(None),
    # Default hides relevance < 3 (see services.DEFAULT_MIN_RELEVANCE); pass 0 to see everything.
    min_relevance: int | None = Query(services.DEFAULT_MIN_RELEVANCE, ge=0, le=10),
    # F24: default view is 'open'/'unknown' from the last 90 days -- ignored once `status` is set.
    # Q5-11: left as `None` (not `DEFAULT_SINCE_DAYS`) so `services.list_tenders` can tell "no
    # since_days given" apart from "since_days=90 given" -- the former lets `include_closed`/
    # `include_archived` lift the window entirely instead of re-hiding the rows they were meant to
    # reveal; the latter always applies the window verbatim.
    since_days: int | None = Query(None, ge=1),
    include_closed: bool = Query(False),
    include_archived: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    return services.list_tenders(
        status=status,
        country=country,
        q=q,
        min_relevance=min_relevance,
        since_days=since_days,
        include_closed=include_closed,
        include_archived=include_archived,
        limit=limit,
    )


@router.get("/tenders/forecasts")
def list_tender_forecasts(limit: int = Query(100, ge=1, le=500)) -> list[dict]:
    return services.list_tender_forecasts(limit=limit)


@router.get("/tenders/coverage")
def get_tender_source_coverage() -> dict:
    """A15: read-only per-region tender-source coverage (config/tenders.yaml + the `tenders`
    table) for the tenders page's "כיסוי מקורות" panel -- see services.tender_source_coverage."""
    return services.tender_source_coverage()


class TenderFeedbackCreate(BaseModel):
    verdict: Literal["relevant", "irrelevant"]
    reason: str | None = None


@router.post("/tenders/{tender_id}/feedback")
def create_tender_feedback(tender_id: int, body: TenderFeedbackCreate) -> dict:
    """W2b: one-click 👍/👎 (+ optional free-text reason) on a tenders row. Immediately flips the
    tender's own `intake` (👍 -> 'accepted', 👎 -> 'rejected-by-user', hidden by default) and
    recomputes the self-tuning relevance threshold + this source's scan priority -- see
    eoa.tenders.feedback."""
    result = services.record_tender_feedback(tender_id, body.verdict, body.reason)
    if result is None:
        raise not_found("המכרז לא נמצא")
    return result


@router.get("/tenders/{tender_id}/feedback")
def get_tender_feedback(tender_id: int) -> list[dict]:
    return services.list_tender_feedback(tender_id)
