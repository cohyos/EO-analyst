"""Tests for eoa.notify.ntfy.send() -- the JSON publish endpoint (bug F11).

F11: the previous implementation sent the notification title as a raw `Title` HTTP header.
httpx/h11 encode header values as (effectively) ASCII, so any non-ASCII title -- every Hebrew
report title -- raised `UnicodeEncodeError` and silently dropped the notification. `send()`
now POSTs a JSON body (https://docs.ntfy.sh/publish/#publish-as-json) to the ntfy server root
instead, carrying `title`/`message` as UTF-8 JSON fields, never HTTP header bytes.
"""

from __future__ import annotations

import json

import httpx
import respx

from eoa.notify import ntfy

NTFY_BASE = "http://127.0.0.1:8090"


@respx.mock
def test_hebrew_title_publishes_via_json_endpoint(monkeypatch):
    """A Hebrew title/body must not raise, and must be sent as UTF-8 JSON, not an ASCII header."""
    monkeypatch.setenv("NTFY_URL", NTFY_BASE)

    route = respx.post(NTFY_BASE).mock(return_value=httpx.Response(200, json={"id": "abc123"}))

    hebrew_title = "דוח יומי מוכן"
    hebrew_body = "תקציר: שלושה פריטים חדשים נמצאו הלילה."

    result = ntfy.send(
        hebrew_title,
        hebrew_body,
        priority="high",
        tags=["page_facing_up"],
        to_public_fallback=False,  # keep this test hitting only the mocked self-hosted server
    )

    assert route.called
    assert result.ok is True
    assert result.message_id == "abc123"

    request = route.calls.last.request
    # The whole point of the fix: this must be JSON, not headers -- so no header ever carries
    # non-ASCII bytes.
    assert request.headers["content-type"].startswith("application/json")
    for name, value in request.headers.items():
        value.encode("ascii")  # would raise UnicodeEncodeError pre-fix if title were a header

    payload = json.loads(request.content.decode("utf-8"))
    assert payload["title"] == hebrew_title
    assert payload["message"] == hebrew_body
    assert payload["priority"] == ntfy.PRIORITY["high"]
    assert payload["tags"] == ["page_facing_up"]
    assert payload["topic"]  # topic travels in the JSON body now, not the URL path


@respx.mock
def test_actions_are_sent_as_json_objects(monkeypatch):
    """`actions` must be an array of ntfy JSON action objects, not the old header mini-DSL."""
    monkeypatch.setenv("NTFY_URL", NTFY_BASE)
    route = respx.post(NTFY_BASE).mock(return_value=httpx.Response(200, json={"id": "xyz"}))

    ntfy.send(
        "title",
        "body",
        actions=[{"kind": "view", "label": "Open", "url": "https://example.com"}],
        to_public_fallback=False,
    )

    payload = json.loads(route.calls.last.request.content.decode("utf-8"))
    assert payload["actions"] == [{"action": "view", "label": "Open", "url": "https://example.com"}]


@respx.mock
def test_http_error_status_returns_not_ok(monkeypatch):
    monkeypatch.setenv("NTFY_URL", NTFY_BASE)
    respx.post(NTFY_BASE).mock(return_value=httpx.Response(500))

    result = ntfy.send("t", "b", to_public_fallback=False)
    assert result.ok is False
