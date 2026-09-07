"""`GET/POST /api/investigations`, `WS /ws/investigations/{job_id}`."""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from eoa.api import services
from eoa.api.errors import bad_request, not_found
from eoa.config import settings
from eoa.investigations import links

log = structlog.get_logger(__name__)

router = APIRouter(tags=["investigations"])
ws_router = APIRouter()


class NewInvestigationRequest(BaseModel):
    # Q2-9: bounded so an oversized question can't be used to force an
    # unreasonably large deep-search/LLM-context payload.
    question: str = Field(..., max_length=2000)
    item_id: int | None = None


@router.get("/investigations")
def list_investigations(limit: int = Query(20, ge=1, le=200)) -> list[dict]:
    return services.list_investigations(limit=limit)


@router.post("/investigations")
def new_investigation(body: NewInvestigationRequest) -> dict:
    """U12 "חקירה חדשה": start a free-standing investigation from a typed question."""
    if not body.question or not body.question.strip():
        raise bad_request("יש להקליד שאלה")
    job_id = services.start_investigation(body.question, body.item_id)
    if job_id is None:
        raise not_found("הפריט לא נמצא")
    return {"job_id": job_id}


@router.post("/investigations/{job_id}/expand")
def expand_investigation(job_id: int) -> dict:
    """U12 "הרחב חקירה (תקציב נוסף)": re-run with double budget + prior findings as context."""
    new_job_id = services.expand_investigation(job_id)
    if new_job_id is None:
        raise not_found("החקירה לא נמצאה")
    return {"job_id": new_job_id}


@router.get("/investigations/{job_id}")
def get_investigation(job_id: int) -> dict:
    investigation = services.get_investigation(job_id)
    if investigation is None:
        raise not_found("החקירה לא נמצאה")
    # R10-links: trigger item / lineage / citing-reports, in one nested object so the detail page
    # doesn't need a second round trip (see eoa.investigations.links for how each part is derived).
    investigation["provenance"] = links.investigation_provenance(job_id)
    return investigation


@router.get("/items/{item_id}/investigations")
def get_item_investigations(item_id: int) -> list[dict]:
    """R10-links: an item's own investigations with outcome/confidence/lineage pointers -- the
    same rows `ItemDetailPage`'s "חקירות עומק" block renders, as a standalone endpoint so other
    callers (and this endpoint's own tests) don't need the full `/items/{id}` payload."""
    result = links.item_investigations(item_id)
    if result is None:
        raise not_found("הפריט לא נמצא")
    return result


@router.get("/reports/{report_id}/investigations")
def get_report_investigations(report_id: int) -> list[dict]:
    """R10-links: the investigations a report's own "חקירות עומק" section shows -- feeds
    `ReportsPage`'s "חקירות בדוח" side list."""
    result = links.investigations_for_report(report_id)
    if result is None:
        raise not_found("הדוח לא נמצא")
    return result


@router.post("/investigations/{job_id}/stop")
def stop_investigation(job_id: int) -> dict:
    ok = services.stop_investigation(job_id)
    if not ok:
        raise not_found("החקירה לא נמצאה")
    return {"ok": True}


@ws_router.websocket("/ws/investigations/{job_id}")
async def ws_investigation(ws: WebSocket, job_id: int) -> None:
    """Push new `investigation_log` rows for `job_id` as they arrive."""
    await ws.accept()
    last_log_id = await run_in_threadpool(services.latest_investigation_log_id, job_id)
    try:
        while True:
            new_logs, last_log_id = await run_in_threadpool(
                services.investigation_log_since, job_id, last_log_id
            )
            for row in new_logs:
                await ws.send_json(row)
            await asyncio.sleep(settings().api.status_push_seconds)
    except WebSocketDisconnect:
        log.debug("ws_investigation.disconnected", job_id=job_id)
