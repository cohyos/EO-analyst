"""Remote-access authentication (ADR-008, docs/adr/008-remote-access.md).

Today the API has no authentication at all because it was built for a single local user
(``config/config.yaml``'s ``api`` binds to ``127.0.0.1``). Now that Tailscale lets the same
machine be reached from a phone/laptop anywhere, this module adds an *optional* gate
(``api.remote_access.enabled``) that:

* trusts a loopback client exactly as before -- no session, no cookie, nothing changes for the
  primary local usage this project was built around;
* requires any other client (Tailscale ``100.64.0.0/10``, LAN, or anything else) to log in with a
  shared passcode (``POST /api/auth/login``) before any ``/api/*`` route or the ``/ws/status``
  socket responds, via a short-lived, server-side session cookie.

``RemoteAccessMiddleware`` is a *raw* ASGI middleware (not ``BaseHTTPMiddleware``) specifically so
it can also gate the ``websocket`` scope -- ``BaseHTTPMiddleware`` only ever sees ``http`` scopes,
so a per-route check inside ``routes/status.py`` would be the only alternative, and this keeps
that file untouched. See ``docs/adr/008-remote-access.md`` for the full threat model (Tailscale
Serve only, no port forwarding, no Funnel) and what this does *not* protect against.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from typing import Any

import structlog
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from starlette.datastructures import Headers

from eoa.api.errors import APIError
from eoa.api.routes.settings import _require_token
from eoa.config import settings

log = structlog.get_logger(__name__)

router = APIRouter(tags=["auth"])

SESSION_COOKIE_NAME = "eoa_session"
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days (ADR-008 addendum 2026-09-08); overridable via
# api.remote_access.session_ttl_days -- see session_ttl_seconds()

RATE_LIMIT_MAX_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 15 * 60  # 5 attempts / 15 min per IP

# Response header set on every successful non-loopback, authenticated response so the SPA can
# show a "remote session active" indicator (web/src/api/real.ts / TopBar.tsx) without ever being
# able to read the HttpOnly session cookie itself.
REMOTE_SESSION_HEADER = b"x-eoa-remote-session"

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


# ---------------------------------------------------------------------------
# Passcode hashing -- argon2 if installed, else bcrypt, else PBKDF2-HMAC-SHA256 (hashlib, always
# available). The output is self-describing so `verify_passcode` never needs to be told which
# scheme produced a given hash (relevant once `passcode_hash` in config.yaml outlives a library
# upgrade/downgrade).
# ---------------------------------------------------------------------------

try:
    from argon2 import PasswordHasher as _Argon2PasswordHasher
    from argon2.exceptions import VerifyMismatchError as _Argon2VerifyMismatchError

    _argon2_hasher: Any = _Argon2PasswordHasher()
except ImportError:  # pragma: no cover - exercised only when argon2-cffi isn't installed
    _argon2_hasher = None
    _Argon2VerifyMismatchError = Exception

try:
    import bcrypt as _bcrypt
except ImportError:  # pragma: no cover - exercised only when bcrypt isn't installed
    _bcrypt = None

_PBKDF2_ITERATIONS = 260_000
_PBKDF2_PREFIX = "pbkdf2_sha256"


def hash_passcode(plain: str) -> str:
    """Hash `plain` with the strongest scheme available: argon2 > bcrypt > PBKDF2-HMAC-SHA256."""
    if _argon2_hasher is not None:
        return _argon2_hasher.hash(plain)
    if _bcrypt is not None:
        return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt()).decode("ascii")
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"{_PBKDF2_PREFIX}${_PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_passcode(plain: str, hashed: str) -> bool:
    """Constant-time verify. Returns False (never raises) for any malformed/unknown hash."""
    if not plain or not hashed:
        return False
    try:
        if hashed.startswith("$argon2"):
            if _argon2_hasher is None:
                return False
            try:
                return bool(_argon2_hasher.verify(hashed, plain))
            except _Argon2VerifyMismatchError:
                return False
        if hashed.startswith(("$2a$", "$2b$", "$2y$")):
            if _bcrypt is None:
                return False
            return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
        if hashed.startswith(f"{_PBKDF2_PREFIX}$"):
            _prefix, iterations_s, salt_hex, derived_hex = hashed.split("$")
            iterations = int(iterations_s)
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(derived_hex)
            derived = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations)
            return hmac.compare_digest(derived, expected)
    except Exception:  # malformed hash of any kind -> never authenticate, never raise
        log.warning("auth.verify_passcode_malformed_hash")
        return False
    return False


# ---------------------------------------------------------------------------
# Effective passcode hash: EOA_ACCESS_PASSCODE (env, plain, hashed once here and never logged)
# takes priority over a pre-hashed config.yaml value. Cached after first use per process --
# `reset_passcode_cache()` is a test hook (also used if the env var changes without a restart).
# ---------------------------------------------------------------------------

_passcode_cache: dict[str, str | None] = {}


def effective_passcode_hash() -> str | None:
    if "hash" not in _passcode_cache:
        plain = os.environ.get("EOA_ACCESS_PASSCODE")
        if plain:
            _passcode_cache["hash"] = hash_passcode(plain)
        else:
            cfg_hash = settings().api.remote_access.passcode_hash
            _passcode_cache["hash"] = cfg_hash or None
    return _passcode_cache["hash"]


def reset_passcode_cache() -> None:
    """Test hook: forget the cached hash so a changed env var / config is picked up again."""
    _passcode_cache.clear()


# ---------------------------------------------------------------------------
# Server-side session store. 2026-09-08 (user decision): persisted in the ``remote_sessions``
# table (migration 0032) so an API restart no longer logs the phone out; the process-local dict
# is a read cache and the fallback when the DB is unavailable (unit tests without a DB, a
# transient outage -- a session created during an outage simply does not survive a restart).
# Only SHA-256(token) is stored server-side; the cookie carries the token itself.
# ---------------------------------------------------------------------------

_sessions_lock = threading.Lock()
_sessions: dict[str, datetime] = {}


def session_ttl_seconds() -> int:
    try:
        days = int(settings().api.remote_access.session_ttl_days)
        if days > 0:
            return days * 24 * 60 * 60
    except Exception:  # pragma: no cover -- settings unavailable in some test harnesses
        pass
    return SESSION_TTL_SECONDS


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _db_exec(sql: str, params: tuple[Any, ...], *, fetch: bool = False) -> Any:
    """Run one statement against the sessions table; ``None`` when the DB is unavailable."""
    try:
        from eoa.db import connection

        with connection(timeout=2) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            if not fetch:
                return True
            row = cur.fetchone()
            # the pool's default row factory yields dicts; normalise to a tuple for callers
            return tuple(row.values()) if isinstance(row, dict) else row
    except Exception as exc:  # DB down / not migrated / no DATABASE_URL (tests)
        log.debug("auth.session_db_unavailable", error=str(exc)[:120])
        return None


def create_session() -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(seconds=session_ttl_seconds())
    with _sessions_lock:
        _sessions[token] = expires_at
    _db_exec(
        "INSERT INTO remote_sessions (token_hash, expires_at) VALUES (%s, %s) "
        "ON CONFLICT (token_hash) DO UPDATE SET expires_at = EXCLUDED.expires_at",
        (_token_hash(token), expires_at),
    )
    _db_exec("DELETE FROM remote_sessions WHERE expires_at < now()", ())
    return token, expires_at


def is_valid_session(token: str | None) -> bool:
    if not token:
        return False
    now = datetime.now(UTC)
    with _sessions_lock:
        expires_at = _sessions.get(token)
        if expires_at is not None:
            if expires_at < now:
                del _sessions[token]
                return False
            return True
    row = _db_exec(
        "SELECT expires_at FROM remote_sessions WHERE token_hash = %s", (_token_hash(token),), fetch=True
    )
    if not row:
        return False
    expires_at = row[0]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < now:
        _db_exec("DELETE FROM remote_sessions WHERE token_hash = %s", (_token_hash(token),))
        return False
    with _sessions_lock:
        _sessions[token] = expires_at
    _db_exec("UPDATE remote_sessions SET last_seen_at = now() WHERE token_hash = %s", (_token_hash(token),))
    return True


def destroy_session(token: str | None) -> None:
    if not token:
        return
    with _sessions_lock:
        _sessions.pop(token, None)
    _db_exec("DELETE FROM remote_sessions WHERE token_hash = %s", (_token_hash(token),))


def clear_all_sessions() -> None:
    """Test hook (process-local cache only; tests never reach the DB)."""
    with _sessions_lock:
        _sessions.clear()


# ---------------------------------------------------------------------------
# Per-IP login rate limit: 5 attempts / 15 min, sliding window.
# ---------------------------------------------------------------------------

_attempts_lock = threading.Lock()
_attempts: dict[str, list[float]] = {}


def _recent_attempts(host: str, now: float) -> list[float]:
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    return [t for t in _attempts.get(host, []) if t >= window_start]


def check_rate_limit(host: str) -> bool:
    """True if `host` still has an attempt budget left this window."""
    now = time.monotonic()
    with _attempts_lock:
        recent = _recent_attempts(host, now)
        _attempts[host] = recent
        return len(recent) < RATE_LIMIT_MAX_ATTEMPTS


def record_failed_attempt(host: str) -> None:
    now = time.monotonic()
    with _attempts_lock:
        recent = _recent_attempts(host, now)
        recent.append(now)
        _attempts[host] = recent


def reset_rate_limit_for(host: str) -> None:
    with _attempts_lock:
        _attempts.pop(host, None)


def reset_all_rate_limits() -> None:
    """Test hook."""
    with _attempts_lock:
        _attempts.clear()


# ---------------------------------------------------------------------------
# Loopback / X-Forwarded-For resolution.
#
# `tailscale serve` proxies an incoming HTTPS connection to this API over plain HTTP from
# 127.0.0.1, carrying the real remote address in `X-Forwarded-For`. So: X-Forwarded-For is
# honoured *only* when the direct TCP peer is itself loopback (that header is otherwise fully
# spoofable by anyone who can reach this API at all, which is the entire point of gating
# non-loopback traffic in the first place).
# ---------------------------------------------------------------------------


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    h = host.strip()
    if h in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def effective_client_host(peer_host: str | None, forwarded_for: str | None) -> str | None:
    """Resolve the address to trust-check for a request whose direct TCP peer is loopback.

    `X-Forwarded-For` is attacker-controlled end to end: a remote client can set an arbitrary
    value before the request ever reaches the trusted local proxy. A well-behaved proxy (and
    `tailscale serve`, when it forwards this header at all) *appends* the hop it saw the
    connection from rather than overwriting the header, so the one entry we can actually trust
    is the LAST one -- not the first, which is whatever the original caller supplied. Taking the
    first value let a remote caller put `127.0.0.1` in front of the chain and bypass the session
    gate entirely (F06). If the last entry itself is not loopback, the request is treated as
    remote regardless of anything earlier in the chain.
    """
    if peer_host and is_loopback_host(peer_host) and forwarded_for:
        parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return peer_host


def _session_token_from_cookie_header(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    jar: SimpleCookie = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:
        return None
    morsel = jar.get(SESSION_COOKIE_NAME)
    return morsel.value if morsel else None


# ---------------------------------------------------------------------------
# ASGI middleware
# ---------------------------------------------------------------------------

_SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
]

_AUTH_EXEMPT_PATHS = {"/api/auth/login", "/api/auth/logout"}
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _error_body(code: str, message_he: str) -> dict:
    return {"error": {"code": code, "message_he": message_he, "detail": None}}


class RemoteAccessMiddleware:
    """Gate every `/api/*` request and the `/ws/status` handshake for non-loopback clients.

    A no-op entirely when `api.remote_access.enabled` is false (the default) -- existing
    loopback-only behaviour is completely unchanged until an operator opts in.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        cfg = settings().api.remote_access
        if not cfg.enabled:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        client = scope.get("client")
        peer_host = client[0] if client else None
        effective_host = effective_client_host(peer_host, headers.get("x-forwarded-for"))
        trusted = is_loopback_host(effective_host)
        path = scope["path"]

        if trusted:
            # Unchanged behaviour: no session required, no headers added.
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            if path.startswith("/ws"):
                token = _session_token_from_cookie_header(headers.get("cookie"))
                if not is_valid_session(token):
                    await send({"type": "websocket.close", "code": 4401})
                    return
            await self.app(scope, receive, send)
            return

        # HTTP, non-loopback from here on.
        if not (path.startswith("/api") or path.startswith("/ws")):
            # Static SPA assets: let index.html/JS load so AccessGate can render client-side.
            await self._call_with_security_headers(scope, receive, send)
            return

        if path in _AUTH_EXEMPT_PATHS:
            await self._call_with_security_headers(scope, receive, send)
            return

        token = _session_token_from_cookie_header(headers.get("cookie"))
        if not is_valid_session(token):
            await self._send_json(
                send, 401, _error_body("auth_required", "נדרש להתחבר כדי לגשת למערכת מרחוק")
            )
            return

        if cfg.token_required_for_writes and scope.get("method") in _MUTATING_METHODS:
            token_header = headers.get("x-eoa-token")
            try:
                _require_token(token_header)
            except APIError as exc:
                await self._send_json(
                    send, exc.status_code, _error_body(exc.code, exc.message_he)
                )
                return

        await self._call_with_security_headers(scope, receive, send, authenticated=True)

    async def _call_with_security_headers(
        self, scope: dict, receive: Any, send: Any, *, authenticated: bool = False
    ) -> None:
        is_api = scope["path"].startswith("/api")

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                extra = list(_SECURITY_HEADERS)
                if is_api:
                    extra.append((b"cache-control", b"no-store"))
                if authenticated:
                    extra.append((REMOTE_SESSION_HEADER, b"1"))
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        await self.app(scope, receive, send_wrapper)

    @staticmethod
    async def _send_json(send: Any, status_code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]
        headers.extend(_SECURITY_HEADERS)
        await send({"type": "http.response.start", "status": status_code, "headers": headers})
        await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    passcode: str


