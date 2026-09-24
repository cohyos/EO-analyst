"""Async HTML page fetching: timeouts, byte caps, retries, and robots.txt.

Only this module (plus `rss.py`, which fetches feed documents the same way
via `fetch_page`) is allowed to speak HTTP to arbitrary internet hosts —
matching `docs/CONVENTIONS.md` rule 13 (`fetcher`/`egress` network only).
"""

from __future__ import annotations

import codecs
import contextvars
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpcore
import httpx
import structlog
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = structlog.get_logger(__name__)

# Q2-4 (2026-09-06): bounds the manual redirect loop used when a caller passes
# `validate_redirect` -- see `fetch_page`'s docstring.
_MAX_REDIRECT_HOPS = 5

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
    follow_redirects: bool = True,
    extensions: dict[str, object] | None = None,
) -> tuple[httpx.Response, bytes]:
    async with client.stream(
        "GET",
        url,
        timeout=timeout,
        headers=headers,
        follow_redirects=follow_redirects,
        extensions=extensions,
    ) as response:
        if _is_retryable_status(response.status_code):
            raise _RetryableStatusError(response.status_code)
        chunks = bytearray()
        async for chunk in response.aiter_bytes():
            chunks.extend(chunk)
            if len(chunks) >= max_bytes:
                break
        content = bytes(chunks[:max_bytes])
    return response, content


