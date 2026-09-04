"""`GET /api/status` and `WS /ws/status` -- service health, resource gate, pipeline state."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

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
    # `gate().status()` reads GPU/RAM telemetry (nvidia-smi / a Windows
    # `powershell` subprocess / an Ollama HTTP call) synchronously -- real
    # blocking I/O that must not run inline on the event loop the same way
    # the DB-backed calls above are already threadpooled.
    gate_status = await run_in_threadpool(lambda: gate().status())
    return {
        "at": datetime.now(tz=UTC).isoformat(),
        "services": services_health,
        "gate": gate_status,
        "pipeline": pipeline,
    }


def _dumps(payload: Any) -> str:
    """`json.dumps` with `default=str` so a stray `datetime`/`Decimal` never breaks the socket.

    `WebSocket.send_json` (starlette) calls plain `json.dumps` with no
    `default=`. A raw DB value that slips through (e.g. a `jobs` row's
    `started_at` on an in-flight `current_job`, or a `run_log` row's
    `heartbeat_at`) raises `TypeError` there -- and since that happens
    outside any `try/except WebSocketDisconnect`, the exception was
    previously fatal to the whole handler, killing the socket immediately
    after `accept()` on the very first tick that hit it. `services.py`'s
    status functions now serialise their own datetime columns, but this is
    kept as a second line of defense for anything that isn't covered yet.
    """
    return json.dumps(payload, default=str, separators=(",", ":"), ensure_ascii=False)


async def _send_json(ws: WebSocket, payload: Any) -> None:
    await ws.send_text(_dumps(payload))


@router.get("/status")
async def get_status() -> dict:
    return await _status_payload()


@ws_router.websocket("/ws/status")
async def ws_status(ws: WebSocket) -> None:
    """Push the status payload every `api.status_push_seconds`, plus new `run_log` lines as they arrive."""
    await ws.accept()

    # Send a first frame right away, before anything else can block or
    # fail, so a client that only waits for the initial message never
    # times out against an otherwise-healthy connection.
    try:
        await _send_json(ws, await _status_payload())
    except WebSocketDisconnect:
        log.debug("ws_status.disconnected")
        return
    except Exception as exc:
        log.warning("ws_status.first_frame_failed", error=repr(exc))

    try:
        last_log_id = await run_in_threadpool(services.latest_run_log_id)
    except Exception as exc:
        log.warning("ws_status.init_failed", error=repr(exc))
        last_log_id = 0

    while True:
        try:
            await asyncio.sleep(settings().api.status_push_seconds)
        except WebSocketDisconnect:
            log.debug("ws_status.disconnected")
            return

        try:
            await _send_json(ws, await _status_payload())
            new_logs, last_log_id = await run_in_threadpool(services.run_log_since, last_log_id)
            for row in new_logs:
                await _send_json(ws, {"type": "log", **row})
        except WebSocketDisconnect:
            log.debug("ws_status.disconnected")
            return
        except Exception as exc:
            # A single bad tick (DB hiccup, telemetry error, an unexpected
            # non-serialisable value) must never take the socket down --
            # log it and keep pushing on the next tick.
            log.warning("ws_status.tick_failed", error=repr(exc))
