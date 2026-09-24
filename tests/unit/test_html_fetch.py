"""Unit tests for eoa.fetch.html — httpx mocked via respx, no real network."""

from __future__ import annotations

import ipaddress

import httpcore
import httpx
import pytest
import respx

from eoa.errors import FetchError
from eoa.fetch.html import FetchedPage, _decode, _PinnedIPTransport, _robots_cache, fetch_page
from eoa.fetch.sanitize import _repair_mojibake

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_robots_cache():
    """The robots.txt parser cache is module-level; reset it so tests don't leak into each other."""
    _robots_cache.clear()
    yield
    _robots_cache.clear()


@respx.mock
async def test_fetch_page_returns_body_status_and_final_url() -> None:
    respx.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example.test/article").mock(
        return_value=httpx.Response(200, html="<html><body><p>hello</p></body></html>")
    )

    page = await fetch_page("https://example.test/article")

    assert isinstance(page, FetchedPage)
    assert page.status == 200
    assert page.url == "https://example.test/article"
    assert page.final_url == "https://example.test/article"
    assert "<p>hello</p>" in page.html


@respx.mock
async def test_fetch_page_honors_robots_disallow() -> None:
    respx.get("https://example.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
    )
    route = respx.get("https://example.test/private/secret")
    route.mock(return_value=httpx.Response(200, html="<html>should never be fetched</html>"))

    with pytest.raises(FetchError, match=r"robots\.txt disallows"):
        await fetch_page("https://example.test/private/secret")

    assert not route.called


@respx.mock
async def test_fetch_page_allows_paths_not_disallowed() -> None:
    respx.get("https://example.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
    )
    respx.get("https://example.test/public/page").mock(
        return_value=httpx.Response(200, html="<html>public content</html>")
    )

    page = await fetch_page("https://example.test/public/page")
    assert "public content" in page.html


@respx.mock
async def test_fetch_page_missing_robots_txt_fails_open() -> None:
    respx.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example.test/anything").mock(return_value=httpx.Response(200, html="<html>ok</html>"))

    page = await fetch_page("https://example.test/anything")
    assert "ok" in page.html


@respx.mock
async def test_fetch_page_enforces_max_bytes() -> None:
    respx.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
    big_body = "<html><body>" + ("A" * 10_000) + "</body></html>"
    respx.get("https://example.test/big").mock(return_value=httpx.Response(200, text=big_body))

    page = await fetch_page("https://example.test/big", max_bytes=100)

    assert len(page.html.encode("utf-8")) <= 100


@respx.mock
async def test_fetch_page_retries_on_5xx_then_succeeds() -> None:
    respx.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get("https://example.test/flaky")
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(503),
        httpx.Response(200, html="<html>recovered</html>"),
    ]

    page = await fetch_page("https://example.test/flaky")

    assert page.status == 200
    assert "recovered" in page.html
    assert route.call_count == 3


@respx.mock
async def test_fetch_page_raises_fetch_error_after_exhausting_retries() -> None:
    respx.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example.test/always-down").mock(return_value=httpx.Response(503))

    with pytest.raises(FetchError):
        await fetch_page("https://example.test/always-down")


# --------------------------------------------------------------------------
# F10 (SOL-AUDIT-2026-09-24): a non-retryable non-2xx status must never be returned as content.
# --------------------------------------------------------------------------


@respx.mock
async def test_fetch_page_rejects_non_retryable_4xx_status() -> None:
    respx.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example.test/gone").mock(
        return_value=httpx.Response(404, html="<html>Not Found</html>")
    )

    with pytest.raises(FetchError, match="non-success status 404"):
        await fetch_page("https://example.test/gone")


@respx.mock
async def test_fetch_page_rejects_403_even_with_a_short_body() -> None:
    respx.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example.test/forbidden").mock(
        return_value=httpx.Response(403, html="<html>Forbidden</html>")
    )

    with pytest.raises(FetchError, match="non-success status 403"):
        await fetch_page("https://example.test/forbidden")


# --------------------------------------------------------------------------
# F37 (SOL-AUDIT-2026-09-24): robots.txt is re-checked for every redirect hop's own host/path,
# not just the starting URL.
# --------------------------------------------------------------------------


@respx.mock
async def test_redirect_destination_robots_disallow_is_honored() -> None:
    respx.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example.test/start").mock(
        return_value=httpx.Response(302, headers={"Location": "https://blocked.test/private/secret"})
    )
    respx.get("https://blocked.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
    )
    route = respx.get("https://blocked.test/private/secret")
    route.mock(return_value=httpx.Response(200, html="<html>should never be fetched</html>"))

    def _validate_redirect(next_url: str) -> set[str] | None:
        return None  # only robots.txt behavior is under test here, no IP pinning

    with pytest.raises(FetchError, match=r"robots\.txt disallows"):
        await fetch_page("https://example.test/start", validate_redirect=_validate_redirect)

    assert not route.called


