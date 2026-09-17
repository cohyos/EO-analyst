"""`GET /api/reports`, `/api/reports/{id}`, `/api/reports/{id}/file`,
`/api/reports/{id}/citations`, `GET /api/morning`, `POST /api/reports/tech-daily/build`,
`GET /api/reports/tech-daily/status`."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from eoa.api import services
from eoa.api.errors import bad_request, not_found

router = APIRouter(tags=["reports"])


# tech_daily (2026-09-17, user request): "בנה דוח טכנולוגיה עכשיו" -- these two routes have three
# literal path segments ("reports"/"tech-daily"/"build" or "status"), which never collides with the
# two-segment `/reports/{report_id}` below regardless of declaration order (a non-numeric
# `report_id` there 422s rather than falling through, but the segment count already keeps the two
# apart) -- see the other three-segment routes (`/file`, `/citations`) for the same shape.
class TechDailyBuildRequest(BaseModel):
    lookback_days: int = Field(1, ge=1, le=90)
    force: bool = False


@router.get("/reports")
def list_reports(kind: str | None = None, limit: int = Query(30, ge=1, le=200)) -> list[dict]:
    return services.list_reports(kind=kind, limit=limit)


@router.get("/reports/{report_id}")
def get_report(report_id: int) -> dict:
    report = services.get_report(report_id)
    if report is None:
        raise not_found("הדוח לא נמצא")
    return report


@router.get("/reports/{report_id}/file")
def download_report_file(report_id: int, fmt: str = Query(..., pattern="^(docx|md|html)$")) -> FileResponse:
    path = services.report_file_path(report_id, fmt)
    if path is None:
        raise not_found("הקובץ לא נמצא")
    return FileResponse(path, filename=path.name)


@router.get("/reports/{report_id}/citations")
def get_report_citations(report_id: int) -> dict:
    citations = services.report_citations(report_id)
    if citations is None:
        raise not_found("הדוח לא נמצא")
    return citations


@router.get("/morning")
def morning() -> dict:
    return services.morning()


@router.post("/reports/tech-daily/build")
def build_tech_daily_report(body: TechDailyBuildRequest) -> dict:
    try:
        return services.enqueue_tech_daily_report(body.lookback_days, force=body.force)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc


@router.get("/reports/tech-daily/status")
def tech_daily_report_status() -> dict:
    return services.tech_daily_status()
