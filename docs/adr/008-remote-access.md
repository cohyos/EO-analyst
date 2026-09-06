# ADR-008: Remote access over Tailscale (passcode + session gate)

**Status:** accepted (2026-09-06)

## Context

The API (`agent/eoa/api/app.py`, `config/config.yaml`'s `api.host: 127.0.0.1`) has **no
authentication at all**. That was always fine: ADR-002/ADR-004 bound it to loopback, the user is
the only person who ever opens the web UI, and the app runs on a laptop next to them. Nothing in
`docs/CONVENTIONS.md` treats this as a gap because it isn't one -- for a single local user, a
network boundary *is* the access control.

The user now runs the machine continuously (`docs/adr/004-windows-native.md`'s native supervisor)
and has Tailscale installed, which makes it trivially easy to reach `127.0.0.1:8765` from a phone
or another machine on the same tailnet via `tailscale serve` (a reverse proxy that terminates
HTTPS on the tailnet side and forwards to a local port over plain HTTP from `127.0.0.1`). The
moment that happens, "loopback-only" stops being true, and the complete absence of authentication
stops being a non-issue. This ADR adds the minimum gate needed to close that specific new opening
without touching anything about how the app behaves for its original, primary use case.

## Threat model

**In scope (what this defends against):**
- A stranger who discovers or guesses the Tailscale Serve HTTPS URL (or a Tailscale peer of the
  user's account who shouldn't have access to this app specifically) reading or modifying analyst
  data.
- Credential-stuffing / brute-forcing the passcode.
- A phone or laptop's browser leaking the passcode (it never sees or stores one after login) or
  the session cookie to script/JS on another origin (`HttpOnly`, `Secure`, `SameSite=Strict`).

**Explicitly out of scope (not addressed by this ADR):**
- **No port forwarding, no Tailscale Funnel.** This design assumes the *only* way to reach the API
  from outside is through `tailscale serve`, which itself requires being an authenticated member
  of the user's own tailnet (Tailscale's own auth, including whatever 2FA the user's account has).
  A Funnel (public internet exposure) or a router port-forward would put this passcode gate
  directly on the open internet, where a 5-attempts/15-minutes rate limit is a far weaker
  defense than it is against a closed tailnet. **Do not enable Funnel or port-forward 8765/8091
  without redesigning this.**
- **Compromise of a device already on the tailnet.** Anyone who can reach `127.0.0.1` on the host
  itself (e.g. malware on the machine, another local user account) is trusted exactly as before --
  this ADR only gates *network* access, not local access, matching the existing threat model in
  `docs/adr/004-windows-native.md` §3-4.
- **The outbound-HTTP audit log gap** noted in ADR-004 is unrelated and still open.
- **Full defense against a malicious MCP/browser extension** on the client device reading the
  session cookie via a browser vulnerability -- `HttpOnly` stops JS access but not OS/browser-level
  compromise.
- Multi-user support, per-user accounts, or an audit trail of who did what -- there is exactly one
  shared passcode and one class of "remote session," matching this project's single-operator
  design everywhere else.

## Decision

### 1. Loopback stays exactly as it is today

`agent/eoa/api/auth.py::RemoteAccessMiddleware` (a raw ASGI middleware, wired into
`agent/eoa/api/app.py`) is the single new gate. It resolves an "effective client host" for every
request:

- the direct TCP peer (`scope["client"][0]`), **unless** that peer is itself loopback *and* the
  request carries `X-Forwarded-For` -- in which case the first address in that header is used
  instead. This one exception exists because `tailscale serve` always proxies to this API from
  `127.0.0.1` over plain HTTP, carrying the real tailnet peer in `X-Forwarded-For`; without this,
  every Tailscale-proxied request would look like a loopback request and the gate would never
  fire. `X-Forwarded-For` is **never** trusted from a non-loopback peer (that header is otherwise
  trivially spoofable by anyone who can already reach the API at all).
- if the effective host is loopback (`127.0.0.1`/`::1`), the request is trusted exactly as before
  this ADR -- no cookie, no passcode, nothing changes.
- any other effective host must present a valid session (`eoa_session` cookie) for every `/api/*`
  route and the `/ws/status` WebSocket handshake, or gets `401 {"error":{"code":"auth_required"}}`
  (WebSocket: closed with code `4401` before `accept()`).

The whole middleware is a no-op when `config/config.yaml`'s `api.remote_access.enabled` is `false`
(the default) -- nothing about existing behavior changes until an operator explicitly opts in.

### 2. Login: shared passcode -> short-lived server-side session

`POST /api/auth/login {"passcode": "..."}`:
- Rate-limited 5 attempts / 15 minutes per effective client host (in-memory sliding window).
- The passcode is compared against a hash computed once at process start from the
  `EOA_ACCESS_PASSCODE` environment variable (read from `runtime\eoa.env`, written by
  `scripts\native\remote_access.ps1 -SetPasscode`, never logged) -- or, if that variable is unset,
  a pre-hashed `api.remote_access.passcode_hash` value in `config/config.yaml`. Hashing is
  argon2 if `argon2-cffi` is installed, else `bcrypt` if installed, else PBKDF2-HMAC-SHA256 via
  the standard library (`hashlib`) -- always available, so the gate works even with neither
  optional dependency installed. The stored hash format is self-describing
  (`$argon2...`/`$2b$...`/`pbkdf2_sha256$...`), so which scheme produced it never needs to be
  tracked separately.
- On success: a random 32-byte token (`secrets.token_urlsafe(32)`) is stored server-side (in
  memory, 12h TTL, no persistence across a restart -- this is a single-process app, ADR-004) and
  set as the `eoa_session` cookie: `HttpOnly` (no JS access), `Secure` (browser will only ever send
  it over the HTTPS connection Tailscale Serve terminates), `SameSite=Strict` (never sent
  cross-site), `Max-Age=43200` (12h), `Path=/`.
- `POST /api/auth/logout` destroys the server-side session and clears the cookie.

No passcode is ever stored client-side (not in `localStorage`, not in `sessionStorage`) -- the
browser only ever holds the opaque session cookie, exactly like any other cookie-based web login.

### 3. Writes: the existing per-route token, reused, applied globally for remote clients

`routes/settings.py` already has an independent, always-on check: if `EOA_API_TOKEN` is set, every
`PUT /api/settings/*` must carry a matching `X-EOA-Token` header (finding #17,
`output/reviews/codex_security_review.md`). That check is untouched. On top of it,
`RemoteAccessMiddleware` reuses the *same* `_require_token` function to gate **every** mutating
request (`POST`/`PUT`/`PATCH`/`DELETE`) from a non-loopback, already-authenticated client, when
`api.remote_access.token_required_for_writes` is true (default) and `EOA_API_TOKEN` is set --
extending that one route's belt-and-suspenders check to the whole API for remote traffic, without
duplicating its logic.

### 4. `/ws/status` honors the same session

Starlette's `BaseHTTPMiddleware` never sees WebSocket scopes, so a per-route check inside
`routes/status.py` would be the only alternative to a raw ASGI middleware -- `auth.py` uses the
raw-middleware form specifically so `routes/status.py` needs no changes at all. For a non-loopback,
unauthenticated WebSocket handshake, the middleware sends `{"type": "websocket.close", "code":
4401}` before the app ever calls `accept()`, which Starlette's own `TestClient` (and any real
client) surfaces as a clean handshake rejection.

### 5. Frontend: a full-page gate, not a modal over broken content

`web/src/components/auth/AccessGate.tsx` renders instead of the whole app whenever `real.ts`'s
`request()` sees a `401 auth_required` response, exactly the response every `/api/*` call gets
from a non-loopback, logged-out client. `index.html`/the JS bundle itself is still served (static
assets are exempt from the gate) so the gate has something to render into; the app underneath is
not mounted while the gate is up, so an unauthenticated client doesn't spray a page's worth of
components at 401 responses. A successful login clears the flag and invalidates every React Query
cache entry so the app reloads with real data immediately. `TopBar.tsx` shows a small lock icon +
"התנתק"/"Log out" only when the *last* response carried the `X-EOA-Remote-Session: 1` header
(set by the middleware only for an authenticated, non-loopback response) -- ordinary local usage
never sees it.

### 6. Operator workflow

`scripts\native\remote_access.ps1` (`docs\RUNBOOK.md`'s "Remote Access" section,
`docs\USER_GUIDE_HE.md`'s "גישה מרחוק" section):
- `-SetPasscode`: prompts securely, writes `EOA_ACCESS_PASSCODE` into `runtime\eoa.env`.
- `-Enable` / `-Disable` / `-Status`: manage the `tailscale serve` mapping itself and print the
  resulting `https://` URL; `-Enable` prints a loud warning if `api.remote_access.enabled` still
  reads `false` in `config/config.yaml` (this script does not flip that flag for you -- see below).
- `-NoSleep`: `powercfg /change standby-timeout-ac 0`, so the share doesn't quietly stop answering
  because the laptop slept.

**Why the script doesn't flip `api.remote_access.enabled` itself:** the config flag is the actual
security boundary (the passcode gate is a no-op without it); a script that silently turns it on as
a side effect of an unrelated action (setting a passcode, or toggling the Tailscale-layer proxy)
risks the operator not realizing the gate is live, or -- worse -- not realizing it *isn't*. Editing
`config.yaml`'s `enabled: true`/`false` line is a one-line, deliberate, visible edit; the script
only ever reads it back to warn.

## Consequences

- **Positive:** the primary, original use case (local browser, `http://127.0.0.1:8765`) is
  provably unaffected -- the middleware's very first branch is the loopback check, and it's a
  global no-op while `enabled: false`. Remote access requires an explicit opt-in at three
  independent layers (Tailscale account membership, `tailscale serve` running, `config.yaml`'s
  flag), so no single mistake exposes the app with zero authentication.
- **Negative:** one more moving part (`runtime\eoa.env`'s `EOA_ACCESS_PASSCODE`,
  `config.yaml`'s `remote_access` block) to keep straight; a stale `tailscale serve` mapping left
  running after `api.remote_access.enabled` is flipped back to `false` would leave the API fully
  open to the tailnet again (mitigated by `-Status` surfacing both states together, and by
  `-Enable`'s warning).
- **Neutral:** sessions are in-memory and single-process (ADR-004) -- a supervisor-triggered API
  restart logs every remote session out; this matches how `EOA_API_TOKEN`/rate-limit state already
  behaves today and was judged acceptable for a personal, single-operator tool.
- **Follow-up:** none of this defends against Funnel/port-forward exposure (see Threat model) --
  if that's ever wanted, this ADR needs revisiting first, not just a `tailscale funnel` command.
