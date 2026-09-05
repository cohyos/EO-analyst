"""MCP server: patent search -- EPO Open Patent Services (OPS) and USPTO PatentsView (A8,
docs/adr/006-mcp-sources.md).

* EPO OPS: OAuth2 client-credentials flow against ``EPO_OPS_KEY``/``EPO_OPS_SECRET`` (confirmed
  live 2026-09-06: ``POST https://ops.epo.org/3.2/auth/accesstoken`` with no credentials returns
  ``401 "Client identifier is required"`` -- the endpoint and auth shape are real; a token is
  cached in-process for its reported lifetime and refreshed on expiry/401).
* USPTO PatentsView: the modern (2023+) Search API at ``https://search.patentsview.org/api/v1/``,
  API-key auth via ``X-Api-Key``. This project's dev/CI network could not resolve
  ``search.patentsview.org`` (DNS failure, not an HTTP error) while ``api.patentsview.org`` (the
  legacy host) did resolve but serves a docs front-end, not raw JSON, at the paths tried --
  **unverified live; the query shape below follows PatentsView's documented Search API contract
  and may need ``PATENTSVIEW_API_BASE`` adjusted if your network resolves a different host.**
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from eoa.mcp_servers._common import http_get_json, http_post_form, json_out, not_configured

mcp = FastMCP("eoa-patents")

EPO_OPS_TOKEN_URL = "https://ops.epo.org/3.2/auth/accesstoken"
EPO_OPS_SEARCH_URL = "https://ops.epo.org/3.2/rest-services/published-data/search"
DEFAULT_PATENTSVIEW_BASE = "https://search.patentsview.org/api/v1"

_epo_token: str | None = None
_epo_token_expiry: float = 0.0


@mcp.tool()
def ping() -> str:
    """Liveness check: reports whether each provider's credentials are configured (no network call)."""
    return json_out(
        {
            "epo_ops_configured": bool(os.environ.get("EPO_OPS_KEY") and os.environ.get("EPO_OPS_SECRET")),
            "patentsview_configured": bool(os.environ.get("PATENTSVIEW_API_KEY")),
        }
    )


def _epo_token_value() -> str | None:
    global _epo_token, _epo_token_expiry
    key, secret = os.environ.get("EPO_OPS_KEY", ""), os.environ.get("EPO_OPS_SECRET", "")
    if not key or not secret:
        return None
    if _epo_token and time.monotonic() < _epo_token_expiry:
        return _epo_token
    basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
    resp = http_post_form(
        EPO_OPS_TOKEN_URL,
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {basic}"},
    )
    # EPO OPS returns XML on auth errors and JSON is not guaranteed by this endpoint; _common's
    # http_post_form still tries json() first and falls back to raw text, handled below.
    data = resp.get("json")
    if resp["status"] != 200 or not isinstance(data, dict) or "access_token" not in data:
        return None
    _epo_token = str(data["access_token"])
    _epo_token_expiry = time.monotonic() + max(60, int(data.get("expires_in", 1200)) - 30)
    return _epo_token


@mcp.tool()
def epo_ops_search(query: str, limit: int = 20) -> str:
    """EPO Open Patent Services published-data search (CQL query syntax, e.g.
    ``'ti=\"night vision\" AND pd within \"2023-2026\"'``). Requires ``EPO_OPS_KEY``/
    ``EPO_OPS_SECRET`` (OAuth2 client-credentials app, free registration at ops.epo.org)."""
    token = _epo_token_value()
    if token is None:
        return not_configured("EPO_OPS_KEY", "EPO_OPS_SECRET")
    resp = http_get_json(
        EPO_OPS_SEARCH_URL,
        params={"q": query, "Range": f"1-{max(1, min(limit, 100))}"},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    if resp["status"] != 200:
        return json_out({"error": f"EPO OPS returned HTTP {resp['status']}", "body": resp.get("text")})
    return json_out(_extract_epo_results(resp["json"] or {}))


def _extract_epo_results(data: dict[str, Any]) -> dict[str, Any]:
    """EPO OPS's JSON is deeply nested (`ops:world-patent-data` -> ... ); pull out the handful of
    fields this project cares about, tolerant of a missing/renamed branch (returns an empty list
    rather than raising) since the exact shape is unverified live from this sandbox."""
    try:
        biblio = data["ops:world-patent-data"]["ops:biblio-search"]
        total = int(biblio.get("@total-result-count", 0))
        results = biblio.get("ops:search-result", {}).get("ops:publication-reference", [])
        if isinstance(results, dict):
            results = [results]
        docs = []
        for r in results:
            doc_id = r.get("document-id", {})
            docs.append(
                {
                    "country": doc_id.get("country", {}).get("$"),
                    "doc_number": doc_id.get("doc-number", {}).get("$"),
                    "kind": doc_id.get("kind", {}).get("$"),
                }
            )
        return {"total_result_count": total, "results": docs}
    except (KeyError, TypeError):
        return {"total_result_count": None, "results": [], "raw": data}


@mcp.tool()
def patentsview_search(query: str, assignee: str = "", limit: int = 20) -> str:
    """USPTO PatentsView keyword/assignee search. Requires ``PATENTSVIEW_API_KEY`` (free, self-serve
    at patentsview.org). See this module's docstring for the live-verification caveat on the host."""
    api_key = os.environ.get("PATENTSVIEW_API_KEY", "")
    if not api_key:
        return not_configured("PATENTSVIEW_API_KEY")
    base = (os.environ.get("PATENTSVIEW_API_BASE") or DEFAULT_PATENTSVIEW_BASE).rstrip("/")
    query_obj: dict[str, Any] = {"_text_any": {"patent_title": query}}
    if assignee:
        query_obj = {"_and": [query_obj, {"_text_any": {"assignees.assignee_organization": assignee}}]}
    resp = http_get_json(
        f"{base}/patent/",
        params={
            "q": _json_compact(query_obj),
            "f": _json_compact(["patent_id", "patent_title", "patent_date", "assignees.assignee_organization"]),
            "o": _json_compact({"size": max(1, min(limit, 100))}),
        },
        headers={"X-Api-Key": api_key},
    )
    if resp["status"] != 200:
        return json_out({"error": f"PatentsView returned HTTP {resp['status']}", "body": resp.get("text")})
    data = resp["json"] or {}
    return json_out({"total_hits": data.get("total_hits"), "patents": data.get("patents") or []})


def _json_compact(obj: Any) -> str:
    import json

    return json.dumps(obj, separators=(",", ":"))


if __name__ == "__main__":
    mcp.run(transport="stdio")
