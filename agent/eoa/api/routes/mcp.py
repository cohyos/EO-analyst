"""`GET /api/mcp/servers`, `POST /api/mcp/servers/{id}/ping`, `GET /api/mcp/calls` -- A8 MCP tool
sources (docs/adr/006-mcp-sources.md). Read-only listing/ping + call-accounting, matching the shape
of `agent/eoa/api/routes/llm.py`'s U8 provider endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from eoa.api import services
from eoa.api.errors import APIError

router = APIRouter(tags=["mcp"])


@router.get("/mcp/servers")
def get_mcp_servers() -> dict:
    return services.list_mcp_servers()


@router.post("/mcp/servers/{server_id}/ping")
def post_mcp_server_ping(server_id: str) -> dict:
    try:
        return services.ping_mcp_server(server_id)
    except services.McpServerNotFound as exc:
        raise APIError(404, "not_found", f"שרת MCP לא נמצא: {server_id}") from exc


@router.get("/mcp/calls")
def get_mcp_calls(since: str = Query(default="24h")) -> dict:
    hours_text = since.strip().lower().removesuffix("h") or "24"
    try:
        hours = max(1, int(hours_text))
    except ValueError:
        hours = 24
    return services.summarize_mcp_calls(since_hours=hours)
