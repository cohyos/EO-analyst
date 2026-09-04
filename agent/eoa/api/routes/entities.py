"""`GET /api/entities`, `/api/entities/{id}`, `/api/graph`, `/api/graph/query`."""

from __future__ import annotations

from fastapi import APIRouter, Query

from eoa.api import services
from eoa.api.errors import bad_request, not_found

router = APIRouter(tags=["entities"])


@router.get("/entities")
def list_entities(q: str | None = None, kind: str | None = None, limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    return services.list_entities(q=q, kind=kind, limit=limit)


@router.get("/entities/{entity_id}")
def get_entity(entity_id: int) -> dict:
    entity = services.get_entity(entity_id)
    if entity is None:
        raise not_found("הישות לא נמצאה")
    return entity


@router.get("/graph")
def graph(entity_id: int, depth: int = Query(1, ge=1, le=4), labels: str | None = None) -> dict:
    try:
        return services.build_graph(entity_id, depth=depth, labels=labels)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc


@router.get("/graph/query")
def graph_query(name: str, arg: str | None = None) -> list[dict]:
    try:
        return services.run_named_graph_query(name, arg)
    except KeyError as exc:
        raise not_found(f"שאילתה לא מוכרת: {name}") from exc
