"""Unit tests for eoa.fetch.html — httpx mocked via respx, no real network."""

from __future__ import annotations

import ipaddress

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


class _RecordingInnerTransport(httpx.AsyncBaseTransport):
    """Stands in for the real `httpx.AsyncHTTPTransport` inside `_PinnedIPTransport` -- records
    the exact host `_PinnedIPTransport` actually asked it to connect to (it never itself
    re-resolves a hostname), so a test can prove the connection target."""

    def __init__(self) -> None:
        self.dialed_hosts: list[str] = []
        self.sni_hostnames: list[str | None] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.dialed_hosts.append(request.url.host)
        self.sni_hostnames.append(request.extensions.get("sni_hostname"))
        return httpx.Response(200, content=b"<html>ok</html>", request=request)


async def test_pinned_transport_dials_the_validated_ip_not_the_hostname() -> None:
    """F01 (SOL-REVIEW-2026-09-24 round 2): the review's own required test -- prove the connection
    is actually DIALED at the validated IP, not merely checked after connecting. A rebound DNS
    answer for `example.test` (imagined here as some private address the round-1 code would have
    connected to and only rejected afterward) is never even looked up: `_PinnedIPTransport`
    replaces the request URL's host with the already-validated IP literal before the inner
    transport -- which would be the one making the real TCP/TLS connection -- ever sees the
    request, for both the robots.txt fetch and the article fetch. This fails against the old
    (round-1) code, where the inner transport would have been asked to connect to the hostname
    `example.test` itself (the private-IP rebinding gap the review's F01 evidence quotes)."""
    inner = _RecordingInnerTransport()
    transport = _PinnedIPTransport(inner)
    client = httpx.AsyncClient(transport=transport)
    try:
        page = await fetch_page(
            "https://example.test/article", client=client, pin_ips={"93.184.216.34"}
        )
    finally:
        await client.aclose()

    assert "ok" in page.html
    # Every connection actually attempted (robots.txt + the article) dialed the pinned IP literal
    # -- never the hostname (where a rebound DNS answer would otherwise be resolved) and never any
    # private/internal address.
    assert inner.dialed_hosts  # sanity: at least one connection was actually made
    for host in inner.dialed_hosts:
        assert host == "93.184.216.34"
        assert host != "example.test"
        assert not ipaddress.ip_address(host).is_private
    # TLS SNI/certificate verification still uses the real hostname, not the dialed IP.
    assert inner.sni_hostnames and all(sni == "example.test" for sni in inner.sni_hostnames)
    # Callers still see the original hostname/URL, never the dialed IP.
    assert page.final_url == "https://example.test/article"


async def test_pinned_transport_passes_through_when_no_pin_given() -> None:
    """No `"pinned_ip"` extension on the request (e.g. a caller with no `pin_ips`) -- the request
    reaches the inner transport completely unchanged, exactly as if `_PinnedIPTransport` weren't
    there at all."""
    inner = _RecordingInnerTransport()
    transport = _PinnedIPTransport(inner)
    client = httpx.AsyncClient(transport=transport)
    try:
        page = await fetch_page("https://example.test/article", client=client)
    finally:
        await client.aclose()

    assert "ok" in page.html
    assert all(host == "example.test" for host in inner.dialed_hosts)


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
