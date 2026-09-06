"""Shared httpx link checker for D6 (daily/weekly report links must resolve 200/3xx).

Mirrors the approach used by hand in ``docs/qa/links_r1.csv``/``links_r2.csv`` (fetch each URL,
record its HTTP status), but as a reusable, bounded-concurrency function: <=6 concurrent requests,
15s timeout each, HEAD first falling back to GET (some sites 405 on HEAD). A small allow-list of
domains already known (from prior QA rounds) to answer real browsers with 200 but bot traffic with
a Cloudflare/WAF 403 keeps those from tanking the score every round for a reason this pipeline
cannot fix (see ``eoa.fetch.sanitize.detect_block_page``, the same phenomenon at fetch time).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

#: Hosts known (from docs/qa/findings_Q4_r*.md link audits) to answer a bot/QA-network HEAD/GET
#: with a Cloudflare "Attention Required" 403 while serving real browsers fine -- treated as an
#: acceptable (non-failing) status for this host only, not a general "403 is fine" rule.
DEFAULT_ARTIFACT_DOMAIN_ALLOWLIST: frozenset[str] = frozenset(
    {
        "www.safran-group.com",
        "safran-group.com",
    }
)

_TIMEOUT_S = 15.0
_MAX_CONCURRENT = 6


@dataclass
class LinkResult:
    url: str
    status: int | None
    ok: bool
    note: str = ""


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


async def _check_one(client: httpx.AsyncClient, url: str, allowlist: frozenset[str], sem: asyncio.Semaphore) -> LinkResult:
    async with sem:
        try:
            resp = await client.head(url, timeout=_TIMEOUT_S, follow_redirects=True)
            if resp.status_code in (405, 403) and resp.status_code != 200:
                resp = await client.get(url, timeout=_TIMEOUT_S, follow_redirects=True)
        except httpx.HTTPError as exc:
            return LinkResult(url=url, status=None, ok=False, note=f"request error: {exc.__class__.__name__}")
        status = resp.status_code
        ok = 200 <= status < 400
        if not ok and _host(url) in allowlist and status == 403:
            return LinkResult(url=url, status=status, ok=True, note="allow-listed domain (known WAF-vs-browser gap)")
        return LinkResult(url=url, status=status, ok=ok)


async def check_links_async(
    urls: list[str], *, allowlist: frozenset[str] = DEFAULT_ARTIFACT_DOMAIN_ALLOWLIST
) -> list[LinkResult]:
    sem = asyncio.Semaphore(_MAX_CONCURRENT)
    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 (EO-Analyst QA link checker)"}) as client:
        return await asyncio.gather(*(_check_one(client, u, allowlist, sem) for u in urls))


def check_links(urls: list[str], *, allowlist: frozenset[str] = DEFAULT_ARTIFACT_DOMAIN_ALLOWLIST) -> list[LinkResult]:
    """Synchronous wrapper -- safe to call from a plain script/test (no running event loop)."""
    if not urls:
        return []
    return asyncio.run(check_links_async(urls, allowlist=allowlist))
