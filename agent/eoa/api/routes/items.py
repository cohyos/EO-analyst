"""`GET /api/items`, `GET /api/items/{id}`, feedback, investigate, corroborate."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from eoa.api import services
from eoa.api.errors import bad_request, conflict, not_found

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
    israel: bool = False,
    group_by: str | None = Query(None, pattern="^country$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort: str = Query("score", pattern="^(score|published_at)$"),
    # Story clustering (2026-09-17): one card per story instead of one per item -- see
    # `eoa.api.services.list_items`'s `group_stories` branch. Defaults to off so this endpoint's
    # existing contract (docs/API.md) is unchanged for any caller that doesn't ask for it; the feed
    # page itself requests it explicitly (web/src/pages/FeedPage.tsx).
    group_stories: bool = False,
) -> dict:
    total, items = services.list_items(
        level=level,
        domain=domain,
        since=since,
        q=q,
        country=country,
        israel=israel,
        page=page,
        page_size=page_size,
        sort=sort,
        group_stories=group_stories,
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
    """Q5-3 (docs/qa/findings_Q5_r1.md): idempotent like `POST /api/run` -- a deep_search job
    already queued/running for this item responds 409 with its id instead of enqueueing a second
    one, and a `done` investigation for this item from the last 24h is reused (`existing: true`)
    instead of re-running work that already has an answer."""
    try:
        result = services.investigate_item(item_id, body.question)
    except services.InvestigationAlreadyActive as exc:
        raise conflict(
            "חקירה כבר רצה או ממתינה בתור עבור פריט זה",
            detail={"job_id": exc.job.get("id"), "state": exc.job.get("state")},
        ) from exc
    if result is None:
        raise not_found("הפריט לא נמצא")
    return result


@router.post("/items/{item_id}/corroborate")
def corroborate(item_id: int) -> dict:
    """Cross-source corroboration (2026-09-07 user requirement): re-run the deterministic
    corroboration check for this one item on demand and return the same ``corroboration`` object
    shape embedded in every item list/detail payload."""
    result = services.recompute_corroboration(item_id)
    if result is None:
        raise not_found("הפריט לא נמצא")
    return result
