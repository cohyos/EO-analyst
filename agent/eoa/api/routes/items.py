"""`GET /api/items`, `GET /api/items/{id}`, feedback, investigate."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from eoa.api import services
from eoa.api.errors import bad_request, not_found

router = APIRouter(tags=["items"])

FEEDBACK_LEVELS = {"red", "orange", "yellow", "archive"}


class FeedbackRequest(BaseModel):
    user_level: str
    comment: str | None = None


class InvestigateRequest(BaseModel):
    question: str | None = None


@router.get("/items")
def list_items(
    level: str | None = None,
    domain: str | None = None,
    since: str | None = None,
    q: str | None = None,
    country: str | None = None,
    group_by: str | None = Query(None, pattern="^country$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort: str = Query("score", pattern="^(score|published_at)$"),
) -> dict:
    total, items = services.list_items(
        level=level,
        domain=domain,
        since=since,
        q=q,
        country=country,
        page=page,
        page_size=page_size,
        sort=sort,
    )
    result: dict = {"total": total, "items": items}
    # U7a: additive -- only present when `group_by=country` is requested, so
    # existing callers of the contract (docs/API.md) see no shape change.
    if group_by == "country":
        result["groups"] = services.items_by_country_groups(level=level, domain=domain, since=since)
    return result


@router.get("/items/by-country")
def items_by_country(
    level: str | None = None,
    domain: str | None = None,
    since: str | None = None,
) -> dict:
    """U7c: per-country item counts + level breakdown for the current feed
    filters -- backs the Feed screen's "מפת מדינות" panel."""
    return {"countries": services.items_by_country_groups(level=level, domain=domain, since=since)}


@router.get("/items/{item_id}")
def get_item(item_id: int) -> dict:
    item = services.get_item(item_id)
    if item is None:
        raise not_found("הפריט לא נמצא")
    return item


@router.post("/items/{item_id}/feedback")
def submit_feedback(item_id: int, body: FeedbackRequest) -> dict:
    if body.user_level not in FEEDBACK_LEVELS:
        raise bad_request(f"רמה לא חוקית: {body.user_level}")
    item = services.item_feedback(item_id, body.user_level, body.comment)
    if item is None:
        raise not_found("הפריט לא נמצא")
    return item


@router.post("/items/{item_id}/investigate")
def investigate(item_id: int, body: InvestigateRequest) -> dict:
    job_id = services.investigate_item(item_id, body.question)
    if job_id is None:
        raise not_found("הפריט לא נמצא")
    return {"job_id": job_id}
