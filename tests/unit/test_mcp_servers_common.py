"""Tests for eoa.mcp_servers._common -- Q2-14 (per-hop SSRF-safe manual redirect loop) and Q2-15
(secret redaction on every error path). httpx is mocked via respx; no real network.

Hosts are literal public/loopback IPs (matching `tests/unit/test_remote_fetch.py`'s pattern), not
hostnames -- `assert_public_http_url` always resolves its host via `socket.getaddrinfo`, which for
a literal IP address is answered locally without any real DNS query, but for a fictitious hostname
would depend on the test machine's network/resolver and be flaky.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from eoa.errors import FetchError
from eoa.mcp_servers._common import http_get_json


class TestRedirectGuard:
    @respx.mock
    def test_redirect_to_loopback_is_refused_before_being_requested(self):
        respx.get("http://93.184.216.34/search").mock(
            return_value=httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})
        )
        loopback_route = respx.get("http://127.0.0.1/admin").mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(FetchError):
            http_get_json("http://93.184.216.34/search")

        # The critical assertion: the redirect target was never actually requested.
        assert not loopback_route.called

    @respx.mock
    def test_cross_host_redirect_is_refused(self):
        respx.get("http://93.184.216.34/search").mock(
            return_value=httpx.Response(302, headers={"Location": "http://1.1.1.1/steal"})
        )
        evil_route = respx.get("http://1.1.1.1/steal").mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(FetchError, match="cross-host"):
            http_get_json("http://93.184.216.34/search")

        assert not evil_route.called

    @respx.mock
    def test_same_host_redirect_is_followed(self):
        respx.get("http://93.184.216.34/search").mock(
            return_value=httpx.Response(302, headers={"Location": "http://93.184.216.34/search/v2"})
        )
        respx.get("http://93.184.216.34/search/v2").mock(return_value=httpx.Response(200, json={"ok": True}))

        out = http_get_json("http://93.184.216.34/search")
        assert out == {"status": 200, "json": {"ok": True}, "text": None}

    @respx.mock
    def test_too_many_redirects_is_refused(self):
        for i in range(5):
            respx.get(f"http://93.184.216.34/hop{i}").mock(
                return_value=httpx.Response(302, headers={"Location": f"http://93.184.216.34/hop{i + 1}"})
            )
        respx.get("http://93.184.216.34/hop5").mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(FetchError, match="too many redirects"):
            http_get_json("http://93.184.216.34/hop0")


class TestErrorRedaction:
    @respx.mock
    def test_transport_error_message_never_leaks_a_key_in_the_url(self):
        respx.get("http://93.184.216.34/search").mock(
            side_effect=httpx.ConnectError(
                "connection failed for http://93.184.216.34/search?api_key=SUPERSECRET123456"
            )
        )
        with pytest.raises(FetchError) as excinfo:
            http_get_json("http://93.184.216.34/search", params={"q": "x"})
        assert "SUPERSECRET123456" not in str(excinfo.value)

    @respx.mock
    def test_response_body_echoing_a_key_is_redacted(self):
        respx.get("http://93.184.216.34/search").mock(
            return_value=httpx.Response(
                500,
                text="upstream rejected request to /search?api_key=SUPERSECRET123456&q=x",
            )
        )
        out = http_get_json("http://93.184.216.34/search", params={"q": "x"})
        assert out["status"] == 500
        assert "SUPERSECRET123456" not in (out["text"] or "")

    @respx.mock
    def test_bearer_token_in_error_body_is_redacted(self):
        respx.get("http://93.184.216.34/search").mock(
            return_value=httpx.Response(403, text="Authorization: Bearer abc123.def456-secret")
        )
        out = http_get_json("http://93.184.216.34/search")
        assert "abc123.def456-secret" not in (out["text"] or "")
