"""R02 (SOL-REVIEW3-2026-09-24 blocker 3): bounded, scheduled retries for failed notification
deliveries.

``eoa.orchestrator.jobs._notify`` claims a ``(kind, key)`` row via ``eoa.memory.relational.
claim_notification_pending``, attempts delivery, and records the outcome via
``eoa.memory.relational.mark_notification_result``. A ``Sent(ok=False)`` (HTTP error, timeout,
unreachable server -- ``ntfy.send`` never raises for these) correctly lands the row on ``failed``
and flags the run ``partial`` -- but nothing before this module ever revisited that row: the job
worker only ever claims ``queued``/``deferred`` rows from the ``jobs`` table, and a failed
notification is not a job, just a row in ``notifications_sent``. This module is the missing "retry
the failed delivery": :func:`retry_failed_notifications` runs on its own scheduler interval (see
``eoa.orchestrator.main.build_scheduler``'s ``notification_retry`` job -- in-process, not another
``jobs`` row), selects ``failed`` rows that are due and still under ``NOTIFICATION_MAX_ATTEMPTS``,
and resends the exact payload ``_notify`` stored via ``eoa.notify.ntfy.build_report_ready``/
``build_failure`` (migration 0039's ``notifications_sent.payload``).

Claiming through :func:`~eoa.memory.relational.claim_notification_pending` -- the SAME function
``_notify`` itself uses -- before resending is what makes this safe to run concurrently with a job
replay of the same notification (e.g. the ``daily_run`` job that originally failed to notify gets
reaped and reruns its own ``notify`` stage while this sweep is also considering the same row):
whichever caller's ``INSERT ... ON CONFLICT ... DO UPDATE ... WHERE ...`` commits first wins the
row (``pending``, ``attempts`` incremented); the other sees the row is no longer ``failed`` (or no
longer stale-``pending``) and its claim is a no-op, so it skips."""

from __future__ import annotations

from typing import Any

import structlog

from eoa.memory.relational import (
    NOTIFICATION_MAX_ATTEMPTS,
    claim_notification_pending,
    mark_notification_result,
)
from eoa.notify import ntfy

log = structlog.get_logger(__name__)


def _due_failed_notifications(limit: int) -> list[dict[str, Any]]:
    """Rows eligible for a retry attempt right now: ``failed``, under the attempt cap, with a
    resendable ``payload`` (legacy rows recorded before migration 0039 have none and are skipped
    here -- nothing to resend), and either never scheduled or due."""
    from psycopg.rows import dict_row

    from eoa.db import connection

    query = """
        SELECT kind, key, payload
        FROM notifications_sent
        WHERE status = 'failed'
          AND attempts < %(max_attempts)s
          AND payload IS NOT NULL
          AND (next_attempt_at IS NULL OR next_attempt_at <= now())
        ORDER BY next_attempt_at ASC NULLS FIRST
        LIMIT %(limit)s
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"max_attempts": NOTIFICATION_MAX_ATTEMPTS, "limit": limit})
        return list(cur.fetchall())


def retry_failed_notifications(limit: int = 10) -> dict[str, int]:
    """Resend up to ``limit`` due, retryable ``failed`` notifications; returns counts for logging/
    tests. Never raises -- a bad row (resend exception, a claim lost to a concurrent replay, a
    malformed payload) is caught and counted, not left to crash the whole sweep, so one broken row
    can never wedge the interval job (the scheduler registration in ``eoa.orchestrator.main`` also
    wraps this call in its own try/except, as defense in depth, not as the only guard)."""
    counts = {"considered": 0, "sent": 0, "failed": 0, "skipped": 0}
    try:
        rows = _due_failed_notifications(limit)
    except Exception as exc:
        log.warning("notification_retry_query_failed", error=str(exc)[:200])
        return counts

    for row in rows:
        kind, key, payload = row["kind"], row["key"], row["payload"]
        counts["considered"] += 1
        if not claim_notification_pending(kind, key):
            # Lost the race (a job replay or another sweep tick claimed it first) or no longer
            # eligible by the time we got here (already sent / attempts exhausted meanwhile).
            counts["skipped"] += 1
            continue
        try:
            sent = ntfy.send(**payload)
            mark_notification_result(kind, key, ok=sent.ok, payload=payload)
            counts["sent" if sent.ok else "failed"] += 1
        except Exception as exc:
            log.warning("notification_retry_send_failed", kind=kind, key=key, error=str(exc)[:200])
            mark_notification_result(kind, key, ok=False, payload=payload)
            counts["failed"] += 1

    if counts["considered"]:
        log.info("notification_retry_swept", **counts)
    return counts
