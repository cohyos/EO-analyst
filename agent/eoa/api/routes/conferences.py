"""`GET /api/conferences`, `/api/conferences/ical` -- FR-12 rolling conference tracker (eoa.conferences)."""

from __future__ import annotations

from fastapi import APIRouter, Query, Response

from eoa.api import services

router = APIRouter(tags=["conferences"])


@router.get("/conferences")
def list_conferences(from_: str | None = Query(None, alias="from"), to: str | None = None) -> list[dict]:
    return services.list_conferences(from_, to)


@router.get("/conferences/ical")
def conferences_ical() -> Response:
    return Response(content=services.conferences_ical(), media_type="text/calendar")
