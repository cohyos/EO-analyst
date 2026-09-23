"""Tests for remote-access authentication (ADR-008, `agent/eoa/api/auth.py`).

Follows the `TestClient` pattern in `tests/unit/test_api_smoke.py`; DB pool is monkeypatched so no
real Postgres is needed. The trust decision hinges on the ASGI scope's `client` tuple, which
Starlette's `TestClient` lets a test override directly (`client=("127.0.0.1", ...)` for a trusted
loopback peer, `client=("100.70.157.25", 5000)` for a simulated remote Tailscale peer) -- no real
network involved.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_remote_access_auth.py -q``
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from eoa.api import auth

LOOPBACK = ("127.0.0.1", 12345)
REMOTE = ("100.70.157.25", 5000)


class _FakeRemoteAccessCfg:
    def __init__(
        self,
        enabled: bool = True,
        token_required_for_writes: bool = True,
        passcode_hash: str = "",
    ) -> None:
        self.enabled = enabled
        self.token_required_for_writes = token_required_for_writes
        self.passcode_hash = passcode_hash
        self.trusted_local_only = True


class _FakeApiCfg:
    def __init__(self, remote_access: _FakeRemoteAccessCfg) -> None:
        self.remote_access = remote_access


class _FakeSettings:
    def __init__(self, remote_access: _FakeRemoteAccessCfg) -> None:
        self.api = _FakeApiCfg(remote_access)


@pytest.fixture(autouse=True)
def _reset_auth_state() -> Iterator[None]:
    """Every test starts with a clean session store / rate-limit table / passcode cache."""
    auth.clear_all_sessions()
    auth.reset_all_rate_limits()
    auth.reset_passcode_cache()
    yield
    auth.clear_all_sessions()
    auth.reset_all_rate_limits()
    auth.reset_passcode_cache()


@pytest.fixture()
def app_client(monkeypatch: pytest.MonkeyPatch):
    """Returns a factory `make(client=..., enabled=True, ...)` -> TestClient."""
    from eoa import db

    monkeypatch.setattr(db, "get_pool", lambda: object())
    monkeypatch.setattr(db, "close_pool", lambda: None)

    from eoa.api import services
    from eoa.api.app import create_app

    monkeypatch.setattr(
        services,
        "read_settings_yaml",
        lambda name: "companies: []\n",
    )
    monkeypatch.setattr(services, "settings_revision", lambda name: "abc123")
    monkeypatch.setattr(services, "write_settings_yaml", lambda *a, **k: [])
    monkeypatch.setattr(services, "create_lesson", lambda kind, text: {"id": 1, "kind": kind, "text": text})

    app = create_app()

    def make(*, client: tuple[str, int], enabled: bool = True, token_required_for_writes: bool = True):
        cfg = _FakeRemoteAccessCfg(enabled=enabled, token_required_for_writes=token_required_for_writes)
        monkeypatch.setattr(auth, "settings", lambda: _FakeSettings(cfg))
        # https:// base_url: our session cookie is `Secure`, and httpx's cookie jar (like a real
        # browser) only stores/resends a Secure cookie over an https-scheme request -- matching
        # the real deployment (Tailscale Serve terminates TLS in front of this API).
        return TestClient(app, client=client, base_url="https://testserver")

    return make


# --------------------------------------------------------------------------
# Loopback bypass
# --------------------------------------------------------------------------


def test_loopback_client_bypasses_auth_entirely(app_client) -> None:
    client = app_client(client=LOOPBACK, enabled=True)
    r = client.get("/api/settings/watchlist")
    assert r.status_code == 200


def test_loopback_client_via_forwarded_for_with_non_loopback_peer_is_not_trusted(app_client) -> None:
    """X-Forwarded-For is honoured only when the *peer* itself is loopback -- a directly-connecting
    non-loopback peer that merely sets the header must not be able to spoof trust."""
    client = app_client(client=REMOTE, enabled=True)
    r = client.get("/api/settings/watchlist", headers={"X-Forwarded-For": "127.0.0.1"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "auth_required"


def test_disabled_remote_access_is_a_full_noop_even_for_remote_peers(app_client) -> None:
    client = app_client(client=REMOTE, enabled=False)
    r = client.get("/api/settings/watchlist")
    assert r.status_code == 200


# --------------------------------------------------------------------------
# Remote, unauthenticated
# --------------------------------------------------------------------------


def test_remote_client_without_session_gets_401_auth_required(app_client) -> None:
    client = app_client(client=REMOTE, enabled=True)
    r = client.get("/api/settings/watchlist")
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "auth_required"
    assert "message_he" in body["error"]


def test_remote_peer_via_loopback_forwarded_for_is_gated(app_client) -> None:
    """Verification scenario from the task: a request that reaches the API from loopback (as
    `tailscale serve` always does) but carries a non-loopback X-Forwarded-For must be treated as
    remote and rejected without a session."""
    client = app_client(client=LOOPBACK, enabled=True)
    r = client.get("/api/settings/watchlist", headers={"X-Forwarded-For": "100.70.1.2"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "auth_required"


def test_spoofed_first_position_loopback_does_not_bypass_session(app_client) -> None:
    """F06: a remote caller cannot defeat the session gate by prepending a fake `127.0.0.1` to
    `X-Forwarded-For` -- only the LAST (proxy-appended) hop is trusted. Chain here simulates the
    real client's genuine tailscale address (`100.70.157.25`, the deployment's own observed
    address) being appended by the trusted local proxy after an attacker-supplied first value."""
    client = app_client(client=LOOPBACK, enabled=True)
    r = client.get(
        "/api/settings/watchlist",
        headers={"X-Forwarded-For": "127.0.0.1, 100.70.157.25"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "auth_required"


def test_multiple_spoofed_loopback_entries_before_real_remote_hop_still_gated(app_client) -> None:
    """Same bypass attempt with several spoofed loopback-looking entries stacked in front of the
    real, proxy-appended remote address -- still must not bypass the session gate."""
    client = app_client(client=LOOPBACK, enabled=True)
    r = client.get(
        "/api/settings/watchlist",
        headers={"X-Forwarded-For": "127.0.0.1, localhost, 100.70.157.25"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "auth_required"


def test_last_hop_loopback_is_still_trusted(app_client) -> None:
    """If the LAST (proxy-appended, trustworthy) entry is itself loopback -- i.e. the chain's
    true final hop before this server was loopback -- the original no-session-required behaviour
    is preserved, regardless of what a caller put earlier in the header."""
    client = app_client(client=LOOPBACK, enabled=True)
    r = client.get(
        "/api/settings/watchlist",
        headers={"X-Forwarded-For": "100.70.1.2, 127.0.0.1"},
    )
    assert r.status_code == 200


def test_static_asset_paths_are_not_gated(app_client) -> None:
    """The SPA shell itself must still load for a remote, unauthenticated client so the client-side
    AccessGate can render (only /api/* and /ws/* are gated)."""
    client = app_client(client=REMOTE, enabled=True)
    r = client.get("/some-non-api-path")
    # Whatever the SPA static mount / 404 handling does, it must not be our 401 auth envelope.
    assert r.status_code != 401 or r.json().get("error", {}).get("code") != "auth_required"


# --------------------------------------------------------------------------
# Login: success / failure / rate limiting
# --------------------------------------------------------------------------


def test_login_fails_without_a_configured_passcode(app_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EOA_ACCESS_PASSCODE", raising=False)
    client = app_client(client=REMOTE, enabled=True)
    r = client.post("/api/auth/login", json={"passcode": "anything"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_passcode"


def test_login_wrong_passcode_401(app_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOA_ACCESS_PASSCODE", "correct-horse")
    client = app_client(client=REMOTE, enabled=True)
    r = client.post("/api/auth/login", json={"passcode": "wrong"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_passcode"


def test_login_success_sets_cookie_with_expected_flags(
    app_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EOA_ACCESS_PASSCODE", "correct-horse")
    client = app_client(client=REMOTE, enabled=True)
    r = client.post("/api/auth/login", json={"passcode": "correct-horse"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    set_cookie = r.headers.get("set-cookie", "")
    assert auth.SESSION_COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=strict" in set_cookie or "samesite=strict" in set_cookie.lower()
    assert f"Max-Age={auth.SESSION_TTL_SECONDS}" in set_cookie

    # Regression guard: `expires` must be an absolute HTTP-date within a few minutes of "now +
    # 12h", never something derived by mishandling `expires_at.timestamp()` as a relative offset
    # (that bug produced a cookie dated ~55 years in the future -- see auth.py's `login`).
    import email.utils

    match = re.search(r"expires=([^;]+);", set_cookie, re.IGNORECASE)
    assert match, f"no expires= attribute in Set-Cookie: {set_cookie!r}"
    expires_dt = email.utils.parsedate_to_datetime(match.group(1))
    delta_seconds = (expires_dt - datetime.now(UTC)).total_seconds()
    assert abs(delta_seconds - auth.SESSION_TTL_SECONDS) < 300, (
        f"cookie expires {delta_seconds}s from now, expected ~{auth.SESSION_TTL_SECONDS}s"
    )


def test_login_then_session_cookie_authenticates_subsequent_requests(
    app_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EOA_ACCESS_PASSCODE", "correct-horse")
    client = app_client(client=REMOTE, enabled=True)

    r_login = client.post("/api/auth/login", json={"passcode": "correct-horse"})
    assert r_login.status_code == 200

    # httpx.Client (TestClient's base) persists Set-Cookie across requests within the same client.
    r_get = client.get("/api/settings/watchlist")
    assert r_get.status_code == 200
    assert r_get.headers.get(auth.REMOTE_SESSION_HEADER.decode()) == "1"


def test_login_rate_limited_after_five_failures(app_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOA_ACCESS_PASSCODE", "correct-horse")
    client = app_client(client=REMOTE, enabled=True)

    for _ in range(auth.RATE_LIMIT_MAX_ATTEMPTS):
        r = client.post("/api/auth/login", json={"passcode": "wrong"})
        assert r.status_code == 401

    r_limited = client.post("/api/auth/login", json={"passcode": "wrong"})
    assert r_limited.status_code == 429
    assert r_limited.json()["error"]["code"] == "rate_limited"

    # Even the *correct* passcode is blocked once the window is exhausted.
    r_still_limited = client.post("/api/auth/login", json={"passcode": "correct-horse"})
    assert r_still_limited.status_code == 429


def test_login_disabled_when_remote_access_off(app_client) -> None:
    client = app_client(client=REMOTE, enabled=False)
    r = client.post("/api/auth/login", json={"passcode": "anything"})
    assert r.status_code == 404


def test_logout_clears_session(app_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOA_ACCESS_PASSCODE", "correct-horse")
    client = app_client(client=REMOTE, enabled=True)

    client.post("/api/auth/login", json={"passcode": "correct-horse"})
    assert client.get("/api/settings/watchlist").status_code == 200

    r_logout = client.post("/api/auth/logout")
    assert r_logout.status_code == 200

    r_after = client.get("/api/settings/watchlist")
    assert r_after.status_code == 401


# --------------------------------------------------------------------------
# X-EOA-Token on mutating requests, for a remote session
# --------------------------------------------------------------------------


def test_remote_authenticated_write_requires_token_when_configured(
    app_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EOA_ACCESS_PASSCODE", "correct-horse")
    monkeypatch.setenv("EOA_API_TOKEN", "s3cr3t")
    client = app_client(client=REMOTE, enabled=True, token_required_for_writes=True)

    client.post("/api/auth/login", json={"passcode": "correct-horse"})

    r_no_token = client.put("/api/settings/watchlist", json={"yaml": "companies: []\n"})
    assert r_no_token.status_code == 401
    assert r_no_token.json()["error"]["code"] == "unauthorized"

    r_wrong_token = client.put(
        "/api/settings/watchlist",
        json={"yaml": "companies: []\n"},
        headers={"X-EOA-Token": "wrong"},
    )
    assert r_wrong_token.status_code == 401

    r_right_token = client.put(
        "/api/settings/watchlist",
        json={"yaml": "companies: []\n"},
        headers={"X-EOA-Token": "s3cr3t"},
    )
    assert r_right_token.status_code == 200


def test_remote_authenticated_write_without_env_token_configured_is_allowed(
    app_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EOA_ACCESS_PASSCODE", "correct-horse")
    monkeypatch.delenv("EOA_API_TOKEN", raising=False)
    client = app_client(client=REMOTE, enabled=True, token_required_for_writes=True)

    client.post("/api/auth/login", json={"passcode": "correct-horse"})
    r = client.put("/api/settings/watchlist", json={"yaml": "companies: []\n"})
    assert r.status_code == 200


def test_remote_authenticated_write_allowed_without_token_when_flag_off(
    app_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    # /api/lessons has no route-level token check of its own (unlike /api/settings/*, which
    # always enforces EOA_API_TOKEN independently -- see routes/settings.py's `_require_token`),
    # so it isolates the middleware's own `token_required_for_writes` gate.
    monkeypatch.setenv("EOA_ACCESS_PASSCODE", "correct-horse")
    monkeypatch.setenv("EOA_API_TOKEN", "s3cr3t")
    client = app_client(client=REMOTE, enabled=True, token_required_for_writes=False)

    client.post("/api/auth/login", json={"passcode": "correct-horse"})
    r = client.post("/api/lessons", json={"kind": "note", "text": "בדיקה"})
    assert r.status_code == 200


# --------------------------------------------------------------------------
# WebSocket /ws/status honours the same session cookie
# --------------------------------------------------------------------------


def test_ws_status_remote_without_session_is_rejected(app_client) -> None:
    client = app_client(client=REMOTE, enabled=True)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/status"):
            pass
    assert exc_info.value.code == 4401


def test_ws_status_loopback_client_connects_without_session(
    app_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from eoa.api import services
    from eoa.api.routes import status as status_route

    class _FakeGateStatus:
        def status(self) -> dict:
            return {
                "at": "2026-01-01T00:00:00+00:00",
                "gpu": {
                    "available": False,
                    "vram_total_mb": 0,
                    "vram_used_mb": 0,
                    "vram_free_mb": 0,
                    "util_pct": 0,
                    "temp_c": 0,
                },
                "ram": {"free_mb": 1000, "total_mb": 2000},
                "disk_free_gb": 100.0,
                "loaded_models": [],
                "batch_window": False,
                "recent_decisions": [],
            }

    class _FakeStatusApiCfg:
        status_push_seconds = 0.01

    class _FakeStatusSettings:
        api = _FakeStatusApiCfg()

    monkeypatch.setattr(
        services,
        "services_status",
        lambda: {"postgres": True, "ollama": False, "searxng": True, "ntfy": True},
    )
    monkeypatch.setattr(
        services,
        "pipeline_status",
        lambda: {
            "current_job": None,
            "queue_depth": 0,
            "stage": None,
            "night_window": False,
            "next_run_at": None,
            "last_run": None,
        },
    )
    monkeypatch.setattr(status_route, "gate", lambda: _FakeGateStatus())
    monkeypatch.setattr(status_route, "settings", lambda: _FakeStatusSettings())
    monkeypatch.setattr(services, "latest_run_log_id", lambda: 0)
    monkeypatch.setattr(services, "run_log_since", lambda last_id: ([], last_id))

    client = app_client(client=LOOPBACK, enabled=True)
    with client.websocket_connect("/ws/status") as ws:
        frame = ws.receive_json()
    assert "services" in frame


def test_ws_status_remote_with_valid_session_connects(
    app_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from eoa.api import services
    from eoa.api.routes import status as status_route

    class _FakeGateStatus:
        def status(self) -> dict:
            return {
                "at": "2026-01-01T00:00:00+00:00",
                "gpu": {
                    "available": False,
                    "vram_total_mb": 0,
                    "vram_used_mb": 0,
                    "vram_free_mb": 0,
                    "util_pct": 0,
                    "temp_c": 0,
                },
                "ram": {"free_mb": 1000, "total_mb": 2000},
                "disk_free_gb": 100.0,
                "loaded_models": [],
                "batch_window": False,
                "recent_decisions": [],
            }

    class _FakeStatusApiCfg:
        status_push_seconds = 0.01

    class _FakeStatusSettings:
        api = _FakeStatusApiCfg()

    monkeypatch.setattr(
        services,
        "services_status",
        lambda: {"postgres": True, "ollama": False, "searxng": True, "ntfy": True},
    )
    monkeypatch.setattr(
        services,
        "pipeline_status",
        lambda: {
            "current_job": None,
            "queue_depth": 0,
            "stage": None,
            "night_window": False,
            "next_run_at": None,
            "last_run": None,
        },
    )
    monkeypatch.setattr(status_route, "gate", lambda: _FakeGateStatus())
    monkeypatch.setattr(status_route, "settings", lambda: _FakeStatusSettings())
    monkeypatch.setattr(services, "latest_run_log_id", lambda: 0)
    monkeypatch.setattr(services, "run_log_since", lambda last_id: ([], last_id))

    monkeypatch.setenv("EOA_ACCESS_PASSCODE", "correct-horse")
    client = app_client(client=REMOTE, enabled=True)
    client.post("/api/auth/login", json={"passcode": "correct-horse"})

    # `TestClient.websocket_connect` always dials `ws://testserver` regardless of the client's
    # `base_url` (see starlette.testclient), so the httpx cookie jar -- which only attaches a
    # `Secure` cookie to an https-scheme request -- won't resend it here automatically. Attach it
    # explicitly, the way a browser would send it over the real `wss://` connection in production.
    session_cookie = client.cookies.get(auth.SESSION_COOKIE_NAME)
    assert session_cookie, "login did not set the session cookie"

    with client.websocket_connect(
        "/ws/status", headers={"Cookie": f"{auth.SESSION_COOKIE_NAME}={session_cookie}"}
    ) as ws:
        frame = ws.receive_json()
    assert "services" in frame


# --------------------------------------------------------------------------
# Passcode hashing (argon2/bcrypt if installed, else PBKDF2-HMAC-SHA256)
# --------------------------------------------------------------------------


def test_hash_and_verify_roundtrip() -> None:
    hashed = auth.hash_passcode("s3cr3t-passcode")
    assert hashed != "s3cr3t-passcode"
    assert auth.verify_passcode("s3cr3t-passcode", hashed)
    assert not auth.verify_passcode("wrong", hashed)


def test_verify_passcode_rejects_malformed_hash_without_raising() -> None:
    assert auth.verify_passcode("anything", "not-a-real-hash") is False
    assert auth.verify_passcode("anything", "") is False
    assert auth.verify_passcode("", "some-hash") is False
