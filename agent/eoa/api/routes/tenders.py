"""`GET /api/tenders`, `/api/tenders/forecasts` -- section 5.2 / FR-5.2 tender/RFI/RFP tracking (eoa.tenders)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from eoa.api import services

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
