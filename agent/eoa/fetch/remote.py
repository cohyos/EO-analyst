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
        time.sleep(poll_s)
    raise FetchError(f"fetcher job {job_id} timed out after {int(timeout_s)}s")


def run_ingest_remote(since_days: int = 3, timeout_s: float = 25 * 60) -> dict[str, Any]:
    """Ingest via the fetcher container (agent role) or in-process (host role)."""
    if role() != "agent":
        from eoa.fetch.service import run_ingest

        stats = asyncio.run(run_ingest(since_days=since_days))
        return {k: v for k, v in vars(stats).items() if isinstance(v, int | float | str | bool)}
    from eoa.memory.relational import enqueue_job

    job_id = enqueue_job("ingest", {"since_days": since_days}, priority=1)
    log.info("ingest_delegated_to_fetcher", job_id=job_id)
    return _wait_job(job_id, timeout_s, poll_s=5)


def assert_public_http_url(url: str) -> None:
    """SSRF guard: only http/https, no credentials, standard ports, and a public IP after DNS resolution."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise FetchError(f"refusing non-http(s) url: {url[:120]}")
    if parts.username or parts.password:
        raise FetchError("refusing url with embedded credentials")
    if parts.port not in (None, 80, 443, 8080, 8443):
        raise FetchError(f"refusing unusual port {parts.port}")
    host = parts.hostname
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise FetchError(f"dns failure for {host}: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise FetchError(f"refusing non-public address for {host}: {ip}")


def fetch_remote(url: str, timeout_s: float = 90) -> dict[str, Any]:
    """Fetch + sanitize one URL. Returns dict(text,title,lang,published_at,hidden_text_ratio,encoded_blobs,suspicious)."""
    assert_public_http_url(url)
    if role() != "agent":
        return _fetch_local(url)
    from eoa.memory.relational import enqueue_job

    job_id = enqueue_job("fetch_url", {"url": url}, priority=0)
    return _wait_job(job_id, timeout_s)


def _fetch_local(url: str) -> dict[str, Any]:
    from eoa.fetch.html import fetch_page
    from eoa.fetch.sanitize import extract_clean_text

    assert_public_http_url(url)
    page = asyncio.run(fetch_page(url))
    if page.final_url and page.final_url != url:
        assert_public_http_url(page.final_url)  # redirects are re-validated
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
            time.sleep(poll_s)
            continue
        p = job.get("payload") or {}
        try:
            if job["kind"] == "ingest":
                from eoa.fetch.service import run_ingest

                stats = asyncio.run(run_ingest(since_days=int(p.get("since_days", 3))))
                finish_job(
                    job["id"],
                    "done",
                    result={k: v for k, v in vars(stats).items() if isinstance(v, int | float | str | bool)},
                )
            else:
                finish_job(job["id"], "done", result=_fetch_local(str(p.get("url", ""))))
        except Exception as exc:
            log.warning("fetch_job_failed", job_id=job["id"], kind=job["kind"], error=str(exc)[:200])
            finish_job(job["id"], "failed", error=str(exc)[:400])
