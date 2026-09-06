"""Unit tests for eoa.fetch.html — httpx mocked via respx, no real network."""

from __future__ import annotations

import httpx
import pytest
import respx

from eoa.errors import FetchError
from eoa.fetch.html import FetchedPage, _decode, _robots_cache, fetch_page
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
