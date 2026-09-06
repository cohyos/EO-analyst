"""MCP server: US defense procurement / arms-transfer sources (A8, docs/adr/006-mcp-sources.md).

Tools (all read-only, no API side effects):

* ``ping``                        -- liveness check.
* ``sam_gov_search``              -- SAM.gov Opportunities API v2 (needs ``SAM_GOV_API_KEY``).
* ``usaspending_awards_by_psc``   -- USAspending.gov awards by Product/Service Code (no key).
* ``dsca_major_arms_sales``       -- DSCA press releases listing (no key; best-effort HTML scrape --
  see the function docstring for the live caveat found 2026-09-06).
* ``federal_register_search``     -- Federal Register full-text search (no key).
* ``congress_gov_search``         -- Congress.gov bill/report search (needs ``CONGRESS_GOV_API_KEY``).

Run as ``python -m eoa.mcp_servers.procurement`` (stdio transport, matching ``config/mcp.yaml``).
"""

from __future__ import annotations

import os
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from eoa.config import settings
from eoa.mcp_servers._common import http_get_json, http_post_json, json_out, not_configured, truncate_list

mcp = FastMCP("eoa-procurement")

SAM_GOV_SEARCH_URL = "https://api.sam.gov/opportunities/v2/search"
USASPENDING_AWARDS_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
DSCA_MAJOR_ARMS_SALES_URL = "https://www.dsca.mil/press-media/major-arms-sales"
FEDERAL_REGISTER_SEARCH_URL = "https://www.federalregister.gov/api/v1/articles.json"
CONGRESS_GOV_BILL_URL = "https://api.congress.gov/v3/bill"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


@mcp.tool()
def ping() -> str:
    """Liveness check -- returns "pong" with no network calls."""
    return "pong"


@mcp.tool()
def sam_gov_search(
    keyword: str = "",
    naics: str = "",
    psc: str = "",
    posted_from: str = "",
    posted_to: str = "",
    limit: int = 20,
) -> str:
    """Search SAM.gov contract opportunities (solicitations/RFIs/RFPs) by keyword/NAICS/PSC/date
    range. ``posted_from``/``posted_to`` are ``MM/dd/yyyy``; both required by the underlying API --
    default to the trailing 90 days when omitted. Requires ``SAM_GOV_API_KEY`` (free, self-serve at
    sam.gov)."""
    api_key = os.environ.get("SAM_GOV_API_KEY", "")
    if not api_key:
        return not_configured("SAM_GOV_API_KEY")
    if not posted_from or not posted_to:
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        posted_to = posted_to or now.strftime("%m/%d/%Y")
        posted_from = posted_from or (now - timedelta(days=90)).strftime("%m/%d/%Y")
    params: dict[str, Any] = {
        "postedFrom": posted_from,
        "postedTo": posted_to,
        "limit": max(1, min(limit, 100)),
    }
    if keyword:
        params["title"] = keyword
    if naics:
        params["ncode"] = naics
    if psc:
        params["ccode"] = psc
    # Q2-15 (2026-09-06): api.sam.gov sits behind api.data.gov's key infrastructure, which accepts
    # the key via either the `api_key` query parameter or an `X-Api-Key` header -- the header is
    # used here so the key is never embedded in a URL that could end up in access logs, an
    # exception message, or `mcp_calls.error`.
    resp = http_get_json(SAM_GOV_SEARCH_URL, params=params, headers={"X-Api-Key": api_key})
    if resp["status"] != 200:
        return json_out({"error": f"sam.gov returned HTTP {resp['status']}", "body": resp.get("text")})
    data = resp["json"] or {}
    opportunities = truncate_list(data.get("opportunitiesData") or [], limit)
    return json_out(
        {
            "total_records": data.get("totalRecords"),
            "opportunities": [
                {
                    "notice_id": o.get("noticeId"),
                    "title": o.get("title"),
                    "type": o.get("type"),
                    "posted_date": o.get("postedDate"),
                    "response_deadline": o.get("responseDeadLine"),
                    "naics_code": o.get("naicsCode"),
                    "psc_code": o.get("classificationCode"),
                    "agency": o.get("fullParentPathName"),
                    "url": o.get("uiLink"),
                }
                for o in opportunities
            ],
        }
    )


@mcp.tool()
def usaspending_awards_by_psc(
    psc_codes: list[str] | None = None,
    start_date: str = "",
    end_date: str = "",
    award_type_codes: list[str] | None = None,
    limit: int = 20,
) -> str:
    """USAspending.gov federal award search filtered by Product/Service Code (PSC). No API key.
    ``psc_codes`` defaults to this project's EO/IR watch list (``config/mcp.yaml``:
    ``procurement.psc_codes_eo_ir`` -- night vision 5855, optical instruments 6650, aircraft
    gunnery fire control 1270, radar 5840/5841). ``award_type_codes`` defaults to contracts
    (A/B/C/D)."""
    codes = psc_codes or settings().mcp.procurement.psc_codes_eo_ir
    if not start_date or not end_date:
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        end_date = end_date or now.strftime("%Y-%m-%d")
        start_date = start_date or (now - timedelta(days=365)).strftime("%Y-%m-%d")
    body = {
        "filters": {
            "psc_codes": list(codes),
            "time_period": [{"start_date": start_date, "end_date": end_date}],
            "award_type_codes": list(award_type_codes or ["A", "B", "C", "D"]),
        },
        "fields": [
            "Award ID",
            "Recipient Name",
            "Award Amount",
            "Start Date",
            "End Date",
            "Awarding Agency",
            "Awarding Sub Agency",
            "Description",
        ],
        "limit": max(1, min(limit, 100)),
        "page": 1,
    }
    resp = http_post_json(USASPENDING_AWARDS_URL, json_body=body)
    if resp["status"] != 200:
        return json_out({"error": f"usaspending returned HTTP {resp['status']}", "body": resp.get("text")})
    data = resp["json"] or {}
    return json_out({"psc_codes": codes, "awards": data.get("results") or []})


