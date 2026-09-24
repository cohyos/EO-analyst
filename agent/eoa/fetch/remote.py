"""Bridge between the isolated `agent` container (no internet) and the `fetcher` container (egress).

The agent never opens sockets to the internet. Instead it enqueues jobs of kind ``ingest`` or ``fetch_url``
and waits for the fetcher to complete them (jobs table = the only channel). When ``EOA_ROLE`` is not
``agent`` (host dev, tests), the same functions run the work in-process.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import time
from typing import Any
from urllib.parse import urlsplit

import structlog

from eoa.errors import FetchError
from eoa.execution import checkpoint, sleep, timeout_seconds

log = structlog.get_logger(__name__)


def role() -> str:
    return os.environ.get("EOA_ROLE", "host")


def _wait_job(job_id: int, timeout_s: float, poll_s: float = 1.0) -> dict[str, Any]:
    from eoa.db import connection

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with connection() as conn:
            row = conn.execute("SELECT state, result, error FROM jobs WHERE id=%s", (job_id,)).fetchone()
        if row and row["state"] in {"done", "failed", "partial"}:
            if row["state"] == "failed":
                raise FetchError(f"fetcher job {job_id} failed: {row['error']}")
            return row["result"] or {}
        sleep(poll_s)
    raise FetchError(f"fetcher job {job_id} timed out after {int(timeout_s)}s")


def run_ingest_remote(since_days: int = 3, timeout_s: float = 25 * 60, *, poll: bool = False) -> dict[str, Any]:
    """Ingest via the fetcher container (agent role) or in-process (host role). ``poll`` -- the
    daytime light poll (F35, see ``eoa.fetch.service._source_is_due``)."""
    if role() != "agent":
        from eoa.fetch.service import run_ingest

        stats = asyncio.run(_bounded_async(run_ingest(since_days=since_days, poll=poll), timeout_s))
        return {k: v for k, v in vars(stats).items() if isinstance(v, int | float | str | bool)}
    from eoa.memory.relational import enqueue_job

    job_id = enqueue_job("ingest", {"since_days": since_days, "poll": poll}, priority=1)
    log.info("ingest_delegated_to_fetcher", job_id=job_id)
    return _wait_job(job_id, timeout_s, poll_s=5)


def _validate_public_ips(host: str) -> set[str]:
    """Resolve ``host`` and validate every returned address is public.

    Split out of :func:`assert_public_http_url` (Q2-4, 2026-09-06) so callers that
    need to *pin* a later connection to the addresses that were actually validated
    -- see ``_fetch_local``'s redirect loop below -- can get that validated set back
    instead of re-resolving (and thus re-trusting whatever DNS answers on a second,
    later lookup -- the classic TOCTOU/DNS-rebinding gap).
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise FetchError(f"dns failure for {host}: {exc}") from exc
    ips: set[str] = set()
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or not ip.is_global  # also rejects CGNAT/Tailscale 100.64.0.0/10 (Q2-2, 2026-09-06)
        ):
            raise FetchError(f"refusing non-public address for {host}: {ip}")
        ips.add(str(ip))
    if not ips:
        raise FetchError(f"dns resolution returned no usable addresses for {host}")
    return ips


