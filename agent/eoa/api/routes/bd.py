"""`POST /api/bd/reports`, `GET /api/bd/reports`, `GET /api/bd/territories` -- A11 "דוח מיקוד
לפיתוח עסקי, מכירה ושיווק לפי טריטוריה" (eoa.report.bd_territory). A generated report is a normal
`reports` row (`kind='bd_territory'`), so its full detail/download/citations continue to be served
by the existing generic `GET /api/reports/{id}`, `GET /api/reports/{id}/file`, `GET
/api/reports/{id}/citations` (agent/eoa/api/routes/reports.py) -- this router only adds the
territory-scoped list/selector and the create endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from eoa.api import services
from eoa.api.errors import bad_request

router = APIRouter(tags=["bd"])


class BdReportRequest(BaseModel):
    territory: str
    lookback_days: int = 90


@router.post("/bd/reports")
def create_bd_report(body: BdReportRequest) -> dict:
    """Builds synchronously if the underlying `bd_report` job finishes within ~55s, else enqueues
    it and returns a `job_id` to poll (`GET /api/bd/reports?territory=`)."""
    try:
        return services.build_or_enqueue_bd_report(body.territory, body.lookback_days)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc


@router.get("/bd/reports")
def list_bd_reports(territory: str | None = None, limit: int = Query(30, ge=1, le=200)) -> list[dict]:
    return services.list_bd_reports(territory=territory, limit=limit)


@router.get("/bd/territories")
def list_bd_territories() -> list[dict]:
    return services.bd_territories()
