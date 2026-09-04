"""`GET /api/status` and `WS /ws/status` -- service health, resource gate, pipeline state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from eoa.api import services
from eoa.config import settings
from eoa.resources.gate import gate

log = structlog.get_logger(__name__)

router = APIRouter(tags=["status"])
ws_router = APIRouter()


async def _status_payload() -> dict:
    services_health = await run_in_threadpool(services.services_status)
    pipeline = await run_in_threadpool(services.pipeline_status)
    return {
        "at": datetime.now(tz=UTC).isoformat(),
        "services": services_health,
        "gate": gate().status(),
        "pipeline": pipeline,
    }


@router.get("/status")
async def get_status() -> dict:
    return await _status_payload()


@ws_router.websocket("/ws/status")
async def ws_status(ws: WebSocket) -> None:
    """Push the status payload every `api.status_push_seconds`, plus new `run_log` lines as they arrive."""
    await ws.accept()
    last_log_id = await run_in_threadpool(services.latest_run_log_id)
    try:
        while True:
            await ws.send_json(await _status_payload())
            new_logs, last_log_id = await run_in_threadpool(services.run_log_since, last_log_id)
            for row in new_logs:
                await ws.send_json({"type": "log", **row})
            await asyncio.sleep(settings().api.status_push_seconds)
    except WebSocketDisconnect:
        log.debug("ws_status.disconnected")