@respx.mock
async def test_redirect_destination_robots_allow_is_fetched() -> None:
    respx.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example.test/start").mock(
        return_value=httpx.Response(302, headers={"Location": "https://allowed.test/article"})
    )
    respx.get("https://allowed.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://allowed.test/article").mock(
        return_value=httpx.Response(200, html="<html>ok</html>")
    )

    def _validate_redirect(next_url: str) -> set[str] | None:
        return None

    page = await fetch_page("https://example.test/start", validate_redirect=_validate_redirect)
    assert "ok" in page.html


# --------------------------------------------------------------------------
# F01 (SOL-AUDIT-2026-09-24): the robots.txt fetch is pinned to the same validated-IP set as the
# page fetch it gates -- previously it made an entirely unpinned connection.
# --------------------------------------------------------------------------


class _FakeNetworkStream:
    """respx's mock transport never populates the `network_stream` extension `_server_addr`
    reads (see that function's own docstring), so a controllable custom transport is used here
    instead -- no real network I/O, but a real `httpx.AsyncClient`/`fetch_page` round trip."""

    def __init__(self, addr: str) -> None:
        self._addr = addr

    def get_extra_info(self, name: str):
        return self._addr if name == "server_addr" else None


class _PinnedAddrTransport(httpx.AsyncBaseTransport):
    def __init__(self, addr_by_path: dict[str, str]) -> None:
        self._addr_by_path = addr_by_path
        self.requested_paths: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requested_paths.append(path)
        addr = self._addr_by_path.get(path, "0.0.0.0")
        response = httpx.Response(200, content=b"<html>ok</html>", request=request)
        response.extensions["network_stream"] = _FakeNetworkStream(addr)
        return response


async def test_robots_fetch_rejects_a_rebound_connection() -> None:
    """The finding's own test: a resolver/transport that changes the robots.txt connection to a
    different (here: simulated-rebound) address than the one already validated for the article's
    host must be rejected, and the article itself must never be fetched."""
    transport = _PinnedAddrTransport({"/robots.txt": "10.0.0.9", "/article": "93.184.216.34"})
    client = httpx.AsyncClient(transport=transport)
    try:
        with pytest.raises(FetchError, match="DNS rebinding"):
            await fetch_page(
                "https://example.test/article",
                client=client,
                pin_ips={"93.184.216.34"},
            )
    finally:
        await client.aclose()

    assert "/article" not in transport.requested_paths


async def test_robots_fetch_allowed_when_connection_matches_pinned_ips() -> None:
    transport = _PinnedAddrTransport({"/robots.txt": "93.184.216.34", "/article": "93.184.216.34"})
    client = httpx.AsyncClient(transport=transport)
    try:
        page = await fetch_page(
            "https://example.test/article", client=client, pin_ips={"93.184.216.34"}
        )
    finally:
        await client.aclose()

    assert "ok" in page.html
    assert "/article" in transport.requested_paths


class _FakeWireStream(httpcore.AsyncNetworkStream):
    """A minimal fake `httpcore` wire-level stream: serves one canned HTTP/1.1 response and
    remembers which literal address `_PinnedNetworkBackend`/`_FakeNetworkBackend` actually dialed
    to create it -- letting tests drive `_PinnedIPTransport` through `httpcore`'s REAL connection
    pool (the part that partitions/reuses connections, i.e. the part R01 is about) with no real
    network I/O."""

    def __init__(self, response_bytes: bytes, dialed_ip: str) -> None:
        self._buffer = response_bytes
        self._dialed_ip = dialed_ip

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        chunk, self._buffer = self._buffer[:max_bytes], self._buffer[max_bytes:]
        return chunk

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return self  # no real TLS in tests; the transport layer isn't what's under test here

    def get_extra_info(self, info: str):
        return (self._dialed_ip, 443) if info == "server_addr" else None


def _canned_response(body: bytes = b"<html>ok</html>") -> bytes:
    return (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/html\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )


class _FakeNetworkBackend(httpcore.AsyncNetworkBackend):
    """Stands in for the real network backend `_PinnedNetworkBackend` wraps -- records the exact
    `host` argument every `connect_tcp` call actually received (the one `_PinnedNetworkBackend`
    is responsible for replacing with the validated IP when a pin is active), with zero real
    network I/O."""

    def __init__(self, body: bytes = b"<html>ok</html>") -> None:
        self._body = body
        self.dialed_hosts: list[str] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.dialed_hosts.append(host)
        return _FakeWireStream(_canned_response(self._body), dialed_ip=host)

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise NotImplementedError

    async def sleep(self, seconds: float) -> None:
        return None


