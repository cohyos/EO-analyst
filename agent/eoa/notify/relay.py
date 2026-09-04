"""Relay self-hosted ntfy messages to the public fallback topic.

Runs inside the `fetcher` container (the only service with egress besides SearXNG). The isolated
`agent`/`web` containers publish to the self-hosted ntfy only; while ``notify.mirror_to_public`` is true,
this relay copies every message to the public topic so the phone gets them before it is subscribed to the
Tailscale URL. Bodies are already headline-level (no report content), see plan §2.4.
"""

from __future__ import annotations

import json
import threading
import time

import httpx
import structlog

from eoa.config import settings

log = structlog.get_logger(__name__)


def _relay_once(base: str, topic: str, pub_base: str, pub_topic: str, since: str) -> str:
    r = httpx.get(f"{base}/{topic}/json", params={"poll": 1, "since": since}, timeout=15)
    r.raise_for_status()
    last = since
    for line in r.text.splitlines():
        if not line.strip():
            continue
        msg = json.loads(line)
        if msg.get("event") != "message":
            continue
        last = str(msg.get("id") or last)
        headers = {
            "Title": msg.get("title") or "EO-analyst",
            "Priority": str(msg.get("priority") or 3),
            "Tags": ",".join(msg.get("tags") or []),
            "Content-Type": "text/plain; charset=utf-8",
        }
        if msg.get("click"):
            headers["Click"] = msg["click"]
        httpx.post(
            f"{pub_base}/{pub_topic}",
            content=(msg.get("message") or "").encode("utf-8"),
            headers=headers,
            timeout=15,
        )
        log.info("ntfy_relayed", title=(msg.get("title") or "")[:60])
    return last


def run_relay(stop: threading.Event | None = None, interval_s: int = 20) -> None:
    """Poll the internal topic and republish new messages to the public topic until ``stop`` is set."""
    n = settings().notify
    if not (n.mirror_to_public and n.public_fallback_url and n.public_fallback_topic):
        log.info("ntfy_relay_disabled")
        return
    base, topic = n.url.rstrip("/"), n.topic
    pub_base, pub_topic = n.public_fallback_url.rstrip("/"), n.public_fallback_topic
    since = str(int(time.time()))  # only messages from now on
    log.info("ntfy_relay_start", internal=f"{base}/{topic}", public=f"{pub_base}/{pub_topic}")
    while stop is None or not stop.is_set():
        try:
            since = _relay_once(base, topic, pub_base, pub_topic, since)
        except Exception as exc:
            log.debug("ntfy_relay_poll_failed", error=str(exc)[:120])
        time.sleep(interval_s)


def start_relay_thread() -> threading.Thread:
    """Start the relay as a daemon thread (used by the fetcher service)."""
    t = threading.Thread(target=run_relay, name="ntfy-relay", daemon=True)
    t.start()
    return t
