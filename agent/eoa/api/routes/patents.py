"""`GET /api/patents`, `/api/patents/{pub_number}`, `/api/patents/heatmap`, `/api/patents/status`,
`/api/patents/surveys` -- A14 patent/IP landscape tracking (eoa.patents).

Self-contained (queries the DB directly rather than routing through ``eoa.api.services``, which
other work concurrently touches) -- mirrors the shape of ``eoa.api.services.build_or_enqueue_bd_report``
for the "sync if quick, else return a job id to poll" survey endpoint.
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from eoa.db import connection
from eoa.memory.relational import enqueue_job

log = structlog.get_logger(__name__)

router = APIRouter(tags=["patents"])

_SURVEY_SYNC_WAIT_SECONDS = 55.0
_SURVEY_POLL_INTERVAL_SECONDS = 1.0
MIN_SURVEY_TOPIC_LENGTH = 8


def _fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


@router.get("/patents/status")
def patents_status() -> dict[str, Any]:
    """Whether EPO OPS/PatentsView credentials are configured -- backs the UI's "search-only mode"
    banner (per the A14 spec's exact banner text)."""
    from eoa.patents.scan import structured_sources_configured

    configured = structured_sources_configured()
    return {
        "structured_sources_configured": configured,
        "banner_he": (
            None
            if configured
            else "מקורות פטנטים: מצב חיפוש בלבד — הזן EPO_OPS_KEY/PATENTSVIEW_API_KEY ב-.env לכיסוי מלא."
        ),
    }


@router.get("/patents")
def list_patents(
    assignee: str | None = Query(None),
    subdomain: str | None = Query(None),
    israeli: bool = Query(False, description="only patents with israel_relevance >= 0.5"),
    min_value_score: int | None = Query(None, ge=0, le=100),
    q: str | None = Query(None, description="free-text match against title/abstract"),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    where = ["1=1"]
    params: dict[str, Any] = {"limit": limit}
    if assignee:
        where.append("%(assignee)s = ANY(assignees)")
        params["assignee"] = assignee
    if subdomain:
        where.append("subdomain = %(subdomain)s")
        params["subdomain"] = subdomain
    if israeli:
        where.append("israel_relevance >= 0.5")
    if min_value_score is not None:
        where.append("value_score >= %(min_value_score)s")
        params["min_value_score"] = min_value_score
    if q:
        where.append("(title ILIKE %(q)s OR abstract ILIKE %(q)s)")
        params["q"] = f"%{q}%"
    rows = _fetchall(
        f"SELECT * FROM patents WHERE {' AND '.join(where)} "
        "ORDER BY value_score DESC NULLS LAST, publication_date DESC NULLS LAST, id DESC "
        "LIMIT %(limit)s",
        params,
    )
    total_row = _fetchone("SELECT count(*) AS c FROM patents")
    return {"patents": rows, "total": (total_row or {}).get("c", len(rows))}


@router.get("/patents/heatmap")
def patents_heatmap(
    top_cpc: int = Query(10, ge=1, le=30), top_assignees: int = Query(10, ge=1, le=30)
) -> dict[str, Any]:
    """CPC x assignee count matrix for the top ``top_cpc`` CPC codes and ``top_assignees``
    assignees currently tracked -- backs the UI's heat matrix."""
    cpc_rows = _fetchall(
        "SELECT c AS cpc, count(*) AS n FROM (SELECT unnest(cpc) AS c FROM patents) s "
        "WHERE c IS NOT NULL GROUP BY c ORDER BY n DESC LIMIT %(limit)s",
        {"limit": top_cpc},
    )
    assignee_rows = _fetchall(
        "SELECT a AS assignee, count(*) AS n FROM (SELECT unnest(assignees) AS a FROM patents) s "
        "WHERE a IS NOT NULL GROUP BY a ORDER BY n DESC LIMIT %(limit)s",
        {"limit": top_assignees},
    )
    cpc_codes = [r["cpc"] for r in cpc_rows]
    assignees = [r["assignee"] for r in assignee_rows]
    if not cpc_codes or not assignees:
        return {"cpc_codes": cpc_codes, "assignees": assignees, "cells": []}
    cell_rows = _fetchall(
        "SELECT c AS cpc, a AS assignee, count(*) AS n FROM "
        "(SELECT unnest(cpc) AS c, unnest(assignees) AS a FROM patents "
        " WHERE cpc && %(cpc_codes)s AND assignees && %(assignees)s) s "
        "WHERE c = ANY(%(cpc_codes)s) AND a = ANY(%(assignees)s) GROUP BY c, a",
        {"cpc_codes": cpc_codes, "assignees": assignees},
    )
    return {"cpc_codes": cpc_codes, "assignees": assignees, "cells": cell_rows}


@router.get("/patents/surveys")
def list_patent_surveys(limit: int = Query(30, ge=1, le=200)) -> list[dict[str, Any]]:
    rows = _fetchall(
        "SELECT s.id, s.topic, s.status, s.created_at, r.path_docx, r.path_md, r.path_html, r.id AS report_id "
        "FROM patent_surveys s LEFT JOIN reports r ON r.id = s.report_id "
        "ORDER BY s.created_at DESC LIMIT %(limit)s",
        {"limit": limit},
    )
    return rows


@router.get("/patents/{pub_number}")
def get_patent(pub_number: str) -> dict[str, Any]:
    row = _fetchone("SELECT * FROM patents WHERE pub_number = %(p)s", {"p": pub_number})
    if row is None:
        raise HTTPException(status_code=404, detail="הפטנט לא נמצא")
    return row


class PatentSurveyCreate(BaseModel):
    topic: str = Field(min_length=MIN_SURVEY_TOPIC_LENGTH)
    territory: str | None = Field(
        default=None,
        description=(
            "Optional territory filter (2026-09-06), e.g. 'US' -- restricts the gathered sample "
            "to that publication-number prefix, see eoa.patents.survey._territory_filter"
        ),
    )


@router.post("/patents/surveys")
def create_patent_survey(body: PatentSurveyCreate) -> dict[str, Any]:
    """Enqueue a ``patent_survey`` job (``eoa.orchestrator.jobs.HANDLERS["patent_survey"]``), then
    poll for up to ``_SURVEY_SYNC_WAIT_SECONDS`` -- returns ``{"survey": {...}, "job_id"}`` if the
    job finished in time, else ``{"job_id", "status": "queued"}`` for the client to poll
    ``GET /api/patents/surveys``."""
    topic = body.topic.strip()
    if len(topic) < MIN_SURVEY_TOPIC_LENGTH:
        raise HTTPException(status_code=422, detail="topic must be at least 8 characters")
    payload: dict[str, Any] = {"topic": topic}
    if body.territory and body.territory.strip():
        payload["territory"] = body.territory.strip()
    job_id = enqueue_job("patent_survey", payload, priority=4)
    deadline = time.monotonic() + _SURVEY_SYNC_WAIT_SECONDS
    while time.monotonic() < deadline:
        job = _fetchone("SELECT state, result, error FROM jobs WHERE id = %s", (job_id,))
        if job is None:
            break
        state = job.get("state")
        if state in ("done", "partial"):
            result = job.get("result") or {}
            survey_id = (result.get("patent_survey") or {}).get("survey_id")
            if survey_id is not None:
                survey = _fetchone(
                    "SELECT s.id, s.topic, s.status, s.created_at, r.path_docx, r.path_md, r.path_html, "
                    "r.id AS report_id FROM patent_surveys s LEFT JOIN reports r ON r.id = s.report_id "
                    "WHERE s.id = %(id)s",
                    {"id": survey_id},
                )
                if survey is not None:
                    return {"survey": survey, "job_id": job_id}
            break
        if state == "failed":
            return {"job_id": job_id, "status": "failed", "error": job.get("error")}
        time.sleep(_SURVEY_POLL_INTERVAL_SECONDS)
    return {"job_id": job_id, "status": "queued"}
