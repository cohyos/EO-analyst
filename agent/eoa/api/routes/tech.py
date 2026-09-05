"""`GET /api/tech/radar`, `GET /api/tech/items` -- A12 מעקב טכנולוגי (רדאר טכנולוגי)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from eoa.api import services

router = APIRouter(tags=["tech"])


@router.get("/tech/radar")
def tech_radar(weeks: int = Query(12, ge=1, le=52)) -> dict:
    return services.tech_radar(weeks)


@router.get("/tech/items")
def tech_items(
    subdomain: str | None = None,
    maturity: str | None = Query(None, pattern="^(lab|prototype|qualified|fielded)$"),
    actor_kind: str | None = Query(
        None, pattern="^(academia|lab|startup|prime|government)$"
    ),
    since: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    total, items = services.list_tech_items(
        subdomain=subdomain,
        maturity=maturity,
        actor_kind=actor_kind,
        since=since,
        page=page,
        page_size=page_size,
    )
    return {"total": total, "items": items}
