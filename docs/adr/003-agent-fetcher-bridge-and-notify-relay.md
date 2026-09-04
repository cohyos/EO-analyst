# ADR-003: The isolated agent never touches the internet — jobs-table bridge to the fetcher, ntfy relay

**Status:** accepted (2026-09-04)

## Context
ADR-002 gives the `agent` container no route to the internet (verified: DNS 0.0.0.0, `ConnectError` on
https://example.com). But the daily cycle needs (a) RSS/HTML ingestion, (b) page reads during deep search, and
(c) notifications that reach the user's phone even before it is subscribed to the self-hosted ntfy server.

## Decision
1. **Jobs-table bridge** (`agent/eoa/fetch/remote.py`): the agent enqueues `ingest` and `fetch_url` jobs; the
   `fetcher` container (egress network) claims them (`FOR UPDATE SKIP LOCKED`), fetches + sanitizes, and writes the
   result to `jobs.result`. The agent polls the row (1–5 s). `EOA_ROLE` selects the path: `agent` → bridge; host/dev →
   in-process. Measured round trip for one page: ~3 s.
2. **Security stays on the agent side**: the fetcher returns sanitized text + sanitizer signals; the agent runs the
   prompt-injection gate (`security.guard.screen`) before any model sees the text.
3. **ntfy relay** (`agent/eoa/notify/relay.py`, thread inside the fetcher): copies every message published on the
   self-hosted topic to the public fallback topic while `notify.mirror_to_public` is true. Agent/web publish only to
   the self-hosted server (they cannot reach ntfy.sh). Messages are headline-level only.

## Consequences
- One more DB round trip per page read; acceptable at ≤ 30 pages per investigation.
- The fetcher is the single egress point for content and the single relay for notifications; its logs are the
  audit trail for outbound traffic (`item.inserted`, `fetch_job_*`, `ntfy_relayed`).
- Turn `mirror_to_public` off once the phone is subscribed to `http://100.70.157.25:8090/eo-analyst`.
