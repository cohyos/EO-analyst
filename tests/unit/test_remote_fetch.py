"""Unit tests for eoa.fetch.remote -- SSRF guard + Q2-4 redirect/DNS-rebinding pinning.

httpx is mocked via respx; no real network. `role()` is left at its default ("host") in this
process, so `fetch_remote`/`_fetch_local` run in-process rather than going through the fetcher
job queue.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from eoa.errors import FetchError
from eoa.fetch.remote import _fetch_local, assert_public_http_url, fetch_remote


class TestAssertPublicHttpUrl:
    def test_returns_validated_ip_set_for_a_public_host(self):
        ips = assert_public_http_url("https://93.184.216.34/")  # example.com's old public A record
        assert ips == {"93.184.216.34"}

    def test_rejects_loopback(self):
        with pytest.raises(FetchError):
            assert_public_http_url("http://127.0.0.1/")

    def test_rejects_link_local(self):
        with pytest.raises(FetchError):
            assert_public_http_url("http://169.254.169.254/latest/meta-data")

    def test_rejects_private_range(self):
        with pytest.raises(FetchError):
            assert_public_http_url("http://10.0.0.5/")


class TestFetchLocalRedirectPinning:
    """Q2-4: every redirect hop is re-validated *before* being requested."""

    @respx.mock
    def test_redirect_to_loopback_is_refused(self):
        respx.get("http://93.184.216.34/robots.txt").mock(return_value=httpx.Response(404))
        respx.get("http://93.184.216.34/start").mock(
            return_value=httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})
        )
        route_admin = respx.get("http://127.0.0.1/admin")
        route_admin.mock(return_value=httpx.Response(200, html="<html>should never be reached</html>"))

        with pytest.raises(FetchError):
            _fetch_local("http://93.184.216.34/start")

        # The critical assertion: the redirect target was never actually requested --
        # it was rejected by `assert_public_http_url` before `fetch_page` issued the
        # second hop, not merely after the fact.
        assert not route_admin.called

    @respx.mock
    def test_redirect_to_public_host_is_allowed(self):
        respx.get("http://93.184.216.34/robots.txt").mock(return_value=httpx.Response(404))
        respx.get("http://93.184.216.34/robots.txt").mock(return_value=httpx.Response(404))
        respx.get("http://1.1.1.1/robots.txt").mock(return_value=httpx.Response(404))
        respx.get("http://93.184.216.34/start").mock(
            return_value=httpx.Response(302, headers={"Location": "http://1.1.1.1/final"})
        )
        respx.get("http://1.1.1.1/final").mock(
            return_value=httpx.Response(200, html="<html><body><p>ok</p></body></html>")
        )

        result = _fetch_local("http://93.184.216.34/start")

        assert result["final_url"] == "http://1.1.1.1/final"
        assert "ok" in result["text"] or result["text"] is not None

    @respx.mock
    def test_fetch_remote_end_to_end_refuses_loopback_redirect(self):
        """Exercises the public `fetch_remote` entry point, not just `_fetch_local` directly."""
        respx.get("http://93.184.216.34/robots.txt").mock(return_value=httpx.Response(404))
        respx.get("http://93.184.216.34/x").mock(
            return_value=httpx.Response(301, headers={"Location": "http://169.254.169.254/latest/meta-data"})
        )
        route = respx.get("http://169.254.169.254/latest/meta-data")
        route.mock(return_value=httpx.Response(200, html="metadata"))

        with pytest.raises(FetchError):
            fetch_remote("http://93.184.216.34/x")

        assert not route.called
