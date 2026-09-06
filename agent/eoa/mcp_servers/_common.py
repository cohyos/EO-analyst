"""Shared helpers for the `eoa.mcp_servers.*` stdio servers: a small sync HTTP client with the
project's SSRF guard, and a uniform "not configured" shape for a missing API key.

Q2-14 (2026-09-06): the old version validated only the initial URL with
`assert_public_http_url` and then let `httpx` follow redirects internally --
connecting to (and trusting) every hop with zero SSRF re-validation in between,
exactly the TOCTOU gap Q2-4 already closed for `eoa.fetch.remote._fetch_local` /
`eoa.fetch.html.fetch_page`. These MCP data-source calls hit a small, fixed set
of configured API hosts (SAM.gov, Congress.gov, EPO OPS, PatentsView, Janes,
...), not arbitrary content-derived URLs, so this reuses the same
`assert_public_http_url` guard but keeps its own manual sync redirect loop
(bounded to `_MAX_REDIRECT_HOPS`) rather than pulling in the async
`fetch_page`/`pin_ips` machinery those modules use for arbitrary web content --
every hop is re-validated *and* must stay on the same host the call started
on, since none of the documented API surfaces this project talks to are known
to redirect cross-host; a real API that legitimately needs a cross-host
redirect will fail loudly here (a reportable "refusing cross-host redirect"
error) rather than being silently allowed to bypass the guard.

Q2-15 (2026-09-06): every error surfaced by this module -- SSRF/redirect
rejections, transport/DNS failures, non-2xx response bodies -- is scrubbed
with `redact_secrets` before it is returned, so a key or token that leaks into
an exception's own text (or an upstream error page echoing the request back)
never reaches `mcp_calls.error` or the model.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from eoa.errors import FetchError
from eoa.fetch.remote import assert_public_http_url
from eoa.security.redact import redact_secrets

DEFAULT_TIMEOUT_S = 20.0
USER_AGENT = "eo-analyst-mcp/0.1"

# Q2-14: bounds the manual redirect loop in `_request` -- matches the spirit of
# `eoa.fetch.html._MAX_REDIRECT_HOPS` (5) but tighter, since a well-behaved JSON API should never
# need more than a hop or two (typically zero).
_MAX_REDIRECT_HOPS = 3


def not_configured(*env_names: str) -> str:
    """Uniform, honest "no key, no automation" response -- never a fabricated result."""
    return json.dumps(
        {
            "error": "not_configured",
            "message": f"missing required environment variable(s): {', '.join(env_names)}",
        },
        ensure_ascii=False,
    )


def http_get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """GET `url`, return `{"status": int, "json": <parsed body or None>, "text": <raw body>}`."""
    hdrs = {"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
    resp = _request("GET", url, params=params, headers=hdrs, timeout_s=timeout_s)
    return _parse_response(resp)


def http_post_json(
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    hdrs = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        **(headers or {}),
    }
    resp = _request("POST", url, json_body=json_body, headers=hdrs, timeout_s=timeout_s)
    return _parse_response(resp)


def http_post_form(
    url: str,
    *,
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """POST `url` with a form-urlencoded body (OAuth2 client-credentials token endpoints, etc.)."""
    hdrs = {"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
    resp = _request("POST", url, data=data, headers=hdrs, timeout_s=timeout_s)
    return _parse_response(resp)


def _request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    headers: dict[str, str],
    timeout_s: float,
) -> httpx.Response:
    """One HTTP request with the Q2-14 SSRF-safe manual redirect loop and Q2-15 error redaction.

    Every hop (the initial URL and every `Location` header that follows) is validated with
    `assert_public_http_url` *before* it is requested -- never after the fact, closing the same
    TOCTOU/DNS-rebinding gap Q2-4 closed for `eoa.fetch.remote._fetch_local`. A redirect is also
    refused outright if it points at a different host than the one the call started on: none of
    the documented API surfaces this project talks to (SAM.gov, Congress.gov, EPO OPS,
    PatentsView, Janes, USAspending, Federal Register, DSCA) are known to need a cross-host
    redirect, so one appearing is far more likely a misconfiguration or a hijacked response than
    legitimate API behavior -- it is reported as an error, never silently followed.

    Any failure along the way (guard rejection, cross-host/too-many-redirects, transport/DNS
    error) is re-raised as a `FetchError` whose message has already been through
    `redact_secrets`, so a key that leaked into the original exception's own text can never reach
    a caller, a log line, or `mcp_calls.error`.
    """
    try:
        assert_public_http_url(url)
        original_host = urlsplit(url).hostname
        current_url = url
        with httpx.Client(timeout=timeout_s, follow_redirects=False) as client:
            for hop in range(_MAX_REDIRECT_HOPS + 1):
                resp = client.request(
                    method,
                    current_url,
                    params=params if hop == 0 else None,
                    json=json_body if hop == 0 else None,
                    data=data if hop == 0 else None,
                    headers=headers,
                )
                if not resp.is_redirect:
                    return resp
                location = resp.headers.get("location")
                if not location:
                    return resp
                next_url = urljoin(current_url, location)
                assert_public_http_url(next_url)
                next_host = urlsplit(next_url).hostname
                if next_host != original_host:
                    raise FetchError(f"refusing cross-host redirect from {original_host!r} to {next_host!r}")
                current_url = next_url
            raise FetchError(f"too many redirects (> {_MAX_REDIRECT_HOPS}) fetching the configured API URL")
    except FetchError as exc:
        raise FetchError(redact_secrets(str(exc))[:2000]) from exc
    except httpx.HTTPError as exc:
        raise FetchError(redact_secrets(str(exc))[:2000]) from exc
    except OSError as exc:  # DNS failures etc. surface as OSError from socket.getaddrinfo
        raise FetchError(redact_secrets(str(exc))[:2000]) from exc


def _parse_response(resp: httpx.Response) -> dict[str, Any]:
    body: Any = None
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        body = None
    text = None if body is not None else redact_secrets(resp.text[:4000])
    return {"status": resp.status_code, "json": body, "text": text}


def truncate_list(items: list[Any], limit: int) -> list[Any]:
    return items[: max(0, limit)]


def json_out(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)
