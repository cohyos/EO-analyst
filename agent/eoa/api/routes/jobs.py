"""`GET /api/jobs`, `POST /api/run`, `POST /api/jobs/{id}/cancel`."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from eoa.api import services
from eoa.api.errors import bad_request, conflict, not_found

router = APIRouter(tags=["jobs"])


class RunRequest(BaseModel):
    scope: str
    mode: str = "eco"


@router.get("/jobs")
def list_jobs(state: str | None = None, limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    return services.list_jobs(state=state, limit=limit)


@router.post("/run")
def run(body: RunRequest) -> dict:
    """U4/F17: idempotent -- if an equivalent run is already queued/running, responds 409 with
    that job's id/state instead of silently enqueueing a second one (repro: "הרץ עכשיו" clicked
    twice with no feedback in between)."""
    try:
        job_id = services.enqueue_run(body.scope, body.mode)
    except services.RunAlreadyActive as exc:
        raise conflict(
            "ריצה מקבילה כבר רצה או ממתינה בתור",
            detail={
                "job_id": exc.job.get("id"),
                "kind": exc.job.get("kind"),
                "state": exc.job.get("state"),
            },
        ) from exc
    except ValueError as exc:
        raise bad_request(str(exc)) from exc
    return {"job_id": job_id}


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: int) -> dict:
    job = services.cancel_job(job_id)
    if job is None:
        raise not_found("המשימה לא נמצאה")
    return job
