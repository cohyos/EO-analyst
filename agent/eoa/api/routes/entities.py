"""`GET /api/entities`, `/api/entities/{id}`, `/api/entities/{id}/graph`, `/api/graph`,
`/api/graph/query` (U10 Entities & Graph redesign, docs/REVIEW_2026-09-05.md).

R10-graph (docs/qa/loop/round_10_fixes.md) adds five read endpoints on top of the U10 surface,
backed by the new `eoa.graph.queries` module (all new SQL there, not in `services.py`):
`/api/graph/overview`, `/api/graph/neighborhood/{id}`, `/api/graph/path`, `/api/graph/search`,
`/api/entities/{id}/detail`. Every U10 endpoint above is unchanged."""

from __future__ import annotations

from fastapi import APIRouter, Query

from eoa.api import services
from eoa.api.errors import bad_request, not_found
from eoa.graph import queries as graph_queries

router = APIRouter(tags=["entities"])


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [x.strip() for x in value.split(",") if x.strip()]
    return items or None


@router.get("/entities")
def list_entities(
    q: str | None = None,
    kind: str | None = None,
    country: str | None = None,
    watchlist: bool = False,
    israel: bool = False,
    all: bool = Query(False, description='"הצג הכל" -- bypass the default relevance filter (F15)'),
    sort: str = Query("last_seen", pattern="^(last_seen|mentions_7d|mentions_30d|name)$"),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict]:
    return services.list_entities(
        q=q,
        kind=kind,
        country=country,
        watchlist=watchlist,
        israel=israel,
        show_all=all,
        sort=sort,
        limit=limit,
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


@router.get("/graph/search")
def graph_search(q: str, limit: int = Query(20, ge=1, le=100)) -> list[dict]:
    """Autocomplete for the graph's entity search box."""
    return graph_queries.search_entities(q, limit=limit)


@router.get("/graph/overview")
def graph_overview(limit: int = Query(30, ge=1, le=200), since: str | None = None) -> dict:
    """The "map of the map" shown before an analyst picks a center entity: top entities by
    mention volume and the edges between them."""
    return graph_queries.overview(limit=limit, since=since)


@router.get("/graph/neighborhood/{entity_id}")
def graph_neighborhood(
    entity_id: int,
    depth: int = Query(1, ge=1, le=2),
    kinds: str | None = Query(None, description="comma-separated entity kinds"),
    relation_types: str | None = Query(None, description="comma-separated edge labels"),
    since: str | None = None,
    limit: int = Query(300, ge=1, le=1500, description='node cap -- the UI\'s "הצג עוד" bumps this'),
) -> dict:
    try:
        return graph_queries.neighborhood(
            entity_id,
            depth=depth,
            kinds=_split_csv(kinds),
            relation_types=_split_csv(relation_types),
            since=since,
            limit=limit,
        )
    except ValueError as exc:
        raise bad_request(str(exc)) from exc


@router.get("/graph/path")
def graph_path(a: int, b: int, max_depth: int = Query(4, ge=1, le=4)) -> dict:
    result = graph_queries.path(a, b, max_depth=max_depth)
    if result is None:
        raise not_found("לא נמצא מסלול בין הישויות", detail={"a": a, "b": b, "max_depth": max_depth})
    return result


@router.get("/entities/{entity_id}/detail")
def entity_detail(entity_id: int) -> dict:
    detail = graph_queries.entity_detail(entity_id)
    if detail is None:
        raise not_found("הישות לא נמצאה")
    return detail
