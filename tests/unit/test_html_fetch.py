"""Unit tests for eoa.fetch.html — httpx mocked via respx, no real network."""

from __future__ import annotations

import httpx
import pytest
import respx

from eoa.errors import FetchError
from eoa.fetch.html import FetchedPage, _robots_cache, fetch_page

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