def assert_public_http_url(url: str) -> set[str]:
    """SSRF guard: only http/https, no credentials, standard ports, and a public IP after DNS
    resolution. Returns the validated IP address set for ``url``'s host (Q2-4) -- most callers
    ignore the return value, but ``_fetch_local`` uses it to pin a hop's connection to the
    addresses that were actually checked."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise FetchError(f"refusing non-http(s) url: {url[:120]}")
    if parts.username or parts.password:
        raise FetchError("refusing url with embedded credentials")
    if parts.port not in (None, 80, 443, 8080, 8443):
        raise FetchError(f"refusing unusual port {parts.port}")
    return _validate_public_ips(parts.hostname)


def fetch_remote(url: str, timeout_s: float = 90) -> dict[str, Any]:
    """Fetch + sanitize one URL. Returns dict(text,title,lang,published_at,hidden_text_ratio,encoded_blobs,suspicious)."""
    assert_public_http_url(url)
    if role() != "agent":
        return _fetch_local(url)
    from eoa.memory.relational import enqueue_job

    job_id = enqueue_job("fetch_url", {"url": url}, priority=0)
    return _wait_job(job_id, timeout_s)


def _fetch_local(url: str) -> dict[str, Any]:
    """Fetch ``url`` in-process, closing the Q2-4 TOCTOU/DNS-rebinding gap.

    The old version validated ``url`` once, let `httpx` follow redirects
    internally (resolving + connecting to each hop with zero SSRF checks in
    between), and only re-validated the *final* URL after the whole chain had
    already been fetched -- a malicious/compromised intermediate host, or a
    same-host DNS answer that changes between the check and the connect,
    would already have been reached by the time that check ran.

    Now: every redirect hop is re-validated with :func:`assert_public_http_url`
    *before* it is requested (``fetch_page``'s ``validate_redirect`` hook,
    ``follow_redirects`` disabled internally whenever that hook is given), and
    the connection's actual peer address is compared against the validated IP
    set for that hop (``pin_ips``) -- best-effort where the transport exposes
    it, see `eoa.fetch.html._server_addr`'s docstring for why this can't be a
    hard guarantee with a stock `httpx.AsyncClient`.
    """
    from eoa.fetch.html import fetch_page
    from eoa.fetch.sanitize import extract_clean_text

    initial_ips = assert_public_http_url(url)
    page = asyncio.run(_bounded_async(fetch_page(url, validate_redirect=assert_public_http_url, pin_ips=initial_ips), 90))
    clean = extract_clean_text(page.html, url)
    return {
        "url": url,
        "final_url": page.final_url,
        "text": clean.text,
        "title": clean.title,
        "lang": clean.lang,
        "published_at": clean.published_at.isoformat() if clean.published_at else None,
        "hidden_text_ratio": clean.hidden_text_ratio,
        "encoded_blobs": len(clean.encoded_blobs),
        "suspicious": list(clean.suspicious),
    }


def fetch_raw_remote(
    url: str,
    *,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
    timeout_s: float = 60,
) -> dict[str, Any]:
    """Fetch one URL and return its raw (unsanitized) body -- for structured JSON APIs (tender
    portals, etc.) where HTML sanitization/text extraction (:func:`fetch_remote`) would destroy
    the payload. Never used for arbitrary HTML: callers still must not feed the returned text to
    an LLM without going through ``eoa.llm.ollama_client.wrap_data`` first, same as any other
    fetched content (FR-9 -- this bridge does not change the DATA-not-instructions rule).

    Returns ``{"url", "status", "json" (parsed body or None), "text" (raw body when not JSON)}``.
    Routes through the fetcher container (``fetch_url`` job, payload ``{"url", "raw": true, ...}``)
    when running as the isolated ``agent`` role, exactly like :func:`fetch_remote`; runs in-process
    otherwise (host dev, tests, or the fetcher's own ``serve_fetch_jobs`` loop).
    """
    assert_public_http_url(url)
    if role() != "agent":
        return _fetch_raw_local(url, method=method, json_body=json_body, timeout_s=timeout_s)
    from eoa.memory.relational import enqueue_job

    job_id = enqueue_job(
        "fetch_url",
        {"url": url, "raw": True, "method": method, "json_body": json_body},
        priority=0,
    )
    return _wait_job(job_id, timeout_s)


def _fetch_raw_local(
    url: str, *, method: str = "GET", json_body: dict[str, Any] | None = None, timeout_s: float = 60
) -> dict[str, Any]:
    import httpx

    from eoa.config import settings

    assert_public_http_url(url)
    headers = {"Accept": "application/json", "User-Agent": settings().fetch.user_agent}
    with httpx.Client(timeout=timeout_seconds(timeout_s)) as client:
        if (method or "GET").upper() == "POST":
            r = client.post(url, json=json_body, headers=headers)
        else:
            r = client.get(url, headers=headers)
    r.raise_for_status()
    try:
        data: Any = r.json()
    except ValueError:
        data = None
    return {"url": url, "status": r.status_code, "json": data, "text": None if data is not None else r.text}


def serve_fetch_jobs(poll_s: float = 2.0, stop_after: float | None = None) -> None:
    """Fetcher-side loop: execute ``ingest`` and ``fetch_url`` jobs from the queue."""
    from eoa.memory.relational import claim_next_job, finish_job

    t_end = None if stop_after is None else time.monotonic() + stop_after
    log.info("fetch_job_server_start")
    while t_end is None or time.monotonic() < t_end:
        try:
            job = claim_next_job(["ingest", "fetch_url"])
        except Exception as exc:  # DB hiccup: keep serving
            log.warning("fetch_claim_failed", error=str(exc)[:120])
            time.sleep(poll_s * 3)
            continue
        if not job:
            sleep(poll_s)
            continue
        p = job.get("payload") or {}
        try:
            if job["kind"] == "ingest":
                from eoa.fetch.service import run_ingest

                stats = asyncio.run(
                    run_ingest(
                        since_days=int(p.get("since_days", 3)),
                        poll=bool(p.get("poll")) or p.get("mode") == "poll",
                    )
                )
                finish_job(
                    job["id"],
                    "done",
                    result={k: v for k, v in vars(stats).items() if isinstance(v, int | float | str | bool)},
                )
            elif p.get("raw"):
                finish_job(
                    job["id"],
                    "done",
                    result=_fetch_raw_local(
                        str(p.get("url", "")),
                        method=str(p.get("method") or "GET"),
                        json_body=p.get("json_body"),
                    ),
                )
            else:
                finish_job(job["id"], "done", result=_fetch_local(str(p.get("url", ""))))
        except Exception as exc:
            log.warning("fetch_job_failed", job_id=job["id"], kind=job["kind"], error=str(exc)[:200])
            finish_job(job["id"], "failed", error=str(exc)[:400])


async def _bounded_async(awaitable, default_timeout: float):
    try:
        timeout = timeout_seconds(default_timeout)
    except BaseException:
        awaitable.close()
        raise
    try:
        result = await asyncio.wait_for(awaitable, timeout=timeout)
        checkpoint()
        return result
    except TimeoutError:
        checkpoint()
        raise
