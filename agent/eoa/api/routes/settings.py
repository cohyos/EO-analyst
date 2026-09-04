"""`GET/PUT /api/settings/{name}` (name in config|sources|watchlist|taxonomy|models)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from eoa.api import services
from eoa.api.errors import not_found

router = APIRouter(tags=["settings"])


class SettingsPayload(BaseModel):
    yaml: str


@router.get("/settings/{name}")
def get_settings(name: str) -> dict:
    try:
        return {"yaml": services.read_settings_yaml(name)}
    except KeyError as exc:
        raise not_found(f"קובץ הגדרות לא מוכר: {name}") from exc
    except FileNotFoundError as exc:
        raise not_found("קובץ ההגדרות לא קיים") from exc


@router.put("/settings/{name}")
def put_settings(name: str, body: SettingsPayload) -> dict:
    try:
        errors = services.write_settings_yaml(name, body.yaml)
    except KeyError as exc:
        raise not_found(f"קובץ הגדרות לא מוכר: {name}") from exc
    return {"ok": not errors, "errors": errors}