async def test_pinned_transport_dials_the_validated_ip_not_the_hostname() -> None:
    """F01 (SOL-REVIEW2-2026-09-24) -- the review's own required test, re-targeted one layer
    deeper than round 2's (transport-level) version: prove the connection is actually DIALED at
    the validated IP, not merely checked after connecting, by inspecting what
    `_PinnedNetworkBackend` asks the real `httpcore` NETWORK backend to connect to. A rebound DNS
    answer for `example.test` is never even looked up: no `connect_tcp` call ever names the
    hostname when a pin is active."""
    backend = _FakeNetworkBackend()
    transport = _PinnedIPTransport(_network_backend=backend)
    client = httpx.AsyncClient(transport=transport)
    try:
        page = await fetch_page(
            "https://example.test/article", client=client, pin_ips={"93.184.216.34"}
        )
    finally:
        await client.aclose()

    assert "ok" in page.html
    assert backend.dialed_hosts  # sanity: at least one connection was actually made
    for host in backend.dialed_hosts:
        assert host == "93.184.216.34"
        assert host != "example.test"
        assert not ipaddress.ip_address(host).is_private
    # Callers still see the original hostname/URL, never the dialed IP -- the request URL itself
    # was never rewritten (that's what makes per-hostname pooling below still work).
    assert page.final_url == "https://example.test/article"


async def test_pinned_transport_dials_an_internationalized_host_by_its_pin() -> None:
    """P2 (SOL-REVIEW3-2026-09-24 "IDNA hostname comparison", html.py:292/337): the pin
    `_PinnedIPTransport.handle_async_request` stores must be keyed the SAME way
    `_PinnedNetworkBackend.connect_tcp` will look it up. `httpcore`'s `host` argument there is
    always `httpx.URL.raw_host` (lowercase, IDNA-encoded/punycode ASCII -- see
    `httpx._transports.default`, which builds the `httpcore.Origin` from `raw_host`), never the
    Unicode form `httpx.URL.host` decodes punycode back into for a non-ASCII hostname
    (`httpx.URL("https://xn--bcher-kva.example").host == "bücher.example"`). Storing that Unicode
    form (the pre-fix code) made every real `connect_tcp` call for an internationalized host
    compare its punycode `host` against a Unicode `pin_host` -- an unconditional mismatch that
    made `_PinnedNetworkBackend.connect_tcp` raise `httpcore.ConnectError` ("refusing to dial
    ...") for a perfectly valid, already-validated host. This fetches a Unicode host
    (`bücher.example`, which httpx punycode-encodes to `xn--bcher-kva.example` internally) and
    asserts the fetch actually succeeds and dials the pinned IP -- it would raise (wrapped as
    `FetchError`) against the pre-fix key."""
    backend = _FakeNetworkBackend()
    transport = _PinnedIPTransport(_network_backend=backend)
    client = httpx.AsyncClient(transport=transport)
    try:
        page = await fetch_page(
            "https://bücher.example/artikel", client=client, pin_ips={"192.0.2.10"}
        )
    finally:
        await client.aclose()

    assert "ok" in page.html
    assert backend.dialed_hosts  # sanity: at least one connection was actually made
    for host in backend.dialed_hosts:
        # The validated IP was dialed -- never the punycode host string, and never rejected.
        assert host == "192.0.2.10"
        assert host != "xn--bcher-kva.example"


async def test_pinned_transport_passes_through_when_no_pin_given() -> None:
    """No `"pinned_ip"` extension on the request (e.g. a caller with no `pin_ips`) -- `connect_tcp`
    is asked to connect the real HOSTNAME, exactly as if `_PinnedIPTransport`/`_PinnedNetworkBackend`
    weren't there at all (ordinary DNS resolution downstream)."""
    backend = _FakeNetworkBackend()
    transport = _PinnedIPTransport(_network_backend=backend)
    client = httpx.AsyncClient(transport=transport)
    try:
        page = await fetch_page("https://example.test/article", client=client)
    finally:
        await client.aclose()

    assert "ok" in page.html
    assert backend.dialed_hosts and all(host == "example.test" for host in backend.dialed_hosts)


