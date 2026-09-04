"""ntfy notifications (self-hosted first, public topic as fallback) and the 5-minute clarification gate."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from eoa.config import settings

log = structlog.get_logger(__name__)

PRIORITY = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}


@dataclass
class Sent:
    ok: bool
    message_id: str | None
    url: str


def _targets() -> list[tuple[str, str]]:
    n = settings().notify
    t = [(os.environ.get("NTFY_URL", n.url).rstrip("/"), n.topic)]
    if n.public_fallback_url and n.public_fallback_topic:
        t.append((n.public_fallback_url.rstrip("/"), n.public_fallback_topic))
    return t


def send(
    title: str,
    body: str,
    *,
    priority: str = "default",
    tags: list[str] | None = None,
    click: str | None = None,
    actions: list[dict[str, Any]] | None = None,
    to_public_fallback: bool = True,
) -> Sent:
    """Publish a message. Tries the self-hosted server first, then the public fallback topic."""
    headers = {"Title": title, "Priority": str(PRIORITY.get(priority, 3)), "Tags": ",".join(tags or [])}
    if click:
        headers["Click"] = click
    if actions:
        headers["Actions"] = "; ".join(_fmt_action(a) for a in actions)
    last_url = ""
    for i, (base, topic) in enumerate(_targets()):
        if i == 1 and not to_public_fallback:
            break
        url = f"{base}/{topic}"
        last_url = url
        try:
            r = httpx.post(
                url,
                content=body.encode("utf-8"),
                headers={**headers, "Content-Type": "text/plain; charset=utf-8"},
                timeout=10,
            )
            if r.status_code < 300:
                mid = (
                    r.json().get("id")
                    if r.headers.get("content-type", "").startswith("application/json")
                    else None
                )
                log.info("ntfy_sent", url=url, title=title[:60])
                return Sent(True, mid, url)  # mirroring to the public topic is done by eoa.notify.relay (fetcher)
            log.warning("ntfy_http_error", url=url, status=r.status_code)
        except Exception as exc:
            log.warning("ntfy_failed", url=url, error=str(exc)[:120])
    return Sent(False, None, last_url)


def _fmt_action(a: dict[str, Any]) -> str:
    """ntfy action header: `view, Label, url` | `http, Label, url, method=POST, body=...`."""
    kind = a.get("kind", "view")
    if kind == "http":
        return f"http, {a['label']}, {a['url']}, method={a.get('method', 'POST')}, body={a.get('body', '')}"
    return f"view, {a['label']}, {a['url']}"


# ------------------------------------------------------------------ typed helpers
def report_ready(kind: str, path_docx: str, headlines: list[str], ui_url: str | None = None) -> Sent:
    body = "\n".join(f"• {h}" for h in headlines[:3]) or "אין כותרות בולטות."
    return send(
        f"דוח {kind} מוכן",
        f"{body}\n\nקובץ: {path_docx}",
        priority="default",
        tags=["page_facing_up"],
        click=ui_url,
    )


def red_alert(title: str, summary_he: str, url: str) -> Sent:
    return send(
        f"🔴 {title[:70]}", f"{summary_he}\n{url}", priority="high", tags=["rotating_light"], click=url
    )


def security_alert(source: str, kind: str, excerpt: str) -> Sent:
    return send(
        "⚠️ הזרקה זוהתה", f"מקור: {source}\nסוג: {kind}\n{excerpt[:200]}", priority="high", tags=["shield"]
    )


def failure(stage: str, error: str) -> Sent:
    return send(f"❌ כשל בשלב {stage}", error[:400], priority="high", tags=["x"])


def status(text: str, priority: str = "low") -> Sent:
    return send("EO-analyst", text, priority=priority, tags=["satellite"])


# ------------------------------------------------------------------ clarification gate (FR-10)
def ask_user(
    question: str, options: list[str], *, timeout_min: int | None = None, kind: str = "clarification"
) -> str | None:
    """Ask a closed question on the channel and poll for a reply that starts with an option (or its number).

    Returns the chosen option, or None on timeout (caller proceeds with its default and marks ⚠️ הנחת עבודה).
    Persists to `clarifications` when the DB is available.
    """
    n = settings().notify
    timeout_min = timeout_min or n.clarification_timeout_min
    numbered = "\n".join(f"{i + 1}. {o}" for i, o in enumerate(options))
    since = int(time.time())
    asked_at = datetime.now(tz=UTC)
    send(
        f"❓ {question[:80]}",
        f"{question}\n{numbered}\n(ענה במספר או בטקסט; ברירת מחדל בעוד {timeout_min} דק': {options[0]})",
        priority="high",
        tags=["question"],
    )
    cid = _persist_question(kind, question, options, asked_at, timeout_min)

    deadline = time.time() + timeout_min * 60
    answer: str | None = None
    while time.time() < deadline and answer is None:
        time.sleep(15)
        for base, topic in _targets():
            try:
                r = httpx.get(f"{base}/{topic}/json", params={"poll": 1, "since": since}, timeout=10)
                for line in r.text.splitlines():
                    if not line.strip():
                        continue
                    import json

                    msg = json.loads(line)
                    if msg.get("event") != "message" or str(msg.get("title", "")).startswith(("❓", "[ORCH")):
                        continue
                    answer = _match(msg.get("message", ""), options)
                    if answer:
                        break
            except Exception as exc:
                log.debug("ntfy_poll_failed", error=str(exc)[:100])
            if answer:
                break
    _persist_answer(cid, answer)
    log.info("clarification_result", question=question[:60], answer=answer)
    return answer


def _match(text: str, options: list[str]) -> str | None:
    t = text.strip().lower()
    if not t:
        return None
    for i, o in enumerate(options):
        if t == str(i + 1) or t.startswith(f"{i + 1} ") or t.startswith(f"{i + 1}."):
            return o
    for o in options:
        if o.lower() in t or t in o.lower():
            return o
    return None


def _persist_question(
    kind: str, q: str, options: list[str], asked_at: datetime, timeout_min: int
) -> int | None:
    try:
        from eoa.db import connection

        with connection() as conn:
            row = conn.execute(
                "INSERT INTO clarifications(kind, question, options, asked_at, timeout_at) "
                "VALUES (%s,%s,%s::jsonb,%s, %s + make_interval(mins => %s)) RETURNING id",
                (
                    kind,
                    q,
                    __import__("json").dumps(options, ensure_ascii=False),
                    asked_at,
                    asked_at,
                    timeout_min,
                ),
            ).fetchone()
            return int(row["id"]) if row else None
    except Exception:
        return None


def _persist_answer(cid: int | None, answer: str | None) -> None:
    if cid is None:
        return
    try:
        from eoa.db import connection

        with connection() as conn:
            conn.execute(
                "UPDATE clarifications SET answer=%s, answered_at=CASE WHEN %s IS NULL THEN NULL ELSE now() END, "
                "assumed=%s WHERE id=%s",
                (answer, answer, answer is None, cid),
            )
    except Exception:
        pass
