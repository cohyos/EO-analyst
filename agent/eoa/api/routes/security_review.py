"""`GET /api/security-reviews`, `POST /api/security-reviews/{job_id}/approve|dismiss` -- W10
(docs/REVIEW_2026-09-06_evening.md round 4): when the L2 prompt-injection guard (`eoa.security`)
partially blocks a deep-search answer, the operator needs a queue of "pending security review"
investigations, and two actions -- "אשר והמשך" (re-run with the flagged snippet whitelisted) and
"דחה" (mark reviewed, no re-run).

Self-contained (queries the DB directly, mirrors `eoa.api.routes.payloads`) rather than adding to
`eoa.api.services` -- that module is being edited concurrently by another engineer this round (see
the task brief), so this stays a new file the way `docs/API.md`'s "add a new route module for a new
read-only/write endpoint" guidance describes. `enqueue_job` is imported read-only from
`eoa.memory.relational` (not modified here), mirroring `eoa.api.services.expand_investigation`'s own
"copy payload + add a field + enqueue" shape for the "אשר והמשך" re-run.

The `security_review`/`security_review_reason_he`/`security_review_snippet` fields on a
`deep_search` job's `result` are being added by the deep-search engineer working the same round
(see the review doc) -- until they land, `jobs.result` simply never has `security_review: true` set
on it, so `list_pending_security_reviews` below always returns `[]` and the UI's own "robust when
absent" empty state is what actually shows. Nothing here assumes the fields exist beyond a safe
`->>'security_review'` JSON lookup, which is `NULL` (not an error) on a job with no such key.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter
from psycopg.types.json import Json

from eoa.api.errors import not_found
from eoa.db import connection
from eoa.memory.relational import enqueue_job

log = structlog.get_logger(__name__)

router = APIRouter(tags=["security_review"])


def _fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


def _execute(query: str, params: Any = None) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)


def _review_card(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload") or {}
    result = row.get("result") or {}
    return {
        "job_id": row["id"],
        "item_id": payload.get("item_id"),
        "question": payload.get("question"),
        "item_title": payload.get("item_title"),
        "reason_he": result.get("security_review_reason_he"),
        "snippet": result.get("security_review_snippet"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
    }


@router.get("/security-reviews")
def list_pending_security_reviews() -> list[dict[str, Any]]:
    """Every `deep_search` job whose result was flagged `security_review: true` and not yet
    resolved (approved or dismissed) via the two endpoints below -- for the inbox's "בדיקות אבטחה
    ממתינות" section. Returns `[]`, not an error, until the deep-search engineer's fields exist."""
    rows = _fetchall(
        """
        SELECT * FROM jobs
        WHERE kind = 'deep_search'
          AND result ->> 'security_review' = 'true'
          AND COALESCE((result ->> 'security_review_resolved')::boolean, false) = false
        ORDER BY started_at DESC NULLS LAST
        LIMIT 100
        """
    )
    return [_review_card(r) for r in rows]


def _mark_resolved(job_id: int, *, dismissed: bool) -> bool:
    row = _fetchone("SELECT id FROM jobs WHERE id = %s AND kind = 'deep_search'", (job_id,))
    if row is None:
        return False
    patch = {"security_review_resolved": True, "security_review_dismissed": dismissed}
    _execute(
        "UPDATE jobs SET result = COALESCE(result, '{}'::jsonb) || %s::jsonb WHERE id = %s",
        (Json(patch), job_id),
    )
    return True


@router.post("/security-reviews/{job_id}/approve")
def approve_security_review(job_id: int) -> dict[str, Any]:
    """ "אשר והמשך": re-runs the investigation with the flagged snippet whitelisted -- enqueues a
    fresh `deep_search` job carrying the original payload plus `security_override: true` and
    `expanded_from_job_id` (mirrors `eoa.api.services.expand_investigation`'s re-run shape), then
    marks the original resolved so it drops off the pending list."""
    job = _fetchone("SELECT * FROM jobs WHERE id = %s AND kind = 'deep_search'", (job_id,))
    if job is None:
        raise not_found("החקירה לא נמצאה")
    payload = dict(job.get("payload") or {})
    payload["security_override"] = True
    payload["expanded_from_job_id"] = job_id
    from eoa.pipeline.investigation_context import ensure_context_he

    refreshed = ensure_context_he(payload)
    if refreshed:
        payload["context_he"] = refreshed
    new_job_id = enqueue_job("deep_search", payload, priority=0)
    _mark_resolved(job_id, dismissed=False)
    return {"job_id": new_job_id}


@router.post("/security-reviews/{job_id}/dismiss")
def dismiss_security_review(job_id: int) -> dict[str, Any]:
    """ "דחה": marks the flagged investigation reviewed with no re-run."""
    ok = _mark_resolved(job_id, dismissed=True)
    if not ok:
        raise not_found("החקירה לא נמצאה")
    return {"ok": True}
