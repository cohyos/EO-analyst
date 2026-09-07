"""R10-graph read queries: everything the analyst-facing entity graph UI needs beyond the
original U10 `services.build_graph`/`get_entity` (docs/qa/loop/round_10_fixes.md).

All new SQL for the graph feature lives here, not in `eoa.api.services` (brief: "all new SQL
goes here, NOT in services.py") -- this module owns its own `_fetchone`/`_fetchall` helpers
(same shape as `services.py`'s, deliberately not shared, to keep this module import-light and
independently testable) built on `eoa.db.connection()`.

Five query shapes, matching the five new routes in `eoa.api.routes.entities`:
  - `search_entities(q)` -> `GET /api/graph/search`
  - `overview(limit, since)` -> `GET /api/graph/overview`
  - `neighborhood(entity_id, depth, kinds, relation_types, since)` -> `GET /api/graph/neighborhood/{id}`
  - `path(a, b, max_depth)` -> `GET /api/graph/path`
  - `entity_detail(entity_id)` -> `GET /api/entities/{id}/detail` -- thin wrapper around
    `services.get_entity` (imported lazily to avoid a module-load cycle: `services` does not
    import this package, so the cycle only exists if this module imported `services` at top
    level while some future `services` change imports `eoa.graph`) plus two new lists
    (investigations, reports) neither `get_entity` nor `build_graph` expose today.

Every function never invents data: a node/edge with no evidence date is left `None`, never
defaulted to "now" or to an arbitrary sentinel.
"""

from __future__ import annotations

from typing import Any

import structlog

from eoa.db import connection
from eoa.memory.graph import EDGE_LABELS

log = structlog.get_logger(__name__)

# Kept in sync with the `entities_kind_check` CHECK constraint (migration 0007).
ENTITY_KINDS = {"company", "program", "system", "person", "org", "country"}

_NEIGHBORHOOD_MAX_DEPTH = 2
_NEIGHBORHOOD_ROW_CAP = 4000
_NODE_CAP = 300
# Hard ceiling on the neighborhood `limit` param (the UI's "הצג עוד" bumps `limit` up to this) --
# prevents an unbounded request from pulling the whole graph into one response.
_NODE_LIMIT_MAX = 1500
_PATH_MAX_DEPTH_CAP = 4
_EVIDENCE_PER_EDGE_CAP = 3


def _fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


def _fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _require_kinds(kinds: list[str] | None) -> list[str] | None:
    if kinds is None:
        return None
    unknown = [k for k in kinds if k not in ENTITY_KINDS]
    if unknown:
        raise ValueError(f"unknown entity kind(s): {unknown}; must be one of {sorted(ENTITY_KINDS)}")
    return kinds


def _require_relation_types(labels: list[str] | None) -> list[str] | None:
    if labels is None:
        return None
    unknown = [x for x in labels if x not in EDGE_LABELS]
    if unknown:
        raise ValueError(f"unknown relation type(s): {unknown}; must be one of {sorted(EDGE_LABELS)}")
    return labels


# --------------------------------------------------------------------------
# node stats (mentions, last_seen, corroboration summary, product_lines)
# --------------------------------------------------------------------------


