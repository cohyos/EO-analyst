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
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    return services.list_tenders(status=status, country=country, q=q, limit=limit)


@router.get("/tenders/forecasts")
def list_tender_forecasts(limit: int = Query(100, ge=1, le=500)) -> list[dict]:
    return services.list_tender_forecasts(limit=limit)
