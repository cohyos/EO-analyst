"""Unit tests for `eoa.api.app`'s Q2-9 (global body-size limit) and Q2-10 (generic 500 handler)
fixes. `fastapi.testclient.TestClient` per the pattern in `tests/unit/test_api_smoke.py`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from eoa import db

    monkeypatch.setattr(db, "get_pool", lambda: object())
    monkeypatch.setattr(db, "close_pool", lambda: None)

    from eoa.api.app import create_app

    app = create_app()
    # `raise_server_exceptions=False`: an unhandled exception IS converted to a proper 500
    # response by `ServerErrorMiddleware` using our registered handler (see `app.py`'s
    # `_unhandled_exception_handler`) -- Starlette's `ServerErrorMiddleware` re-raises the
    # original exception into the test process afterward *by design* ("allows test clients to
    # optionally raise the error within the test case"), which is exactly what the default
    # `raise_server_exceptions=True` surfaces. These tests want the response itself, not that
    # re-raise.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# --------------------------------------------------------------------------
# Q2-9: global request body-size cap (413)
# --------------------------------------------------------------------------


def test_oversized_body_rejected_with_413(client: TestClient) -> None:
    from eoa.api.app import _MAX_BODY_BYTES

    oversized = "x" * (_MAX_BODY_BYTES + 1)
    r = client.post("/api/investigations", content=oversized, headers={"content-type": "application/json"})
    assert r.status_code == 413
    body = r.json()
    assert body["error"]["code"] == "payload_too_large"


def test_oversized_body_rejected_via_content_length_precheck(client: TestClient) -> None:
    """A `Content-Length` alone (before any body bytes are read) is enough to reject."""
    from eoa.api.app import _MAX_BODY_BYTES

    too_big = _MAX_BODY_BYTES + 1
    r = client.post(
        "/api/investigations",
        content=b"{}",
        headers={"content-type": "application/json", "content-length": str(too_big)},
    )
    assert r.status_code == 413


def test_normal_sized_body_passes_through_untouched(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: the middleware must not corrupt/empty a normal-sized body -- a classic
    `BaseHTTPMiddleware` pitfall when the body is consumed without re-caching it for downstream."""
    from eoa.api import services

    captured: dict = {}

    def fake_start_investigation(question, item_id):
        captured["question"] = question
        captured["item_id"] = item_id
        return 42

    monkeypatch.setattr(services, "start_investigation", fake_start_investigation)

    r = client.post("/api/investigations", json={"question": "מה קרה?", "item_id": None})
    assert r.status_code == 200
    assert r.json() == {"job_id": 42}
    assert captured["question"] == "מה קרה?"


def test_settings_put_still_enforces_its_own_tighter_cap(client: TestClient) -> None:
    """`PUT /api/settings/{name}` keeps its own 256 KB cap (enforced in `services.
    write_settings_yaml`, which reports it as a validation error in the `{"ok": false, "errors":
    [...]}` body rather than an HTTP error status -- see that route's existing contract), well
    under the new global 1 MB default -- exercised here only to confirm the two layers coexist
    and the request isn't rejected by the *global* middleware before reaching that check."""
    # A body under the global 1 MB cap but comfortably over the settings-specific 256 KB one.
    big_yaml = "companies:\n" + ("  - name: x\n" * 40_000)  # ~480 KB
    assert 256_000 < len(big_yaml.encode()) < 1_000_000
    r = client.put("/api/settings/watchlist", json={"yaml": big_yaml})
    assert r.status_code == 200  # not rejected by the *global* 1 MB middleware
    body = r.json()
    assert body["ok"] is False
    assert any("256" in e for e in body["errors"])  # the settings-layer's own tighter cap fired


# --------------------------------------------------------------------------
# Q2-10: generic 500 handler (never `str(exc)` to the client)
# --------------------------------------------------------------------------


def test_unhandled_exception_returns_generic_message_and_error_id_not_str_exc(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from eoa.api import services

    secret_detail = "psycopg.OperationalError: password authentication failed for user 'eoa' at 10.0.0.7"

    def boom():
        raise RuntimeError(secret_detail)

    monkeypatch.setattr(services, "morning", boom)

    r = client.get("/api/morning")
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["message_he"] == "שגיאה פנימית בשרת"
    assert secret_detail not in str(body)
    assert "error_id" in body["error"]["detail"]
    assert len(body["error"]["detail"]["error_id"]) == 32  # uuid4().hex


def test_unhandled_exception_logs_traceback_with_matching_error_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`log = structlog.get_logger(__name__)` in this app renders straight to stdout by default
    (no `structlog.configure()` routing it through stdlib `logging` at the API layer -- that
    setup only happens in `eoa.orchestrator.main`), so this asserts against captured stdout
    rather than `caplog`."""
    from eoa.api import services

    def boom():
        raise RuntimeError("boom - must never reach the client")

    monkeypatch.setattr(services, "morning", boom)

    r = client.get("/api/morning")

    error_id = r.json()["error"]["detail"]["error_id"]
    out = capsys.readouterr().out
    assert error_id in out
    assert "boom - must never reach the client" in out  # traceback IS logged server-side
    assert "boom - must never reach the client" not in r.text  # but never sent to the client


# --------------------------------------------------------------------------
# Regression: SSE streaming (/api/ask) still works with the new middleware in
# the stack -- BaseHTTPMiddleware has historically had streaming-response
# pitfalls in older Starlette versions.
# --------------------------------------------------------------------------


def test_ask_sse_stream_still_works_through_body_size_middleware(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from eoa.api import services
    from eoa.llm import ollama_client

    monkeypatch.setattr(services, "ask_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(services, "ask_build_messages", lambda *a, **k: ([], []))
    monkeypatch.setattr(ollama_client, "resolve_provider_info", lambda provider: ("ollama", "resident"))
    monkeypatch.setattr(ollama_client, "chat_stream", lambda *a, **k: iter(["hello", " world"]))

    with client.stream("POST", "/api/ask", json={"question": "מה קרה?"}) as r:
        assert r.status_code == 200
        chunks = list(r.iter_lines())

    joined = "\n".join(chunks)
    assert '"type": "citations"' in joined or '"type":"citations"' in joined
    assert "hello" in joined
    assert '"type": "done"' in joined or '"type":"done"' in joined