@mcp.tool()
def dsca_major_arms_sales(keyword: str = "", limit: int = 10) -> str:
    """DSCA (Defense Security Cooperation Agency) major-arms-sales press releases -- best-effort
    HTML listing scrape, no API key.

    **Live caveat (checked 2026-09-06):** dsca.mil sits behind Akamai bot protection that returned
    HTTP 403 to every request from this project's dev/CI network (browser User-Agent made no
    difference) -- likely an IP-reputation block on cloud/CI egress ranges rather than anything
    this client does wrong. This tool still attempts the fetch and returns the real HTTP status on
    failure (never a fabricated result); it may simply work from the user's own residential/VPN
    network. ``federal_register_search`` below independently indexes the same arms-sales
    notifications (search "arms sales notification") and is confirmed working from this network --
    prefer it as the primary source for DSCA-adjacent content until this is resolved.
    """
    resp = http_get_json(
        DSCA_MAJOR_ARMS_SALES_URL, headers={"User-Agent": _BROWSER_UA, "Accept": "text/html"}
    )
    if resp["status"] != 200:
        return json_out(
            {
                "error": f"dsca.mil returned HTTP {resp['status']}",
                "hint": "likely Akamai bot-protection on this network; see this tool's docstring",
            }
        )
    html = resp.get("text") or ""
    items = _parse_dsca_listing(html, keyword, limit)
    return json_out({"releases": items})


def _parse_dsca_listing(html: str, keyword: str, limit: int) -> list[dict[str, str]]:
    """Very small, dependency-free extraction of ``<a href="...">Title</a>`` pairs under the press
    release listing -- deliberately tolerant (no lxml/bs4 dependency added just for this), since
    the exact markup is unverified live (see ``dsca_major_arms_sales``'s docstring)."""
    out: list[dict[str, str]] = []
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>([^<]{8,200})</a>', html, re.IGNORECASE):
        href, title = m.group(1), m.group(2).strip()
        if "press-media" not in href and "major-arms-sales" not in href:
            continue
        if keyword and keyword.lower() not in title.lower():
            continue
        url = href if href.startswith("http") else f"https://www.dsca.mil{href}"
        out.append({"title": title, "url": url})
        if len(out) >= limit:
            break
    return out


@mcp.tool()
def federal_register_search(query: str, agencies: list[str] | None = None, per_page: int = 10) -> str:
    """Federal Register full-text search (defense.gov/State Dept notices, arms sales
    notifications, rulemakings). No API key. ``agencies`` filters by agency slug (e.g.
    ``"defense-department"``)."""
    params: dict[str, Any] = {
        "conditions[term]": query,
        "per_page": max(1, min(per_page, 100)),
        "order": "newest",
    }
    for i, agency in enumerate(agencies or []):
        params[f"conditions[agencies][{i}]"] = agency
    resp = http_get_json(FEDERAL_REGISTER_SEARCH_URL, params=params)
    if resp["status"] != 200:
        return json_out(
            {"error": f"federalregister.gov returned HTTP {resp['status']}", "body": resp.get("text")}
        )
    data = resp["json"] or {}
    results = truncate_list(data.get("results") or [], per_page)
    return json_out(
        {
            "count": data.get("count"),
            "results": [
                {
                    "title": r.get("title"),
                    "type": r.get("type"),
                    "abstract": (r.get("abstract") or "")[:500],
                    "publication_date": r.get("publication_date"),
                    "agencies": [a.get("name") for a in (r.get("agencies") or [])],
                    "html_url": r.get("html_url"),
                }
                for r in results
            ],
        }
    )


@mcp.tool()
def congress_gov_search(query: str, congress: int | None = None, limit: int = 10) -> str:
    """Congress.gov bill search (NDAA provisions, defense authorization/appropriations language).
    Requires ``CONGRESS_GOV_API_KEY`` (free, self-serve at api.congress.gov). Best-effort against
    the documented ``/v3/bill`` listing + ``q``-style term filtering -- verify against the current
    API docs if results look off; the API's search surface has changed shape across versions."""
    api_key = os.environ.get("CONGRESS_GOV_API_KEY", "")
    if not api_key:
        return not_configured("CONGRESS_GOV_API_KEY")
    url = f"{CONGRESS_GOV_BILL_URL}/{congress}" if congress else CONGRESS_GOV_BILL_URL
    params = {"format": "json", "limit": max(1, min(limit, 250)), "q": query}
    # Q2-15 (2026-09-06): same api.data.gov-backed key infrastructure as SAM.gov above -- send the
    # key as a header, never as a query parameter.
    resp = http_get_json(url, params=params, headers={"X-Api-Key": api_key})
    if resp["status"] != 200:
        return json_out({"error": f"congress.gov returned HTTP {resp['status']}", "body": resp.get("text")})
    data = resp["json"] or {}
    bills = truncate_list(data.get("bills") or [], limit)
    return json_out(
        {
            "bills": [
                {
                    "title": b.get("title"),
                    "number": b.get("number"),
                    "type": b.get("type"),
                    "congress": b.get("congress"),
                    "latest_action": (b.get("latestAction") or {}).get("text"),
                    "update_date": b.get("updateDate"),
                    "url": b.get("url"),
                }
                for b in bills
            ]
        }
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
