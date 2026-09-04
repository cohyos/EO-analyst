"""Async HTML page fetching: timeouts, byte caps, retries, and robots.txt.

Only this module (plus `rss.py`, which fetches feed documents the same way
via `fetch_page`) is allowed to speak HTTP to arbitrary internet hosts —
matching `docs/CONVENTIONS.md` rule 13 (`fetcher`/`egress` network only).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
import structlog
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 20.0
_DEFAULT_MAX_BYTES = 2_000_000
_DEFAULT_USER_AGENT = "eo-analyst/0.1 (+local OSINT research; contact: none)"
_DEFAULT_RESPECT_ROBOTS = True

_ROBOTS_CACHE_TTL_SECONDS = 3600.0
_robots_cache: dict[str, tuple[RobotFileParser, float]] = {}


class FetchedPage(BaseModel):
    """Result of one successful (or robots-blocked-turned-error) page fetch."""

    url: str
    final_url: str
    status: int
    html: str
    fetched_at: datetime


class _RetryableStatusError(Exception):
    """Internal signal for tenacity: a 5xx/429 response should be retried."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"retryable upstream status {status_code}")


def _fetch_settings() -> tuple[float, int, str, bool]:
    """Read fetch.* from `eoa.config.settings()`, falling back to config.yaml's own defaults.

    Imported lazily (and defended with try/except) so this module works
    before/without the concurrently-developed config layer, and so unit
    tests never need a populated `config/*.yaml` on disk.
    """
    try:
        from eoa.config import settings

        cfg = settings().fetch
        return (
            float(cfg.timeout_seconds),
            int(cfg.max_bytes),
            cfg.user_agent,
            bool(cfg.respect_robots),
        )
    except Exception:
        return (
            _DEFAULT_TIMEOUT_SECONDS,
            _DEFAULT_MAX_BYTES,
            _DEFAULT_USER_AGENT,
            _DEFAULT_RESPECT_ROBOTS,
        )


def _is_retryable_status(status_code: int) -> bool:
    return status_code >= 500 or status_code == 429


def _decode(content: bytes, response: httpx.Response) -> str:
    encoding = response.charset_encoding or "utf-8"
    try:
        return content.decode(encoding, errors="replace")
    except (LookupError, TypeError):
        return content.decode("utf-8", errors="replace")


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    retry=retry_if_exception_type((httpx.TransportError, _RetryableStatusError)),
)
async def _get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    headers: dict[str, str],
) -> tuple[httpx.Response, bytes]:
    async with client.stream("GET", url, timeout=timeout, headers=headers, follow_redirects=True) as response:
        if _is_retryable_status(response.status_code):
            raise _RetryableStatusError(response.status_code)
        chunks = bytearray()
        async for chunk in response.aiter_bytes():
            chunks.extend(chunk)
            if len(chunks) >= max_bytes:
                break
        content = bytes(chunks[:max_bytes])
    return response, content


async def _get_robot_parser(url: str, client: httpx.AsyncClient, user_agent: str) -> RobotFileParser:
    parts = urlsplit(url)
    root = f"{parts.scheme}://{parts.netloc}"
    now = time.monotonic()

    cached = _robots_cache.get(root)
    if cached is not None and (now - cached[1]) < _ROBOTS_CACHE_TTL_SECONDS:
        return cached[0]

    parser = RobotFileParser()
    robots_url = urljoin(root, "/robots.txt")
    try:
        response = await client.get(robots_url, timeout=10.0, headers={"User-Agent": user_agent})
        if response.status_code >= 400:
            parser.parse([])  # no robots.txt (or blocked) -> fail open, allow all
        else:
            parser.parse(response.text.splitlines())
    except httpx.HTTPError as exc:
        log.debug("fetch.robots_fetch_failed", root=root, error=repr(exc))
        parser.parse([])  # fail open on transport errors fetching robots.txt itself

    _robots_cache[root] = (parser, now)
    return parser


async def fetch_page(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    max_bytes: int | None = None,
) -> FetchedPage:
    """Fetch one URL, honoring robots.txt and the configured byte cap, with 3x retry on 5xx/429.

    `max_bytes` overrides `config.yaml`'s `fetch.max_bytes` for this call only
    (used by tests and by callers that already know a page is huge).
    """
    from eoa.errors import FetchError

    timeout, cfg_max_bytes, user_agent, respect_robots = _fetch_settings()
    effective_max_bytes = max_bytes if max_bytes is not None else cfg_max_bytes
    headers = {"User-Agent": user_agent}

    owns_client = client is None
    active_client = client or httpx.AsyncClient()
    try:
        if respect_robots:
            parser = await _get_robot_parser(url, active_client, user_agent)
            if not parser.can_fetch(user_agent, url):
                raise FetchError(f"robots.txt disallows fetching {url}")

        try:
            response, content = await _get_with_retry(
                active_client, url, timeout=timeout, max_bytes=effective_max_bytes, headers=headers
            )
        except _RetryableStatusError as exc:
            raise FetchError(f"upstream returned {exc.status_code} for {url} after retries") from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"transport error fetching {url}: {exc}") from exc

        html_text = _decode(content, response)
        return FetchedPage(
            url=url,
            final_url=str(response.url),
            status=response.status_code,
            html=html_text,
            fetched_at=datetime.now(UTC),
        )
    finally:
        if owns_client:
            await active_client.aclose()