async def test_two_hosts_on_the_same_ip_never_share_a_connection() -> None:
    """R01 (SOL-REVIEW2-2026-09-24, must-fix): the actual regression this round's fix targets.
    Two different hostnames that happen to resolve/pin to the SAME IP address must never share a
    pooled connection -- a TLS connection verified for hostname A's certificate must never be
    reused to serve a request the caller believes went to hostname B. `httpcore.AsyncConnectionPool`
    partitions strictly by *origin* (`AsyncHTTPConnection.can_handle_request`: `origin ==
    self._origin`, where `origin` comes from the REQUEST URL, never from the dialed address) --
    this test proves that origin is never collapsed to the dialed IP by inspecting the pool's own
    connection objects after both fetches: each one's origin host is the real hostname it was
    opened for, the two hosts got two disjoint sets of connections, and neither origin is ever the
    IP itself. This fails against the round-2 code, which rewrote the request URL's host to the
    IP before handing it to httpcore -- collapsing both hosts onto ONE origin/pool bucket keyed by
    `203.0.113.9`, which is exactly the cross-host connection reuse this test forbids."""
    backend = _FakeNetworkBackend()
    transport = _PinnedIPTransport(_network_backend=backend)
    client = httpx.AsyncClient(transport=transport)
    try:
        page_a = await fetch_page(
            "https://host-a.example/x", client=client, pin_ips={"203.0.113.9"}
        )
        page_b = await fetch_page(
            "https://host-b.example/y", client=client, pin_ips={"203.0.113.9"}
        )
    finally:
        origins = {c._origin.host.decode() for c in transport._pool._connections}  # type: ignore[attr-defined]
        await client.aclose()

    assert "ok" in page_a.html
    assert "ok" in page_b.html
    # Every `connect_tcp` call actually dialed the pinned IP literal (F01) ...
    assert backend.dialed_hosts and all(host == "203.0.113.9" for host in backend.dialed_hosts)
    # ... yet the pool tracked TWO disjoint origins for it, one per real hostname -- never one
    # shared origin keyed by the IP (R01's regression) and never a connection whose origin *is*
    # the dialed IP.
    assert origins == {"host-a.example", "host-b.example"}


async def test_redirect_to_a_host_that_fails_validation_is_never_connected() -> None:
    """R01/F01 (SOL-REVIEW2-2026-09-24): `fetch_page`'s manual-redirect loop calls
    `validate_redirect(next_url)` (in production, `assert_public_http_url`, which RAISES on
    rejection) before requesting a hop -- so a redirect into a host that fails validation must
    raise out of `fetch_page` without ever placing a connection to it."""
    class _RedirectOnceBackend(httpcore.AsyncNetworkBackend):
        """Serves one 302-to-a-blocked-host response for the start URL; a connection to the
        blocked host would be a test failure, so it's simply never wired up as a valid target."""

        def __init__(self) -> None:
            self.dialed_hosts: list[str] = []

        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            self.dialed_hosts.append(host)
            response = (
                b"HTTP/1.1 302 Found\r\n"
                b"Location: https://blocked.example/private\r\n"
                b"Content-Length: 0\r\n\r\n"
            )
            return _FakeWireStream(response, dialed_ip=host)

        async def connect_unix_socket(self, path, timeout=None, socket_options=None):
            raise NotImplementedError

        async def sleep(self, seconds: float) -> None:
            return None

    redirect_backend = _RedirectOnceBackend()
    transport = _PinnedIPTransport(_network_backend=redirect_backend)
    client = httpx.AsyncClient(transport=transport)

    def _validate_redirect(next_url: str) -> set[str] | None:
        if "blocked.example" in next_url:
            raise FetchError(f"refusing non-public address for {next_url}")
        return {"203.0.113.9"}

    try:
        with pytest.raises(FetchError, match="refusing non-public address"):
            await fetch_page(
                "https://start.example/go",
                client=client,
                pin_ips={"203.0.113.9"},
                validate_redirect=_validate_redirect,
            )
    finally:
        await client.aclose()

    # Only the start host's validated IP was ever dialed (once for robots.txt, once for the page
    # itself -- neither call reuses the other's connection, but both target the SAME host) -- the
    # rejected redirect target (`blocked.example`) was never connected to at all.
    assert redirect_backend.dialed_hosts and all(
        host == "203.0.113.9" for host in redirect_backend.dialed_hosts
    )
    assert len(redirect_backend.dialed_hosts) == 2


async def test_robots_fetch_with_no_pin_ips_is_unchanged() -> None:
    """No `pin_ips` given (the un-guarded caller path, e.g. a direct `fetch_page` call outside
    `eoa.fetch.service`) -- behavior is exactly as before this fix: no pin check is attempted."""
    transport = _PinnedAddrTransport({"/robots.txt": "10.0.0.9", "/article": "93.184.216.34"})
    client = httpx.AsyncClient(transport=transport)
    try:
        page = await fetch_page("https://example.test/article", client=client)
    finally:
        await client.aclose()

    assert "ok" in page.html