def _node_stats(entity_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Batched per-entity stats for a set of node ids: mention_count, last_seen, a
    corroboration-status breakdown across every item mentioning the entity, and the distinct
    `product_lines` those items carry (there is no `entities.product_lines` column -- items,
    events, tenders, tender_forecasts and patents carry it, migration 0027; entities do not)."""
    if not entity_ids:
        return {}
    rows = _fetchall(
        """
        SELECT e.id AS entity_id, e.name, e.kind, e.country,
            count(i.id) AS mention_count,
            max(COALESCE(i.published_at, i.fetched_at)) AS last_seen,
            count(i.id) FILTER (WHERE ic.status = 'corroborated') AS corroborated_n,
            count(i.id) FILTER (WHERE ic.status = 'official_primary') AS official_primary_n,
            count(i.id) FILTER (WHERE ic.status = 'single_source') AS single_source_n,
            count(i.id) FILTER (WHERE ic.status IS NULL OR ic.status = 'unknown') AS unknown_n,
            array_remove(array_agg(DISTINCT pl.pl), NULL) AS product_lines
        FROM entities e
        LEFT JOIN items i ON e.name = ANY(COALESCE(i.entities_mentioned, '{}'))
        LEFT JOIN item_corroboration ic ON ic.item_id = i.id
        LEFT JOIN LATERAL unnest(COALESCE(i.product_lines, '{}')) AS pl(pl) ON true
        WHERE e.id = ANY(%(ids)s)
        GROUP BY e.id, e.name, e.kind, e.country
        """,
        {"ids": entity_ids},
    )
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        out[r["entity_id"]] = {
            "id": r["entity_id"],
            "name": r["name"],
            "kind": r["kind"],
            "country": r["country"],
            "mention_count": int(r["mention_count"] or 0),
            "last_seen": r["last_seen"],
            "corroboration": {
                "corroborated": int(r["corroborated_n"] or 0),
                "official_primary": int(r["official_primary_n"] or 0),
                "single_source": int(r["single_source_n"] or 0),
                "unknown": int(r["unknown_n"] or 0),
            },
            "product_lines": list(r["product_lines"] or []),
        }
    return out


# --------------------------------------------------------------------------
# search_entities
# --------------------------------------------------------------------------


def search_entities(q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Autocomplete search: name or alias `ILIKE`, ordered by relevance then mention volume."""
    limit = min(max(int(limit), 1), 100)
    if not q or not q.strip():
        return []
    rows = _fetchall(
        """
        SELECT e.id, e.name, e.kind, e.country, e.relevance,
            (SELECT count(*) FROM items i WHERE e.name = ANY(COALESCE(i.entities_mentioned, '{}'))) AS mention_count
        FROM entities e
        WHERE e.name ILIKE %(q)s
           OR EXISTS (SELECT 1 FROM unnest(COALESCE(e.aliases, '{}')) a WHERE a ILIKE %(q)s)
        ORDER BY e.relevance DESC NULLS LAST, mention_count DESC NULLS LAST, e.name ASC
        LIMIT %(limit)s
        """,
        {"q": f"%{q.strip()}%", "limit": limit},
    )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "kind": r["kind"],
            "country": r["country"],
            "mention_count": int(r["mention_count"] or 0),
        }
        for r in rows
    ]


# --------------------------------------------------------------------------
# overview
# --------------------------------------------------------------------------


def overview(limit: int = 30, since: str | None = None) -> dict[str, Any]:
    """Top entities by mention volume (optionally since a date), plus the `graph_edges` between
    them -- a starting "map of the map" before an analyst picks a center entity."""
    limit = min(max(int(limit), 1), 200)
    since_clause = "AND COALESCE(i.published_at, i.fetched_at) >= %(since)s" if since else ""
    params: dict[str, Any] = {"limit": limit}
    if since:
        params["since"] = since
    top = _fetchall(
        f"""
        SELECT e.id,
            count(i.id) AS mention_count
        FROM entities e
        JOIN items i ON e.name = ANY(COALESCE(i.entities_mentioned, '{{}}')) {since_clause}
        GROUP BY e.id
        ORDER BY mention_count DESC
        LIMIT %(limit)s
        """,
        params,
    )
    ids = [r["id"] for r in top]
    if not ids:
        return {"nodes": [], "edges": []}
    stats = _node_stats(ids)
    edge_rows = _fetchall(
        """
        SELECT ge.id AS edge_id, ge.src_entity_id, ge.dst_entity_id, ge.label, ge.item_id,
               ge.created_at, i.title AS item_title, COALESCE(i.published_at, i.fetched_at) AS item_date
        FROM graph_edges ge
        LEFT JOIN items i ON i.id = ge.item_id
        WHERE ge.src_entity_id = ANY(%(ids)s) AND ge.dst_entity_id = ANY(%(ids)s)
        """,
        {"ids": ids},
    )
    edges = _aggregate_edges(edge_rows)
    nodes = [stats[i] for i in ids if i in stats]
    return {"nodes": nodes, "edges": edges}