def _request_effective_host(request: Request) -> str:
    peer_host = request.client.host if request.client else None
    return effective_client_host(peer_host, request.headers.get("x-forwarded-for")) or "unknown"


@router.post("/auth/login")
def login(request: Request, response: Response, body: LoginRequest) -> dict:
    cfg = settings().api.remote_access
    if not cfg.enabled:
        raise APIError(404, "not_found", "גישה מרחוק אינה מופעלת")

    host = _request_effective_host(request)
    if not check_rate_limit(host):
        raise APIError(
            429,
            "rate_limited",
            "יותר מדי ניסיונות התחברות — נסה שוב בעוד כמה דקות",
        )

    passcode_hash = effective_passcode_hash()
    if not passcode_hash or not verify_passcode(body.passcode, passcode_hash):
        record_failed_attempt(host)
        log.warning("auth.login_failed", host=host)
        raise APIError(401, "invalid_passcode", "קוד גישה שגוי")

    reset_rate_limit_for(host)
    token, expires_at = create_session()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=session_ttl_seconds(),
        # `expires` must be a `datetime` (Starlette formats it as an HTTP-date itself) or omitted
        # entirely -- NOT an int: `http.cookies.Morsel` treats an integer `expires` as a *delta in
        # seconds from now* (the same semantics as `max_age`), not as a Unix timestamp. Passing
        # `expires_at.timestamp()` (~1.7e9) here previously produced a cookie that expired in
        # 2083, ~55 years later than the actual 12h TTL `max_age` already enforces correctly.
        expires=expires_at,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    log.info("auth.login_ok", host=host)
    return {"ok": True}


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    destroy_session(token)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}
