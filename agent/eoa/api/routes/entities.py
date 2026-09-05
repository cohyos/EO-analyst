"""`GET /api/entities`, `/api/entities/{id}`, `/api/entities/{id}/graph`, `/api/graph`,
`/api/graph/query` (U10 Entities & Graph redesign, docs/REVIEW_2026-09-05.md)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from eoa.api import services
from eoa.api.errors import bad_request, not_found

router = APIRouter(tags=["entities"])


@router.get("/entities")
def list_entities(
    q: str | None = None,
    kind: str | None = None,
    country: str | None = None,
    watchlist: bool = False,
    all: bool = Query(False, description='"הצג הכל" -- bypass the default relevance filter (F15)'),
    sort: str = Query("last_seen", pattern="^(last_seen|mentions_7d|mentions_30d|name)$"),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict]:
    return services.list_entities(
        q=q, kind=kind, country=country, watchlist=watchlist, show_all=all, sort=sort, limit=limit
    )


@router.get("/entities/{entity_id}")
def get_entity(entity_id: int) -> dict:
    entity = services.get_entity(entity_id)
    if entity is None:
        raise not_found("הישות לא נמצאה")
    return entity


@router.get("/entities/{entity_id}/graph")
def entity_graph(entity_id: int, depth: int = Query(1, ge=1, le=4), labels: str | None = None) -> dict:
    """U10's preferred nested form of `GET /api/graph?entity_id=`, kept below unchanged
    for callers already using it -- both call the same `services.build_graph`."""
    try:
        return services.build_graph(entity_id, depth=depth, labels=labels)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc


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
