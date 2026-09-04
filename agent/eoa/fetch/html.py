"""Async HTML page fetching: timeouts, byte caps, retries, and robots.txt.

Only this module (plus `rss.py`, which fetches feed documents the same way
via `fetch_page`) is allowed to speak HTTP to arbitrary internet hosts —
matching `docs/CONVENTIONS.md` rule 13 (`fetcher`/`egress` network only).
"""

from __future__ import annotations

import codecs
import re
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


# Only the first few KB are ever scanned: a charset declaration only counts
# (per browsers and the HTML5 spec) if it appears near the top of the
# document, and limiting the scan keeps this cheap on multi-MB pages.
_SNIFF_WINDOW_BYTES = 4096

_META_CHARSET_RE = re.compile(rb'<meta[^>]+charset\s*=\s*["\']?\s*([a-zA-Z0-9_\-:.]+)', re.IGNORECASE)
_META_HTTP_EQUIV_RE = re.compile(
    rb'<meta[^>]+http-equiv\s*=\s*["\']?content-type["\']?[^>]*content\s*=\s*'
    rb'["\'][^"\'>]*charset\s*=\s*([a-zA-Z0-9_\-:.]+)',
    re.IGNORECASE,
)
_XML_DECL_RE = re.compile(rb'<\?xml[^>]+encoding\s*=\s*["\']([a-zA-Z0-9_\-:.]+)', re.IGNORECASE)

# Single-byte encodings that decode *any* byte sequence without ever raising
# -- a successful decode with one of these is not, by itself, evidence the
# encoding is correct (unlike e.g. UTF-8 or windows-1255, which reject
# invalid byte sequences). A bare claim of one of these -- from the HTTP
# header or an in-page declaration -- is corroborated against
# `charset_normalizer` before being trusted (see `_decode`).
_PERMISSIVE_ENCODING_NAMES = {"iso8859-1", "cp1252"}


def _sniff_declared_encoding(content: bytes) -> str | None:
    """Find an encoding the document declares about itself: `<meta charset>`, the older
    `<meta http-equiv="Content-Type" content="...charset=...">` form, or an XML declaration.

    This is what `httpx.Response.charset_encoding` does *not* do -- it only ever looks at the HTTP
    `Content-Type` header -- so a page whose header is absent/wrong but whose HTML correctly declares
    its own charset (a common shape for legacy CMSs serving windows-1255 Hebrew content) was
    previously decoded with the wrong (default UTF-8) codec.
    """
    head = content[:_SNIFF_WINDOW_BYTES]
    for pattern in (_META_CHARSET_RE, _META_HTTP_EQUIV_RE, _XML_DECL_RE):
        match = pattern.search(head)
        if match:
            try:
                return match.group(1).decode("ascii").strip()
            except UnicodeDecodeError:
                continue
    return None


def _canonical_encoding_name(encoding: str) -> str | None:
    try:
        return codecs.lookup(encoding).name
    except LookupError:
        return None


def _try_decode(content: bytes, encoding: str) -> str | None:
    try:
        return content.decode(encoding)
    except (LookupError, UnicodeDecodeError, TypeError):
        return None


def _detect_with_charset_normalizer(content: bytes) -> str | None:
    """Best-effort statistical charset guess, used both as a fallback decoder and to corroborate a
    permissive single-byte encoding claim (see `_PERMISSIVE_ENCODING_NAMES`)."""
    try:
        from charset_normalizer import from_bytes
    except ImportError:  # pragma: no cover - charset_normalizer is a hard dependency in pyproject.toml
        return None
    try:
        best = from_bytes(bytes(content)).best()
    except Exception as exc:
        log.debug("fetch.charset_normalizer_failed", error=repr(exc))
        return None
    return str(best) if best is not None else None


def _decode(content: bytes, response: httpx.Response) -> str:
    """Decode fetched bytes to text.

    Priority: the HTTP `Content-Type` charset -> an in-document declaration (`<meta charset>` /
    `http-equiv` / XML declaration) -> `charset_normalizer`'s statistical guess -> UTF-8 with lossy
    `errors="replace"` as a last resort.

    A claimed *permissive* single-byte encoding (latin-1/cp1252 -- see
    `_PERMISSIVE_ENCODING_NAMES`) never raises regardless of the actual bytes, so on its own it is
    not proof of correctness: real UTF-8 content mislabelled that way (the direct cause of
    "×¢×‘×¨×™×ª"/"â€™"-style mojibake reaching the DB) would otherwise be decoded "successfully" into
    garbage. Such a claim is corroborated against `charset_normalizer` before being trusted; any
    other encoding (UTF-8, windows-1255, ...) rejects invalid byte sequences on its own, so a
    successful decode there is trusted directly.
    """
    candidates = [response.charset_encoding, _sniff_declared_encoding(content)]

    for encoding in candidates:
        if not encoding:
            continue
        decoded = _try_decode(content, encoding)
        if decoded is None:
            continue
        canonical = _canonical_encoding_name(encoding)
        if canonical in _PERMISSIVE_ENCODING_NAMES:
            detected = _detect_with_charset_normalizer(content)
            if detected and _canonical_encoding_name(detected) != canonical:
                continue  # unconfirmed permissive-encoding claim -- keep looking
        return decoded

    detected = _detect_with_charset_normalizer(content)
    if detected:
        decoded = _try_decode(content, detected)
        if decoded is not None:
            return decoded

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
