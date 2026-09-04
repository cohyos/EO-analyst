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
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    return services.list_tenders(status=status, country=country, q=q, min_relevance=min_relevance, limit=limit)


@router.get("/tenders/forecasts")
def list_tender_forecasts(limit: int = Query(100, ge=1, le=500)) -> list[dict]:
    return services.list_tender_forecasts(limit=limit)