# R01 (SOL-REVIEW2-2026-09-24): the host->IP pin a `_PinnedIPTransport.handle_async_request` call
# has validated for its OWN request, made visible to `_PinnedNetworkBackend.connect_tcp` a few
# frames deeper in the same call stack. A `ContextVar` (not a plain module dict) is required for
# this to be concurrency-safe: `eoa.fetch.service.run_ingest` shares ONE `httpx.AsyncClient` (and
# therefore one transport/pool/backend instance) across many sources fetched concurrently via
# `asyncio.gather`. Each `gather`-scheduled coroutine runs as its own `asyncio.Task`, and asyncio
# gives every `Task` an independent COPY of the context at creation time, so one task's `.set()`
# here is invisible to a sibling task dialing a different host at the same moment -- while still
# being visible across `await` points *within* the one task/request that set it. A plain dict
# keyed by host would also mostly work, but couldn't distinguish two concurrent pins for the SAME
# host with different (rare, but possible mid-run re-validation) IPs, and would need its own
# locking; the contextvar needs neither.
_pinned_host_ip: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "eoa_fetch_pinned_host_ip", default=None
)


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """R01 (SOL-REVIEW2-2026-09-24): the actual DNS-rebinding fix. `_PinnedIPTransport` below no
    longer rewrites the request URL's host to the pinned IP -- the round-2 review found that doing
    so makes `httpcore` pool connections by the DIALED IP (since the request's origin becomes the
    IP once the host is rewritten), so two different hostnames that happen to resolve to the same
    IP address could share one pooled, already-TLS-verified connection: a connection whose
    certificate was verified for hostname A would silently be reused to serve a request the caller
    believes went to hostname B.

    Instead, the request URL (and therefore the `httpcore.Origin` `AsyncConnectionPool` uses to
    key its connection pool, and the hostname `AsyncHTTPConnection._connect` uses as the default
    TLS `server_hostname`) is left completely alone -- pooling and certificate verification happen
    against the REAL hostname, exactly as for any unpinned request. This backend intercepts only
    the one step that used to leak DNS resolution to a second, unpinned lookup: the actual TCP
    dial. When `_pinned_host_ip` has an active pin scoped to the host `httpcore` is about to
    connect (`connect_tcp`'s `host` argument, which for a fresh connection is always that
    connection's origin host -- see `AsyncHTTPConnection._connect`), the literal validated IP is
    dialed directly, with no DNS lookup at all: there is nothing left for a rebinding second
    answer to redirect. A `host` that doesn't match the active pin is refused outright, closing
    the residual case where some other codepath left a stale/wrong pin active. No pin active at
    all (unpinned callers, or callers outside `eoa.fetch.service`'s SSRF-guarded path) falls
    through to the wrapped backend unchanged -- ordinary DNS resolution, exactly as before this
    class existed.
    """

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: object = None,
    ) -> httpcore.AsyncNetworkStream:
        pin = _pinned_host_ip.get()
        if pin is None:
            return await self._inner.connect_tcp(
                host, port, timeout=timeout, local_address=local_address, socket_options=socket_options
            )
        pin_host, pin_ip = pin
        if host != pin_host:
            raise httpcore.ConnectError(
                f"refusing to dial {host}: the active validated-IP pin is scoped to {pin_host}"
            )
        return await self._inner.connect_tcp(
            pin_ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: object = None
    ) -> httpcore.AsyncNetworkStream:
        return await self._inner.connect_unix_socket(path, timeout=timeout, socket_options=socket_options)

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _PinnedIPTransport(httpx.AsyncHTTPTransport):
    """F01/R01 (SOL-REVIEW2-2026-09-24): a plain `httpx.AsyncHTTPTransport` -- identical request/
    response handling, pool construction, and constructor kwargs (`verify`, `http2`, `limits`,
    `proxy`, ...) -- except its underlying `httpcore.AsyncConnectionPool`'s network backend is
    wrapped in `_PinnedNetworkBackend`, and a request carrying a `"pinned_ip"` extension (set by
    `fetch_page`/`_get_robot_parser` below, from the caller-supplied `pin_ips` set) activates that
    pin -- scoped to this one request's host -- for the duration of the call. See
    `_PinnedNetworkBackend`'s docstring for why this, and not URL rewriting, is what actually
    prevents cross-hostname TLS/connection reuse (R01) while still dialing the validated IP
    without a second DNS lookup (F01).
    """

    def __init__(
        self, *args: object, _network_backend: httpcore.AsyncNetworkBackend | None = None, **kwargs: object
    ) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        # `AsyncConnectionPool._network_backend` is read lazily, once per new connection -- see
        # `httpcore/_async/connection_pool.py`'s `_connect_with_semaphore`/`AsyncHTTPConnection`
        # construction sites -- so replacing it here, before any request has been dispatched, is
        # enough to cover every connection this pool ever opens. `_network_backend` (test-only) lets
        # a fake backend stand in for `_PinnedNetworkBackend`'s own inner backend, so a test can
        # count/inspect real `connect_tcp` calls without any real network I/O.
        self._pool._network_backend = _PinnedNetworkBackend(_network_backend or self._pool._network_backend)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        pinned_ip = request.extensions.get("pinned_ip")
        if not pinned_ip:
            return await super().handle_async_request(request)
        # P2 (SOL-REVIEW3-2026-09-24 "IDNA hostname comparison"): the pin MUST be keyed by the
        # same string `_PinnedNetworkBackend.connect_tcp` below will compare it against. That
        # `host` argument comes from `httpcore.Origin.host` (see
        # `httpx._transports.default`, built from `request.url.raw_host`), decoded back to ASCII
        # -- i.e. always the lowercase, IDNA-encoded (punycode) form. `httpx.URL.host` is a
        # DIFFERENT, human-facing property: for an internationalized host it decodes punycode back
        # to Unicode (`httpx.URL("http://xn--bcher-kva.example").host == "bücher.example"`), so
        # storing that here would make every real `connect_tcp` call for such a host compare
        # "xn--bcher-kva.example" (from Origin) against "bücher.example" (this pin) and always
        # mismatch -- `_PinnedNetworkBackend.connect_tcp` would then refuse to dial a perfectly
        # valid internationalized host. `raw_host` is exactly `Origin.host`'s source (both derive
        # from the same internal, already-IDNA-encoded `_uri_reference.host`), so this always
        # agrees with `connect_tcp`'s `host` -- for plain ASCII hosts the two properties already
        # coincide (both merely lowercase), so this changes nothing for the common case. Security
        # semantics are unchanged: the dial below still resolves to `pinned_ip` only, and TLS
        # verification still runs against `request.url` (untouched), not this pin key.
        token = _pinned_host_ip.set((request.url.raw_host.decode("ascii"), pinned_ip))
        try:
            return await super().handle_async_request(request)
        finally:
            _pinned_host_ip.reset(token)


def _server_addr(response: httpx.Response) -> str | None:
    """Best-effort: the IP address `httpx` actually connected to for `response`.

    Belt-and-suspenders alongside `_PinnedIPTransport` (F01): that transport is what actually
    prevents an unpinned connection from being dialed in the first place; this is a second,
    independent check that also catches the case where the installed transport ISN'T
    `_PinnedIPTransport` (a caller-supplied `client` built without it, or a test transport) by
    verifying, after the fact, that whatever address was actually reached matches the validated
    set -- and rejecting the response if not.

    Returns ``None`` when the installed transport doesn't provide this extra info (e.g. `respx`'s
    mock transport in tests, or older `httpx` versions) -- callers must treat that as "cannot
    verify", not "verified", and the caller here (`fetch_page`) does exactly that: it logs and
    continues rather than failing closed, since failing closed would break every fetch on any
    transport that doesn't expose the extension.
    """
    network_stream = response.extensions.get("network_stream")
    if network_stream is None:
        return None
    try:
        info = network_stream.get_extra_info("server_addr")
    except Exception:
        return None
    if not info:
        return None
    return str(info[0]) if isinstance(info, tuple | list) else str(info)


