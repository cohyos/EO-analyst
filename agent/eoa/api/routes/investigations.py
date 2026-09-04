"""`GET/POST /api/investigations`, `WS /ws/investigations/{job_id}`."""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from eoa.api import services
from eoa.api.errors import not_found
from eoa.config import settings

log = structlog.get_logger(__name__)

router = APIRouter(tags=["investigations"])
ws_router = APIRouter()


@router.get("/investigations")
def list_investigations(limit: int = Query(20, ge=1, le=200)) -> list[dict]:
    return services.list_investigations(limit=limit)


@router.get("/investigations/{job_id}")
def get_investigation(job_id: int) -> dict:
    investigation = services.get_investigation(job_id)
    if investigation is None:
        raise not_found("החקירה לא נמצאה")
    return investigation


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
            new_logs, last_log_id = await run_in_threadpool(services.investigation_log_since, job_id, last_log_id)
            for row in new_logs:
                await ws.send_json(row)
            await asyncio.sleep(settings().api.status_push_seconds)
    except WebSocketDisconnect:
        log.debug("ws_investigation.disconnected", job_id=job_id)
