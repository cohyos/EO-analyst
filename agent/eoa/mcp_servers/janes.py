"""MCP server: Janes Data Services API wrapper (A8, docs/adr/006-mcp-sources.md).

The user has a Janes subscription (approved 2026-09-05, see docs/PLAN_WINDOWS_NATIVE.md's A8 row).
Janes' public developer portal (``https://developer.janes.com``) resolves and serves a docs
front-end, but the exact REST paths/response shapes for a given subscription's entitlements are
not something this project could confirm without live subscription credentials -- **every endpoint
template below is a best-effort construction from Janes' publicly documented API surface
(equipment/news/markets/budgets/events) and is explicitly marked "verify against your
subscription's API docs"** in each tool's docstring, per the task's own instruction. The client
itself (auth header, base URL, path templating, JSON passthrough) is a clean, generic REST client
that will keep working even if a path segment needs adjusting -- change ``JANES_API_BASE`` and the
per-tool ``_path`` constants, nothing else.

Never logs ``JANES_API_KEY``. Every tool degrades to a graceful "not configured" JSON error when
the key is missing -- no automation is possible without it, and that is stated plainly rather than
silently returning nothing.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from eoa.mcp_servers._common import http_get_json, json_out, not_configured

mcp = FastMCP("eoa-janes")

DEFAULT_BASE = "https://developer.janes.com/api"


def _base() -> str:
    return (os.environ.get("JANES_API_BASE") or DEFAULT_BASE).rstrip("/")


def _headers() -> dict[str, str] | None:
    key = os.environ.get("JANES_API_KEY", "")
    if not key:
        return None
    # Most enterprise data-service APIs (Janes included, per its developer portal's stated OAuth2/
    # API-key patterns) accept either an `Authorization: Bearer <key>` header or a subscription-key
    # header; both are sent so a subscription using either convention still authenticates.
    return {"Authorization": f"Bearer {key}", "Ocp-Apim-Subscription-Key": key}


def _call(path: str, params: dict[str, Any]) -> str:
    headers = _headers()
    if headers is None:
        return not_configured("JANES_API_KEY")
    url = f"{_base()}/{path.lstrip('/')}"
    resp = http_get_json(
        url, params={k: v for k, v in params.items() if v not in (None, "")}, headers=headers
    )
    if resp["status"] != 200:
        return json_out(
            {
                "error": f"janes API returned HTTP {resp['status']}",
                "body": resp.get("text"),
                "hint": "verify JANES_API_BASE and this tool's path template against your subscription's API docs",
            }
        )
    return json_out(resp["json"] or {})


@mcp.tool()
def ping() -> str:
    """Liveness check: reports whether ``JANES_API_KEY`` is configured (no network call)."""
    return json_out({"configured": bool(os.environ.get("JANES_API_KEY"))})


@mcp.tool()
def janes_search(query: str, limit: int = 20) -> str:
    """General cross-domain Janes search. VERIFY the path template (``search``) against your
    subscription's API docs -- this is the generic entry point most Janes data products expose."""
    return _call("search", {"q": query, "limit": limit})


@mcp.tool()
def janes_equipment(query: str, category: str = "", limit: int = 20) -> str:
    """Janes Equipment data (platforms, sensors, EO/IR systems, specifications). VERIFY the path
    template (``equipment/search``) against your subscription's API docs."""
    return _call("equipment/search", {"q": query, "category": category, "limit": limit})


@mcp.tool()
def janes_news(query: str, country: str = "", limit: int = 20) -> str:
    """Janes defense news/intelligence articles. VERIFY the path template (``news/search``)
    against your subscription's API docs."""
    return _call("news/search", {"q": query, "country": country, "limit": limit})


@mcp.tool()
def janes_markets(query: str, region: str = "", limit: int = 20) -> str:
    """Janes Markets Forecast data (program/market sizing, competitive landscape). VERIFY the path
    template (``markets/search``) against your subscription's API docs."""
    return _call("markets/search", {"q": query, "region": region, "limit": limit})


@mcp.tool()
def janes_budgets(country: str, year: str = "", limit: int = 20) -> str:
    """Janes Defence Budgets data by country/year. VERIFY the path template (``budgets/search``)
    against your subscription's API docs."""
    return _call("budgets/search", {"country": country, "year": year, "limit": limit})


@mcp.tool()
def janes_events(query: str = "", country: str = "", limit: int = 20) -> str:
    """Janes defense/security events calendar. VERIFY the path template (``events/search``)
    against your subscription's API docs."""
    return _call("events/search", {"q": query, "country": country, "limit": limit})


if __name__ == "__main__":
    mcp.run(transport="stdio")
