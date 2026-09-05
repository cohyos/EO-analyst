"""Shared helpers for the `eoa.mcp_servers.*` stdio servers: a small sync HTTP client with the
project's SSRF guard, and a uniform "not configured" shape for a missing API key."""

from __future__ import annotations

import json
from typing import Any

import httpx

from eoa.fetch.remote import assert_public_http_url

DEFAULT_TIMEOUT_S = 20.0
USER_AGENT = "eo-analyst-mcp/0.1"


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
    assert_public_http_url(url)
    hdrs = {"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
    with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
        resp = client.get(url, params=params, headers=hdrs)
    return _parse_response(resp)


def http_post_json(
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    assert_public_http_url(url)
    hdrs = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
    with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
        resp = client.post(url, json=json_body, headers=hdrs)
    return _parse_response(resp)


def http_post_form(
    url: str,
    *,
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """POST `url` with a form-urlencoded body (OAuth2 client-credentials token endpoints, etc.)."""
    assert_public_http_url(url)
    hdrs = {"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
    with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
        resp = client.post(url, data=data, headers=hdrs)
    return _parse_response(resp)


def _parse_response(resp: httpx.Response) -> dict[str, Any]:
    body: Any = None
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        body = None
    return {"status": resp.status_code, "json": body, "text": None if body is not None else resp.text[:4000]}


def truncate_list(items: list[Any], limit: int) -> list[Any]:
    return items[: max(0, limit)]


def json_out(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)
