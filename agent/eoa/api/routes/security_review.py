"""`GET /api/security-reviews`, `POST /api/security-reviews/{job_id}/approve|dismiss` -- W10
(docs/REVIEW_2026-09-06_evening.md round 4): when the L2 prompt-injection guard (`eoa.security`)
partially blocks a deep-search answer, the operator needs a queue of "pending security review"
investigations, and two actions -- "אשר והמשך" (re-run the investigation as a fresh, independent
job -- see F28 below, this is an honest re-run, not a whitelist/override) and "דחה" (mark reviewed,
no re-run).

Self-contained (queries the DB directly, mirrors `eoa.api.routes.payloads`) rather than adding to
`eoa.api.services` -- that module is being edited concurrently by another engineer this round (see
the task brief), so this stays a new file the way `docs/API.md`'s "add a new route module for a new
read-only/write endpoint" guidance describes. `enqueue_job` is imported read-only from
`eoa.memory.relational` (not modified here), mirroring `eoa.api.services.expand_investigation`'s own
"copy payload + enqueue" shape for the "אשר והמשך" re-run.

The `security_review`/`security_flag_reason`/`security_flag_snippet` fields on a `deep_search`
job's `result` (`eoa.llm.schemas.analysis`, set by `eoa.search.deep_search`) are read as a safe
`->>'security_review'` JSON lookup, which is `NULL` (not an error) on a job with no such key -- so
`list_pending_security_reviews` degrades to `[]` rather than erroring if this ever runs against an
older job row that predates the fields.

F28/F29 (docs/qa/content_review/SOL-AUDIT-2026-09-24.md): approve/dismiss now require the target
job to be a currently unresolved, flagged review, claimed atomically in one `UPDATE ... RETURNING`
(`_claim_pending_review`) rather than a separate check-then-write pair -- see that function's own
docstring. `_review_card` also now reads the fields' real persisted names
(`security_flag_reason`/`security_flag_snippet`, not `security_review_reason_he`/
`security_review_snippet`, which never existed on any job's `result`).
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
        # F29 (SOL-AUDIT-2026-09-24.md): the deep-search result schema
        # (`eoa.llm.schemas.analysis` / `eoa.search.deep_search`) actually persists these as
        # `security_flag_reason`/`security_flag_snippet` -- the names this route read before
        # (`security_review_reason_he`/`security_review_snippet`) never existed on any job's
        # `result`, so every review card silently rendered with an empty reason and snippet.
        "reason_he": result.get("security_flag_reason"),
        "snippet": result.get("security_flag_snippet"),
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


def _claim_pending_review(job_id: int, *, dismissed: bool, conn: Any = None) -> dict[str, Any] | None:
    """Atomically resolve `job_id` iff it is *currently* an unresolved, flagged security review --
    the single check-and-act statement both endpoints below rely on (F28, SOL-AUDIT-2026-09-24.md).

    The previous version fetched the job with a plain `SELECT`, then wrote the resolved patch in a
    separate statement -- both a TOCTOU race (two concurrent approvals on the same job could each
    pass the `SELECT` before either write lands, enqueuing two re-run jobs) and no check at all
    that the job was ever actually a *pending, flagged* review (any `deep_search` job id would do,
    including one already resolved). Folding the WHERE into the UPDATE closes both: only one caller
    can ever match and flip `security_review_resolved`, and a job that isn't presently pending never
    matches at all.

    N08 (SOL-REVIEW-2026-09-24): an optional caller-owned ``conn`` runs the UPDATE on that
    connection/transaction instead of opening (and committing) a new one -- see
    :func:`approve_security_review` for why: this claim and the new job's `enqueue_job` must
    commit or roll back together.
    """
    patch = {"security_review_resolved": True, "security_review_dismissed": dismissed}
    query = """
        UPDATE jobs
        SET result = COALESCE(result, '{}'::jsonb) || %s::jsonb
        WHERE id = %s
          AND kind = 'deep_search'
          AND result ->> 'security_review' = 'true'
          AND COALESCE((result ->> 'security_review_resolved')::boolean, false) = false
        RETURNING *
        """
    params = (Json(patch), job_id)
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()
    return _fetchone(query, params)


@router.post("/security-reviews/{job_id}/approve")
def approve_security_review(job_id: int) -> dict[str, Any]:
    """ "אשר והמשך": re-runs the investigation from scratch as a brand-new, independent
    `deep_search` job (mirrors `eoa.api.services.expand_investigation`'s "copy payload + enqueue"
    re-run shape), then atomically marks the original resolved so it drops off the pending list.

    F28 (SOL-AUDIT-2026-09-24.md): this used to set `security_override: true` on the new job's
    payload, but nothing anywhere in the codebase (worker, search pipeline, or security guard) ever
    reads that field -- it was a purely cosmetic flag that gave the operator the false impression
    that "אשר והמשך" whitelists the previously-flagged content. There is no such override
    implemented, so this no longer claims one: the re-run is an honest, ordinary duplicate
    investigation that goes through `eoa.security`'s screening again, independently, like any other
    job. If a real reviewed-content override is wanted later, it needs a narrowly-scoped consumer
    (e.g. the search/guard layer checking a specific claim id against this resolved review row) --
    not a same-named-but-unread payload field.

    N08 (SOL-REVIEW-2026-09-24): the claim (above) and the new job's `enqueue_job` insert used to
    be two separate statements/transactions -- if `enqueue_job` raised (a DB hiccup, a constraint
    violation) after the claim had already committed, the review was left permanently resolved
    with no re-run ever created: an approval that silently disappears. Both now run inside ONE
    `db.connection()` transaction (commits together on success, rolls back both on any failure) --
    `_claim_pending_review`'s own `conn` parameter and `enqueue_job`'s new `conn` parameter both
    exist for exactly this.
    """
    from eoa.pipeline.investigation_context import ensure_context_he

    with connection() as conn:
        job = _claim_pending_review(job_id, dismissed=False, conn=conn)
        if job is None:
            raise not_found("החקירה לא נמצאה או שאינה ממתינה לבדיקת אבטחה")
        payload = dict(job.get("payload") or {})
        payload["expanded_from_job_id"] = job_id
        refreshed = ensure_context_he(payload)
        if refreshed:
            payload["context_he"] = refreshed
        new_job_id = enqueue_job("deep_search", payload, priority=0, conn=conn)
    return {"job_id": new_job_id}


@router.post("/security-reviews/{job_id}/dismiss")
def dismiss_security_review(job_id: int) -> dict[str, Any]:
    """ "דחה": marks the flagged investigation reviewed with no re-run. Same atomic
    unresolved-and-flagged claim as `approve_security_review` (F28) -- a second dismiss/approve on
    an already-resolved job, or a job that was never flagged in the first place, is a 404, not a
    silent no-op."""
    job = _claim_pending_review(job_id, dismissed=True)
    if job is None:
        raise not_found("החקירה לא נמצאה או שאינה ממתינה לבדיקת אבטחה")
    return {"ok": True}