# --------------------------------------------------------------------------
# _decode: charset priority (HTTP header -> declared <meta>/XML -> charset_normalizer -> utf-8 replace)
# and eoa.fetch.sanitize._repair_mojibake -- regression coverage for the
# "×¢×‘..."/"â€™"-style mojibake reaching the DB.
# --------------------------------------------------------------------------


def _fake_response(content: bytes, content_type: str) -> httpx.Response:
    return httpx.Response(200, content=content, headers={"content-type": content_type})


async def test_decode_uses_meta_charset_when_http_header_has_none() -> None:
    """windows-1255 Hebrew page: no charset in the HTTP header, but `<meta charset>` declares it."""
    hebrew = "שלום עולם"
    html_str = f'<html><head><meta charset="windows-1255"></head><body><p>{hebrew}</p></body></html>'
    content = html_str.encode("windows-1255")
    response = _fake_response(content, "text/html")

    assert response.charset_encoding is None  # nothing usable in the header itself

    decoded = _decode(content, response)
    assert hebrew in decoded


async def test_decode_uses_xml_declaration_when_http_header_has_none() -> None:
    hebrew = "מבחן"
    xml_str = f'<?xml version="1.0" encoding="windows-1255"?><rss><item>{hebrew}</item></rss>'
    content = xml_str.encode("windows-1255")
    response = _fake_response(content, "application/rss+xml")

    decoded = _decode(content, response)
    assert hebrew in decoded


async def test_decode_recovers_utf8_content_mislabelled_as_latin1() -> None:
    """The HTTP header explicitly (and wrongly) claims latin-1 for genuinely UTF-8 Hebrew content.

    latin-1 decodes any byte sequence without ever raising, so trusting the header claim blindly
    reproduces the reported "×¢×‘..."-style mojibake; `_decode` must corroborate a
    permissive single-byte encoding claim against `charset_normalizer` before trusting it.
    """
    original = "מוצר אלקטרו-אופטי חדש"
    html_str = f"<html><body><p>{original}</p></body></html>"
    content = html_str.encode("utf-8")
    response = _fake_response(content, "text/html; charset=latin-1")

    assert response.charset_encoding is not None  # the (wrong) header claim is present

    decoded = _decode(content, response)
    assert original in decoded


async def test_decode_falls_back_to_utf8_replace_for_garbage_bytes() -> None:
    content = b"\xff\xfe\x00\x01not valid in any declared charset here"
    response = _fake_response(content, "text/html; charset=ascii")

    decoded = _decode(content, response)  # must not raise
    assert isinstance(decoded, str)


# --------------------------------------------------------------------------
# eoa.fetch.sanitize._repair_mojibake
# --------------------------------------------------------------------------


async def test_repair_mojibake_fixes_single_mis_decoded_hebrew_text() -> None:
    original = "עברית פרויקט"
    mojibake = original.encode("utf-8").decode("latin-1")

    assert mojibake != original  # sanity: the fixture really is garbled
    assert _repair_mojibake(mojibake) == original


async def test_repair_mojibake_fixes_double_encoded_snippet() -> None:
    """Double-encoding: the once-mojibake string was itself re-encoded as UTF-8 and mis-decoded again."""
    original = "זיהוי מטרות בעברית"
    mojibake_once = original.encode("utf-8").decode("latin-1")
    mojibake_twice = mojibake_once.encode("utf-8").decode("latin-1")

    assert mojibake_twice != original

    assert _repair_mojibake(mojibake_twice) == original


async def test_repair_mojibake_fixes_smart_quote_cp1252_mojibake() -> None:
    """A UTF-8 right single quote mis-decoded as cp1252 ("â€™"-style) -- pure ASCII
    letters either way, so this only self-corrects via the mojibake-marker penalty, not letter counts.
    """
    original = "Company’s new sensor"
    mojibake = original.encode("utf-8").decode("cp1252")

    assert mojibake != original
    assert _repair_mojibake(mojibake) == original


async def test_repair_mojibake_leaves_correct_text_untouched() -> None:
    correct_ascii = "This is normal ASCII text about a radar system."
    correct_hebrew = "טקסט תקין בעברית"

    assert _repair_mojibake(correct_ascii) == correct_ascii
    assert _repair_mojibake(correct_hebrew) == correct_hebrew
    assert _repair_mojibake("") == ""
