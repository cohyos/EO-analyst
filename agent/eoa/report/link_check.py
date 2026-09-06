"""Round 4 (2026-09-06, W7): cheap outbound link liveness + staleness probe.

Before a URL reaches a report table (the open-tenders board, the tender-forecast table, or the
sources appendix), the report layer wants some assurance the link is (a) still reachable and (b)
not obviously an archived/stale notice masquerading as a live one -- the concrete complaint was an
"open tender" row linking to a 2015 LITENING notice. This module makes a best-effort, tightly
bounded attempt at both:

  - **liveness**: a GET request (many tender portals reject HEAD) through the existing SSRF guard
    (:func:`eoa.fetch.remote.assert_public_http_url`) -- 4xx/5xx or a network error means "dead".
  - **staleness**: a lone, old (> :data:`STALE_YEARS` years) 4-digit year in the page's own
    ``<title>`` with no more-recent year alongside it -- a cheap heuristic, not a real date parser,
    deliberately: this only needs to catch the "obviously an old archived notice" case, not do
    general-purpose date extraction.

A slow/unreachable host must never block report generation: every check is bounded by
:data:`PER_REQUEST_TIMEOUT_S`, the whole batch by :data:`TOTAL_BUDGET_S` (wall-clock, regardless of
how many URLs are queued), with bounded concurrency (:data:`MAX_CONCURRENCY`). A URL that could not
be checked within the budget comes back ``checked=False`` -- the caller renders it as "לא אומת"
rather than dropping it (an unverifiable link is not the same claim as a dead one).

Callers should build one cache (a plain ``dict[str, LinkCheckResult]``) per report run and reuse it
across every table that might reference the same URL (an open tender and its own forecast
evidence, for instance) -- :func:`check_urls` both reads and writes the cache it is given.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from dataclasses import dataclass

import httpx
import structlog

from eoa.errors import FetchError
from eoa.fetch.remote import assert_public_http_url

log = structlog.get_logger(__name__)

PER_REQUEST_TIMEOUT_S = 8.0
TOTAL_BUDGET_S = 60.0
MAX_CONCURRENCY = 6
STALE_YEARS = 3

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


@dataclass
class LinkCheckResult:
    url: str
    #: False when the check never actually ran (budget exhausted before this url's turn) --
    #: render as "לא אומת", never as dead.
    checked: bool = False
    alive: bool | None = None
    status_code: int | None = None
    #: best-effort "this looks like an old archived notice" signal -- only meaningful when
    #: ``checked and alive``.
    stale: bool = False
    title: str | None = None
    reason: str | None = None


def _extract_title(html_text: str) -> str | None:
    match = _TITLE_RE.search(html_text or "")
    if not match:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return title[:300] or None


def _looks_stale(title: str | None, *, today: dt.date) -> bool:
    """A lone old year in the title, with no more-recent year alongside it, is the whole heuristic
    -- see the module docstring for why this stays deliberately simple."""
    if not title:
        return False
    years = [int(y) for y in _YEAR_RE.findall(title)]
    if not years:
        return False
    cutoff = today.year - STALE_YEARS
    return max(years) <= cutoff


async def _check_one(
    client: httpx.AsyncClient, url: str, semaphore: asyncio.Semaphore, *, today: dt.date
) -> LinkCheckResult:
    async with semaphore:
        try:
            assert_public_http_url(url)
        except FetchError as exc:
            return LinkCheckResult(url=url, checked=True, alive=False, reason=f"blocked: {exc}"[:200])
        try:
            resp = await client.get(url, timeout=PER_REQUEST_TIMEOUT_S, follow_redirects=True)
        except (httpx.HTTPError, OSError) as exc:
            return LinkCheckResult(url=url, checked=True, alive=False, reason=f"error: {exc}"[:200])
        alive = 200 <= resp.status_code < 400
        title = None
        stale = False
        if alive and "html" in (resp.headers.get("content-type") or "").lower():
            title = _extract_title(resp.text)
            stale = _looks_stale(title, today=today)
        return LinkCheckResult(
            url=url, checked=True, alive=alive, status_code=resp.status_code, stale=stale, title=title
        )


async def _check_urls_async(
    urls: list[str],
    *,
    per_request_timeout: float,
    total_budget: float,
    max_concurrency: int,
    today: dt.date,
) -> dict[str, LinkCheckResult]:
    semaphore = asyncio.Semaphore(max_concurrency)
    results: dict[str, LinkCheckResult] = {}
    async with httpx.AsyncClient() as client:
        tasks = {url: asyncio.ensure_future(_check_one(client, url, semaphore, today=today)) for url in urls}
        _done, pending = await asyncio.wait(tasks.values(), timeout=total_budget)
        for pending_task in pending:
            pending_task.cancel()
        for url, task in tasks.items():
            if task in pending:
                results[url] = LinkCheckResult(url=url, checked=False)
                continue
            try:
                results[url] = task.result()
            except Exception as exc:  # pragma: no cover -- defensive, _check_one itself never raises
                log.debug("link_check_task_failed", url=url[:200], error=str(exc)[:160])
                results[url] = LinkCheckResult(url=url, checked=True, alive=False, reason="internal_error")
    return results


def check_urls(
    urls: list[str],
    *,
    cache: dict[str, LinkCheckResult] | None = None,
    per_request_timeout: float = PER_REQUEST_TIMEOUT_S,
    total_budget: float = TOTAL_BUDGET_S,
    max_concurrency: int = MAX_CONCURRENCY,
    today: dt.date | None = None,
) -> dict[str, LinkCheckResult]:
    """Check every url in ``urls`` not already present in ``cache`` (deduped), merge the results
    into ``cache`` (a fresh dict when ``None``), and return it. Never raises -- any failure for an
    individual url comes back as a non-alive :class:`LinkCheckResult` on that url only; a failure
    setting up the whole batch (e.g. the event loop itself) degrades to every url unchecked."""
    cache = cache if cache is not None else {}
    today = today or dt.date.today()
    pending = [u for u in dict.fromkeys(urls) if u and u not in cache]
    if not pending:
        return cache
    try:
        results = asyncio.run(
            _check_urls_async(
                pending,
                per_request_timeout=per_request_timeout,
                total_budget=total_budget,
                max_concurrency=max_concurrency,
                today=today,
            )
        )
    except Exception as exc:
        log.warning("link_check_batch_failed", error=str(exc)[:200], url_count=len(pending))
        results = {u: LinkCheckResult(url=u, checked=False) for u in pending}
    cache.update(results)
    return cache
