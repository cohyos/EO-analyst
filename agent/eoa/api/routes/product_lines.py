"""`GET /api/product-lines`, `GET /api/product-lines/{id}`, `POST /api/product-lines/{id}/report` --
PL-backend (user request 2026-09-07), frozen contract per the task brief and already built against
by the frontend (`web/src/api/real.ts` `getProductLines`/`getProductLine`/`postProductLineReport`,
`web/src/types/api.ts` `ProductLine`/`ProductLineDetail`/`ProductLineReportCreateResponse`). A
generated report is a normal `reports` row (`kind='product_line'`, the line id stored in the
existing `territory` column -- see `eoa.report.product_line` module docstring), so its full detail/
download/citations continue to be served by the existing generic `GET /api/reports/{id}` etc.
(agent/eoa/api/routes/reports.py) -- this router only adds the line-scoped list/detail/create
endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from eoa.api import services
from eoa.api.errors import bad_request, not_found

router = APIRouter(tags=["product-lines"])


@router.get("/product-lines")
def list_product_lines() -> list[dict]:
    return services.list_product_lines()


@router.get("/product-lines/{line_id}")
def get_product_line_detail(line_id: str) -> dict:
    detail = services.product_line_detail(line_id)
    if detail is None:
        raise not_found(f"קו מוצר לא ידוע: {line_id}")
    return detail


@router.post("/product-lines/{line_id}/report")
def create_product_line_report(line_id: str) -> dict:
    try:
        return services.enqueue_product_line_report(line_id)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc
