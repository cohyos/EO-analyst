"""FR-12.4: conference reminder due-dates and deduped ntfy dispatch.

Three reminder kinds, matching spec 12.4 exactly:
  - ``registration_opens`` — the day ``registration_opens`` falls on, for relevance >= 4 only.
  - ``early_bird`` / ``cfp`` — 14 days before ``early_bird_deadline`` / ``cfp_deadline`` (any relevance).
  - ``major_conference`` — 30 days before ``start_date``, for relevance == 5 only.

Dedup is via the ``conference_reminders(conf_id, kind, sent_at)`` table (migration 0003): a
``(conf_id, kind)`` pair fires at most once per conference occurrence (each year's row is a
distinct ``conf_id``, so the same kind fires again for next year's instance).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import structlog

from eoa.db import connection
from eoa.notify import ntfy

log = structlog.get_logger(__name__)

_KIND_TITLES_HE = {
    "registration_opens": "נפתחה הרשמה",
    "early_bird": "עוד שבועיים — Early Bird",
    "cfp": "עוד שבועיים — Call for Papers",
    "major_conference": "כנס מרכזי בעוד חודש",
}


def _fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


def _fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _execute(query: str, params: Any = None) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)


def _as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def due_reminders(
    today: dt.date, rows: list[dict[str, Any]] | None = None
) -> list[tuple[dict[str, Any], str]]:
    """``(conference_row, kind)`` pairs whose reminder should fire on ``today``.

    ``rows`` is injectable for tests (a plain list of conference-like dicts); when omitted, all
    non-terminal conferences are read from the DB.
    """
    if rows is None:
        rows = _fetchall("SELECT * FROM conferences WHERE status NOT IN ('cancelled', 'past')")

    two_weeks_out = today + dt.timedelta(days=14)
    one_month_out = today + dt.timedelta(days=30)

    due: list[tuple[dict[str, Any], str]] = []
    for row in rows:
        relevance = row.get("relevance") or 0
        if relevance >= 4 and _as_date(row.get("registration_opens")) == today:
            due.append((row, "registration_opens"))
        if _as_date(row.get("early_bird_deadline")) == two_weeks_out:
            due.append((row, "early_bird"))
        if _as_date(row.get("cfp_deadline")) == two_weeks_out:
            due.append((row, "cfp"))
        if relevance == 5 and _as_date(row.get("start_date")) == one_month_out:
            due.append((row, "major_conference"))
    return due


def _notify_for(row: dict[str, Any], kind: str) -> None:
    title = f"{_KIND_TITLES_HE.get(kind, kind)}: {row.get('name')}"
    lines = [f"עיר: {row.get('city') or 'לא צוין'}"]
    reg_url = row.get("registration_url")
    if reg_url:
        lines.append(f"רישום: {reg_url}")
    priority = "high" if row.get("relevance") == 5 else "default"
    ntfy.send(title[:100], "\n".join(lines), priority=priority, tags=["calendar"], click=reg_url)


def send_reminders(today: dt.date | None = None) -> dict[str, Any]:
    """FR-12.4: compute due reminders and send the ones not already recorded in ``conference_reminders``."""
    today = today or dt.date.today()
    due = due_reminders(today)
    sent, skipped = 0, 0
    for row, kind in due:
        conf_id = row.get("id")
        already = _fetchone(
            "SELECT 1 FROM conference_reminders WHERE conf_id = %s AND kind = %s", (conf_id, kind)
        )
        if already:
            skipped += 1
            continue
        _notify_for(row, kind)
        _execute(
            "INSERT INTO conference_reminders (conf_id, kind, sent_at) VALUES (%s, %s, now()) "
            "ON CONFLICT (conf_id, kind) DO NOTHING",
            (conf_id, kind),
        )
        sent += 1
    log.info("conference_reminders_sent", due=len(due), sent=sent, skipped_already_sent=skipped)
    return {"due": len(due), "sent": sent, "skipped_already_sent": skipped}
