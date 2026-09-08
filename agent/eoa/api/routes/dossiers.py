"""`GET /api/dossiers`, `POST /api/dossiers`, `GET /api/dossiers/{product_key}`,
`GET /api/dossiers/{product_key}/{id}`, `POST /api/dossiers/{product_key}/rerun` -- PD-backend
(user request 2026-09-08), frozen contract per ``docs/PLAN_PRODUCT_DOSSIER.md`` section 5 and the
UI lane (PD-ui) building against it. A generated dossier is also a normal ``reports`` row
(``kind='product_dossier'``) so its docx/md/html download continues to be served by the existing
generic ``GET /api/reports/{id}`` etc. (``agent/eoa/api/routes/reports.py``) -- this router only
adds the product-scoped list/detail/create/rerun endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from eoa.api import services
from eoa.api.errors import bad_request, not_found

router = APIRouter(tags=["dossiers"])


class DossierCreateRequest(BaseModel):
    product_name: str
    vendor: str | None = None
    aliases: list[str] = []
    product_line: str | None = None
    budget_multiplier: float | None = None


class DossierRerunRequest(BaseModel):
    budget_multiplier: float | None = None


@router.get("/dossiers")
def list_dossiers() -> list[dict]:
    return services.list_dossiers()


@router.post("/dossiers")
def create_dossier(body: DossierCreateRequest) -> dict:
    try:
        return services.enqueue_product_dossier(
            body.product_name, body.vendor, body.aliases, body.product_line, body.budget_multiplier
        )
    except ValueError as exc:
        raise bad_request(str(exc)) from exc


@router.get("/dossiers/{product_key}")
def get_dossier_detail(product_key: str) -> dict:
    detail = services.dossier_detail(product_key)
    if detail is None:
        raise not_found(f"לא נמצאה סקירת מוצר: {product_key}")
    return detail


@router.get("/dossiers/{product_key}/{dossier_id}")
def get_dossier_run(product_key: str, dossier_id: int) -> dict:
    row = services.get_dossier(product_key, dossier_id)
    if row is None:
        raise not_found(f"לא נמצאה סקירה {dossier_id} עבור {product_key}")
    return row


@router.post("/dossiers/{product_key}/rerun")
def rerun_dossier(product_key: str, body: DossierRerunRequest | None = None) -> dict:
    budget_multiplier = body.budget_multiplier if body is not None else None
    result = services.rerun_product_dossier(product_key, budget_multiplier)
    if result is None:
        raise not_found(f"לא נמצאה סקירת מוצר: {product_key}")
    return result
