"""`GET /api/reports`, `/api/reports/{id}`, `/api/reports/{id}/file`, `GET /api/morning`."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from eoa.api import services
from eoa.api.errors import not_found

router = APIRouter(tags=["reports"])


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


@router.get("/morning")
def morning() -> dict:
    return services.morning()