# --------------------------------------------------------------------------
# neighborhood
# --------------------------------------------------------------------------


def _aggregate_edges(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group raw `graph_edges` (+ joined item) rows by (src, dst, label): weight = distinct
    items, first/last_seen from item evidence dates (falling back to the edge's own
    `created_at` when it carries no item), evidence = up to 3 most-recent {item_id, title,
    published_at}."""
    groups: dict[tuple[int, int, str], list[dict[str, Any]]] = {}
    for r in rows:
        key = (r["src_entity_id"], r["dst_entity_id"], r["label"])
        groups.setdefault(key, []).append(r)

    out: list[dict[str, Any]] = []
    for (src, dst, label), group_rows in groups.items():
        dated = [(r["item_date"] or r["created_at"]) for r in group_rows]
        dated = [d for d in dated if d is not None]
        evidence_rows = sorted(
            (r for r in group_rows if r.get("item_id") is not None),
            key=lambda r: r["item_date"] or r["created_at"] or "",
            reverse=True,
        )
        out.append(
            {
                "src": src,
                "dst": dst,
                "relation": label,
                "weight": len(group_rows),
                "first_seen": min(dated) if dated else None,
                "last_seen": max(dated) if dated else None,
                "evidence": [
                    {
                        "item_id": r["item_id"],
                        "title": r.get("item_title"),
                        "published_at": r.get("item_date"),
                    }
                    for r in evidence_rows[:_EVIDENCE_PER_EDGE_CAP]
                ],
            }
        )
    return out


def neighborhood(
    entity_id: int,
    *,
    depth: int = 1,
    kinds: list[str] | None = None,
    relation_types: list[str] | None = None,
    since: str | None = None,
    limit: int = _NODE_CAP,
) -> dict[str, Any]:
    """The center entity's neighborhood out to `depth` hops (<=2), filterable by node kind,
    relation type, and a "since" evidence-date floor. Nodes carry mention/last_seen/
    corroboration/product_lines (`_node_stats`); edges carry weight/first_seen/last_seen/
    up to-3 evidence items (`_aggregate_edges`). Capped at `limit` nodes (default `_NODE_CAP`,
    clamped to `[1, _NODE_LIMIT_MAX]`) -- the center is always kept, the rest kept by
    mention_count descending -- the UI's "הצג עוד" control re-requests with a larger `limit`
    rather than this function silently truncating with no way to see more."""
    depth = max(1, min(int(depth), _NEIGHBORHOOD_MAX_DEPTH))
    kinds = _require_kinds(kinds)
    relation_types = _require_relation_types(relation_types)
    limit = max(1, min(int(limit), _NODE_LIMIT_MAX))

    center = _fetchone("SELECT id, name, kind, country FROM entities WHERE id = %s", (entity_id,))
    if center is None:
        return {"nodes": [], "edges": [], "center_id": entity_id}

    label_clause = "AND ge.label = ANY(%(labels)s)" if relation_types else ""
    params: dict[str, Any] = {"eid": entity_id, "depth": depth}
    if relation_types:
        params["labels"] = relation_types

    rows = _fetchall(
        f"""
        WITH RECURSIVE walk(edge_id, src_entity_id, dst_entity_id, label, item_id, created_at, endpoint, hop) AS (
            SELECT ge.id, ge.src_entity_id, ge.dst_entity_id, ge.label, ge.item_id, ge.created_at,
                   CASE WHEN ge.src_entity_id = %(eid)s THEN ge.dst_entity_id ELSE ge.src_entity_id END,
                   1
            FROM graph_edges ge
            WHERE (ge.src_entity_id = %(eid)s OR ge.dst_entity_id = %(eid)s)
            {label_clause}
            UNION
            SELECT ge.id, ge.src_entity_id, ge.dst_entity_id, ge.label, ge.item_id, ge.created_at,
                   CASE WHEN ge.src_entity_id = w.endpoint THEN ge.dst_entity_id ELSE ge.src_entity_id END,
                   w.hop + 1
            FROM graph_edges ge
            JOIN walk w ON (ge.src_entity_id = w.endpoint OR ge.dst_entity_id = w.endpoint)
            WHERE w.hop < %(depth)s
            {label_clause}
        )
        SELECT DISTINCT w.edge_id, w.src_entity_id, w.dst_entity_id, w.label, w.item_id, w.created_at,
               i.title AS item_title, COALESCE(i.published_at, i.fetched_at) AS item_date
        FROM walk w
        LEFT JOIN items i ON i.id = w.item_id
        LIMIT {_NEIGHBORHOOD_ROW_CAP}
        """,
        params,
    )

    edges = _aggregate_edges(rows)
    if since:
        edges = [e for e in edges if (e["last_seen"] or "") >= since]

    node_ids = {entity_id}
    for e in edges:
        node_ids.add(e["src"])
        node_ids.add(e["dst"])
    stats = _node_stats(list(node_ids))
    # entity_id itself may have no items/edges yet -- never invent, but still surface it.
    if entity_id not in stats:
        stats[entity_id] = {
            "id": center["id"],
            "name": center["name"],
            "kind": center["kind"],
            "country": center["country"],
            "mention_count": 0,
            "last_seen": None,
            "corroboration": {"corroborated": 0, "official_primary": 0, "single_source": 0, "unknown": 0},
            "product_lines": [],
        }

    if kinds:
        node_ids = {
            nid for nid in node_ids if nid == entity_id or (stats.get(nid) or {}).get("kind") in kinds
        }
        edges = [e for e in edges if e["src"] in node_ids and e["dst"] in node_ids]

    ordered = sorted(
        (nid for nid in node_ids if nid != entity_id),
        key=lambda nid: (stats.get(nid) or {}).get("mention_count") or 0,
        reverse=True,
    )
    kept_ids = [entity_id, *ordered[: limit - 1]]
    kept_set = set(kept_ids)
    nodes = [stats[nid] for nid in kept_ids if nid in stats]
    edges = [e for e in edges if e["src"] in kept_set and e["dst"] in kept_set]
    truncated = len(node_ids) > len(kept_set)

    return {"nodes": nodes, "edges": edges, "center_id": entity_id, "truncated": truncated}


# --------------------------------------------------------------------------
# path
# --------------------------------------------------------------------------


def path(a: int, b: int, max_depth: int = 4) -> dict[str, Any] | None:
    """Shortest path between two entities over `graph_edges` (undirected, BFS via a recursive
    CTE that tracks the visited-node path to avoid cycles). Returns `None` when no path exists
    within `max_depth` hops -- never a fabricated/partial path."""
    max_depth = max(1, min(int(max_depth), _PATH_MAX_DEPTH_CAP))
    if a == b:
        row = _fetchone("SELECT id, name, kind, country FROM entities WHERE id = %s", (a,))
        if row is None:
            return None
        return {"nodes": [dict(row)], "edges": [], "hops": 0}

    row = _fetchone(
        """
        WITH RECURSIVE bfs(entity_id, node_path, edge_path, hops) AS (
            SELECT %(a)s::bigint, ARRAY[%(a)s::bigint], ARRAY[]::bigint[], 0
            UNION ALL
            SELECT nxt, bfs.node_path || nxt, bfs.edge_path || ge.id, bfs.hops + 1
            FROM bfs
            JOIN graph_edges ge ON ge.src_entity_id = bfs.entity_id OR ge.dst_entity_id = bfs.entity_id
            CROSS JOIN LATERAL (
                SELECT CASE WHEN ge.src_entity_id = bfs.entity_id THEN ge.dst_entity_id ELSE ge.src_entity_id END AS nxt
            ) hop
            WHERE bfs.hops < %(max_depth)s
              AND NOT (hop.nxt = ANY(bfs.node_path))
        )
        SELECT node_path, edge_path, hops FROM bfs WHERE entity_id = %(b)s ORDER BY hops ASC LIMIT 1
        """,
        {"a": a, "b": b, "max_depth": max_depth},
    )
    if row is None:
        return None

    node_ids: list[int] = list(row["node_path"])
    edge_ids: list[int] = list(row["edge_path"])
    node_rows = _fetchall(
        "SELECT id, name, kind, country FROM entities WHERE id = ANY(%(ids)s)", {"ids": node_ids}
    )
    by_id = {r["id"]: dict(r) for r in node_rows}
    nodes = [by_id[nid] for nid in node_ids if nid in by_id]

    edge_rows = _fetchall(
        """
        SELECT ge.id AS edge_id, ge.src_entity_id, ge.dst_entity_id, ge.label, ge.item_id, ge.created_at,
               i.title AS item_title, COALESCE(i.published_at, i.fetched_at) AS item_date
        FROM graph_edges ge
        LEFT JOIN items i ON i.id = ge.item_id
        WHERE ge.id = ANY(%(ids)s)
        """,
        {"ids": edge_ids},
    )
    by_edge_id = {r["edge_id"]: r for r in edge_rows}
    ordered_edge_rows = [by_edge_id[eid] for eid in edge_ids if eid in by_edge_id]
    edges = _aggregate_edges(ordered_edge_rows)

    return {"nodes": nodes, "edges": edges, "hops": int(row["hops"])}


# --------------------------------------------------------------------------
# entity_detail
# --------------------------------------------------------------------------


def _investigations_for_entity(entity_name: str) -> list[dict[str, Any]]:
    """Deep-search jobs (`kind='deep_search'`) whose `payload->>'item_id'` is an item that
    mentions this entity -- mirrors `services.get_item`'s per-item investigations query."""
    return _fetchall(
        """
        SELECT j.id AS job_id, j.state, j.payload->>'question' AS question, j.started_at, j.finished_at,
               j.created_at
        FROM jobs j
        WHERE j.kind = 'deep_search'
          AND (j.payload->>'item_id') IS NOT NULL
          AND (j.payload->>'item_id')::bigint IN (
              SELECT i.id FROM items i WHERE %(name)s = ANY(COALESCE(i.entities_mentioned, '{}'))
          )
        ORDER BY j.created_at DESC
        LIMIT 20
        """,
        {"name": entity_name},
    )


def _reports_for_entity(entity_name: str) -> list[dict[str, Any]]:
    """Reports whose `items_included` overlaps the set of items mentioning this entity."""
    return _fetchall(
        """
        SELECT r.id, r.kind, r.period_start, r.period_end, r.created_at
        FROM reports r
        WHERE r.items_included && (
            SELECT COALESCE(array_agg(i.id), '{}') FROM items i
            WHERE %(name)s = ANY(COALESCE(i.entities_mentioned, '{}'))
        )
        ORDER BY r.created_at DESC
        LIMIT 20
        """,
        {"name": entity_name},
    )


def entity_detail(entity_id: int) -> dict[str, Any] | None:
    """`GET /api/entities/{id}/detail`: `services.get_entity`'s card (mentions/events/edges/
    neighbors, read-only reuse, never duplicated here) plus `investigations` and `reports`
    citing the entity, which no existing endpoint surfaces."""
    from eoa.api import services  # lazy: avoids a module-load cycle if services ever imports this package

    card = services.get_entity(entity_id)
    if card is None:
        return None
    card = dict(card)
    card["investigations"] = _investigations_for_entity(card["name"])
    card["reports"] = _reports_for_entity(card["name"])
    return card