def build_pinned_client(**kwargs: object) -> httpx.AsyncClient:
    """F01/E04: construct an `httpx.AsyncClient` whose connections are pinned per-request via
    `_PinnedIPTransport` -- used both by `fetch_page`'s own default (no `client` given) and by
    `eoa.fetch.service.run_ingest`'s single shared client for a whole ingest run, so pinning
    applies whether or not a caller reuses one client across many hosts/fetches."""
    return httpx.AsyncClient(transport=_PinnedIPTransport(), **kwargs)


def _choose_pin_ip(pin_ips: set[str] | None) -> str | None:
    """Deterministically pick one address out of a validated `pin_ips` set to actually dial --
    `assert_public_http_url` can return more than one (multiple A/AAAA records); `sorted()[0]` is
    stable across calls for the same set, which keeps repeated fetches of one host on one address
    for the robots-cache TTL / retry window instead of bouncing between candidates."""
    return sorted(pin_ips)[0] if pin_ips else None


async def _get_robot_parser(
    url: str,
    client: httpx.AsyncClient,
    user_agent: str,
    *,
    pin_ips: set[str] | None = None,
) -> RobotFileParser:
    """F01 (SOL-AUDIT-2026-09-24, round 2): the robots.txt fetch is on the same host as the page
    fetch it gates, so it is DIALED (not just post-hoc checked) to the same caller-validated
    ``pin_ips`` set as `fetch_page`'s own page request -- see `_PinnedIPTransport`. A mismatch on
    the post-hoc `_server_addr` check (still run as a second, transport-independent guard) is a
    suspected DNS-rebinding hop and fails closed (raises), unlike the "no robots.txt" / transport-
    error cases below, which intentionally fail open (allow-all) -- those are availability
    failures, not a security signal. A cache hit returns the already-parsed result without any new
    connection, so it never needs (or bypasses) this check."""
    parts = urlsplit(url)
    root = f"{parts.scheme}://{parts.netloc}"
    now = time.monotonic()

    cached = _robots_cache.get(root)
    if cached is not None and (now - cached[1]) < _ROBOTS_CACHE_TTL_SECONDS:
        return cached[0]

    parser = RobotFileParser()
    robots_url = urljoin(root, "/robots.txt")
    pinned_ip = _choose_pin_ip(pin_ips)
    try:
        response = await client.get(
            robots_url,
            timeout=10.0,
            headers={"User-Agent": user_agent},
            extensions={"pinned_ip": pinned_ip} if pinned_ip else None,
        )
        if pin_ips:
            server_addr = _server_addr(response)
            if server_addr is not None and server_addr not in pin_ips:
                from eoa.errors import FetchError

                raise FetchError(
                    f"connected address {server_addr} for {robots_url} does not match the "
                    f"validated address set {sorted(pin_ips)} -- possible DNS rebinding"
                )
            if server_addr is None:
                log.debug("fetch.pin_ip_unverifiable", url=robots_url)
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
    validate_redirect: Callable[[str], set[str] | None] | None = None,
    pin_ips: set[str] | None = None,
) -> FetchedPage:
    """Fetch one URL, honoring robots.txt and the configured byte cap, with 3x retry on 5xx/429.

    `max_bytes` overrides `config.yaml`'s `fetch.max_bytes` for this call only
    (used by tests and by callers that already know a page is huge).

    `validate_redirect`/`pin_ips` (Q2-4, additive, default `None` -- existing
    callers keep httpx's normal automatic-redirect behavior unchanged): when
    `validate_redirect` is given, this function disables httpx's own redirect
    following and instead walks the chain itself (bounded to
    `_MAX_REDIRECT_HOPS` hops), calling `validate_redirect(next_url)` on every
    `Location` header *before* requesting it -- typically
    `eoa.fetch.remote.assert_public_http_url`, so a redirect into a private/
    loopback/link-local address is rejected before a single byte is fetched
    from it, closing the TOCTOU gap where the old code only re-validated the
    *final* URL after the whole chain had already been followed. The
    callable's return value (the freshly-validated IP set for that hop's
    host) replaces `pin_ips` for the next hop's connection check. `pin_ips`
    seeds that check for the initial URL -- pass the set already returned by
    validating `url` itself, to avoid re-resolving it a second time.

    F01 (round 2): when this function opens its own client (no `client` given), that client is
    built by `build_pinned_client()` -- every hop's connection is DIALED at the validated IP
    (`_PinnedIPTransport`), not merely checked after connecting. A caller-supplied `client` (e.g.
    `eoa.fetch.service.run_ingest`'s one shared client for a whole run, E04) must itself be built
    with `build_pinned_client()` for the same guarantee to hold for that call; the post-hoc
    `_server_addr` check below still runs regardless, as a second guard.
    """
    from eoa.errors import FetchError

    timeout, cfg_max_bytes, user_agent, respect_robots = _fetch_settings()
    effective_max_bytes = max_bytes if max_bytes is not None else cfg_max_bytes
    headers = {"User-Agent": user_agent}
    manual_redirects = validate_redirect is not None

    owns_client = client is None
    active_client = client or build_pinned_client()
    try:
        current_url = url
        current_pin_ips = pin_ips
        response: httpx.Response | None = None
        content: bytes = b""
        for _hop in range(_MAX_REDIRECT_HOPS + 1):
            # F37: robots permission is re-checked for EVERY hop's host/path, not just the
            # starting URL -- on the first iteration `current_url == url`; on a manual-redirect
            # hop it is the freshly-validated redirect target, whose robots.txt may differ from
            # the origin's (and is cached separately, keyed by root). With
            # `manual_redirects=False` (no `validate_redirect` given -- the normal-callers path,
            # unchanged), httpx already followed every redirect internally by the time a response
            # comes back, so this loop runs exactly once anyway and behavior is identical to
            # before.
            if respect_robots:
                parser = await _get_robot_parser(
                    current_url, active_client, user_agent, pin_ips=current_pin_ips
                )
                if not parser.can_fetch(user_agent, current_url):
                    raise FetchError(f"robots.txt disallows fetching {current_url}")
            hop_pinned_ip = _choose_pin_ip(current_pin_ips)
            try:
                response, content = await _get_with_retry(
                    active_client,
                    current_url,
                    timeout=timeout,
                    max_bytes=effective_max_bytes,
                    headers=headers,
                    follow_redirects=not manual_redirects,
                    extensions={"pinned_ip": hop_pinned_ip} if hop_pinned_ip else None,
                )
            except _RetryableStatusError as exc:
                raise FetchError(
                    f"upstream returned {exc.status_code} for {current_url} after retries"
                ) from exc
            except httpx.HTTPError as exc:
                raise FetchError(f"transport error fetching {current_url}: {exc}") from exc

            if current_pin_ips:
                server_addr = _server_addr(response)
                if server_addr is not None and server_addr not in current_pin_ips:
                    raise FetchError(
                        f"connected address {server_addr} for {current_url} does not match the "
                        f"validated address set {sorted(current_pin_ips)} -- possible DNS rebinding"
                    )
                if server_addr is None:
                    log.debug("fetch.pin_ip_unverifiable", url=current_url)

            if not manual_redirects or not response.is_redirect:
                break
            location = response.headers.get("location")
            if not location:
                break
            next_url = urljoin(current_url, location)
            validated = validate_redirect(next_url) if validate_redirect is not None else None
            if validated:
                current_pin_ips = validated
            current_url = next_url
        else:
            raise FetchError(f"too many redirects (> {_MAX_REDIRECT_HOPS}) fetching {url}")

        assert response is not None  # loop always runs >= 1 iteration
        # F10: a non-retryable non-2xx response (404/401/451/an undelivered redirect with no
        # `Location`, ...) is never real article/listing content -- 5xx/429 are already turned
        # into a `FetchError` by the retry wrapper above; everything else used to fall through to
        # `_decode` and be returned as an ordinary `FetchedPage`, which `service.py` would then
        # parse or store as if it were the genuine page. Rejecting here, before extraction, means
        # neither an item row nor a "source fetched OK" result is ever produced from one.
        if not (200 <= response.status_code < 300):
            raise FetchError(
                f"non-success status {response.status_code} for {current_url} "
                f"(final url {response.url})"
            )
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
