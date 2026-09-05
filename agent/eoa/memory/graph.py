"""The knowledge graph, backed by plain SQL (`graph_edges`) -- no Apache AGE, no Cypher.

2026-09-05 (ADR-004, docs/PLAN_WINDOWS_NATIVE.md step 1a): Apache AGE is gone.
`entities` rows *are* the graph vertices now (keyed by `entities.id`, same id
this module used to call `entity_id` on the shadow AGE vertex); edges live in
`graph_edges` (migration 0006): `src_entity_id, dst_entity_id, label, item_id,
props jsonb, created_at, updated_at`, unique on
`(src_entity_id, dst_entity_id, label, item_id)`.

Every public function keeps its original signature so callers (`eoa.pipeline.analyze`,
`eoa.api.services`, `eoa.export.obsidian`, `eoa.report.monthly`) did not need to change.
Where the old Cypher/agtype-based implementation returned a return value no caller
actually inspects (`merge_entity`, `add_edge`), this rewrite returns a plain flattened
dict of columns instead of trying to fake AGE's nested `{"properties": {...}}` wire
shape -- there is no agtype anymore. Where callers *do* inspect the shape
(`neighbors`, `edges_of`'s `EdgeRow`, `edge_stats`, the three named analytic queries),
the contract is preserved: `neighbors()` returns flattened `{"entity_id", "name",
"kind", "country"}` dicts (already tolerated by every real caller's
`vertex.get("properties", vertex)` fallback -- see `eoa.export.obsidian`), and
`edges_of()` returns the same `EdgeRow` dataclass as before, `created_at` now a real
(not always-None) timestamp since `graph_edges` stamps one automatically.

Depth-bounded traversals (`neighbors`, `edges_of`) use a recursive CTE walking
`graph_edges` edge-by-edge (not node-by-node) so that, exactly like the old
`-[*1..depth]-` Cypher pattern, only edges that are genuinely part of some path of
length <= depth from the queried entity are returned -- an edge between two
depth-1 peers is only included once the traversal actually reaches depth 2.
`depth` is clamped to `[1, 3]` and every query carries a row cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from psycopg.types.json import Json

from eoa.db import connection

log = structlog.get_logger(__name__)

EDGE_LABELS = {
    "COMPETITOR_OF",
    "SUPPLIER_OF",
    "PARTNER_OF",
    "ACQUIRED",
    "INTEGRATES_WITH",
    "BIDS_AGAINST",
    "DERIVED_FROM",
}

_MAX_DEPTH = 3
_NEIGHBOR_ROW_CAP = 500
_EDGE_ROW_CAP = 2000


def _require_edge_label(label: str) -> None:
    if label not in EDGE_LABELS:
        raise ValueError(f"unknown edge label: {label!r}; must be one of {sorted(EDGE_LABELS)}")


def _bounded_depth(depth: int) -> int:
    return max(1, min(int(depth), _MAX_DEPTH))


def _walk_cte(label: str | None) -> str:
    """Recursive CTE: edge-by-edge BFS from `%(eid)s`, out to `%(depth)s` hops (undirected).

    Each row of `walk` is one traversed edge plus the frontier node it was reached
    through (`endpoint`) and the hop count at which it was found (`hop`). When
    `label` is given, every hop (base case and recursive step alike) is restricted
    to that label, matching Cypher's `-[:LABEL*1..depth]-` semantics where the whole
    path is typed.
    """
    label_clause = "AND ge.label = %(label)s" if label is not None else ""
    return f"""
        WITH RECURSIVE walk(edge_id, src_entity_id, dst_entity_id, label, item_id,
                             props, created_at, endpoint, hop) AS (
            SELECT ge.id, ge.src_entity_id, ge.dst_entity_id, ge.label, ge.item_id,
                   ge.props, ge.created_at,
                   CASE WHEN ge.src_entity_id = %(eid)s THEN ge.dst_entity_id ELSE ge.src_entity_id END,
                   1
            FROM graph_edges ge
            WHERE (ge.src_entity_id = %(eid)s OR ge.dst_entity_id = %(eid)s)
            {label_clause}
            UNION
            SELECT ge.id, ge.src_entity_id, ge.dst_entity_id, ge.label, ge.item_id,
                   ge.props, ge.created_at,
                   CASE WHEN ge.src_entity_id = w.endpoint THEN ge.dst_entity_id ELSE ge.src_entity_id END,
                   w.hop + 1
            FROM graph_edges ge
            JOIN walk w ON (ge.src_entity_id = w.endpoint OR ge.dst_entity_id = w.endpoint)
            WHERE w.hop < %(depth)s
            {label_clause}
        )
    """


def ensure_graph() -> None:
    """Ensure the `graph_edges` table exists (no-op check -- it is created by migration 0006)."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.graph_edges') AS t")
        row = cur.fetchone()
    if not row or not row.get("t"):
        raise RuntimeError("graph_edges table does not exist; run `alembic upgrade head`")
    log.info("graph.ensured")


def merge_entity(entity_id: int, name: str, kind: str, country: str | None = None) -> dict[str, Any]:
    """Sync `name`/`kind`/(optionally) `country` onto the `entities` row for `entity_id`.

    Vertices are `entities` rows now, not a separate AGE copy, so this is a plain
    `UPDATE` rather than a Cypher `MERGE`. Unlike the old AGE version (which
    unconditionally overwrote the shadow vertex's `country` with `""` whenever
    callers passed `None` -- harmless there since it never touched the real
    relational data), a `None` `country` here leaves the existing value alone
    (`COALESCE`), since this now writes directly to `entities` and clobbering a
    real column on every edge write would violate "never invent/never destroy
    provenance" data the row may already carry from `eoa.memory.relational.upsert_entity`.
    """
    query = """
        UPDATE entities
        SET name = %(name)s, kind = %(kind)s, country = COALESCE(%(country)s, country)
        WHERE id = %(entity_id)s
        RETURNING id AS entity_id, name, kind, country
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, {"entity_id": entity_id, "name": name, "kind": kind, "country": country})
        row = cur.fetchone()
    log.info("graph.entity_merged", entity_id=entity_id, name=name, kind=kind)
    return dict(row) if row else {}


def add_edge(
    src_entity_id: int,
    dst_entity_id: int,
    label: str,
    item_id: int | None,
    props: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upsert a `label`-typed edge `src -> dst` into `graph_edges`, stamped with `item_id`.

    Backed by the table's `UNIQUE (src_entity_id, dst_entity_id, label, item_id)`
    constraint: a second call with the same four values updates `props`/`updated_at`
    in place instead of inserting a duplicate row (mirrors the old AGE `MERGE`).
    """
    _require_edge_label(label)
    query = """
        INSERT INTO graph_edges (src_entity_id, dst_entity_id, label, item_id, props)
        VALUES (%(src)s, %(dst)s, %(label)s, %(item_id)s, %(props)s)
        ON CONFLICT (src_entity_id, dst_entity_id, label, item_id) DO UPDATE SET
            props = EXCLUDED.props,
            updated_at = now()
        RETURNING id, src_entity_id, dst_entity_id, label, item_id, props, created_at, updated_at
    """
    params = {
        "src": src_entity_id,
        "dst": dst_entity_id,
        "label": label,
        "item_id": item_id,
        "props": Json(props or {}),
    }
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    log.info("graph.edge_added", src=src_entity_id, dst=dst_entity_id, label=label, item_id=item_id)
    return dict(row) if row else {}


def neighbors(entity_id: int, label: str | None = None, depth: int = 1) -> list[dict[str, Any]]:
    """Return distinct entities reachable from `entity_id` within `depth` hops (undirected)."""
    if label is not None:
        _require_edge_label(label)
    depth = _bounded_depth(depth)
    params: dict[str, Any] = {"eid": entity_id, "depth": depth}
    if label is not None:
        params["label"] = label
    query = (
        _walk_cte(label)
        + f"""
        SELECT DISTINCT e.id AS entity_id, e.name, e.kind, e.country
        FROM walk w
        JOIN entities e ON e.id = w.endpoint
        WHERE w.endpoint <> %(eid)s
        LIMIT {_NEIGHBOR_ROW_CAP}
        """
    )
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


@dataclass
class EdgeRow:
    """One graph edge with its full provenance, as returned by :func:`edges_of`.

    ``src_entity_id``/``dst_entity_id`` (and their ``*_name`` counterparts) reflect
    the edge's stored direction in `graph_edges`. ``item_id`` and ``evidence`` mirror
    what :func:`add_edge` stamped onto the edge; ``created_at`` is the row's real
    `graph_edges.created_at` timestamp (unlike the old AGE-backed version, this is
    always populated -- the table stamps it automatically, it is not invented here).
    """

    src_entity_id: int
    src_name: str | None
    dst_entity_id: int
    dst_name: str | None
    label: str
    item_id: int | None
    evidence: str | None
    created_at: Any | None


def edges_of(entity_id: int, label: str | None = None, depth: int = 1) -> list[EdgeRow]:
    """Return every edge touching `entity_id` within `depth` hops, each carrying its provenance."""
    if label is not None:
        _require_edge_label(label)
    depth = _bounded_depth(depth)
    params: dict[str, Any] = {"eid": entity_id, "depth": depth}
    if label is not None:
        params["label"] = label
    query = (
        _walk_cte(label)
        + f"""
        SELECT DISTINCT w.edge_id, w.src_entity_id, w.dst_entity_id, w.label, w.item_id,
               w.props, w.created_at, s.name AS src_name, d.name AS dst_name
        FROM walk w
        JOIN entities s ON s.id = w.src_entity_id
        JOIN entities d ON d.id = w.dst_entity_id
        LIMIT {_EDGE_ROW_CAP}
        """
    )
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    out: list[EdgeRow] = []
    for row in rows:
        props = row.get("props") or {}
        out.append(
            EdgeRow(
                src_entity_id=row["src_entity_id"],
                src_name=row["src_name"],
                dst_entity_id=row["dst_entity_id"],
                dst_name=row["dst_name"],
                label=row["label"],
                item_id=row["item_id"],
                evidence=props.get("evidence"),
                created_at=row["created_at"],
            )
        )
    return out


def edge_stats() -> dict[str, int]:
    """Return a count of edges per `EDGE_LABELS` label (0 for labels with no edges yet)."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT label, count(*) AS n FROM graph_edges GROUP BY label")
        rows = cur.fetchall()
    counts: dict[str, int] = dict.fromkeys(EDGE_LABELS, 0)
    for row in rows:
        if row["label"] in counts:
            counts[row["label"]] = int(row["n"])
    return counts


def partners_of_competitors(entity_name: str) -> list[dict[str, Any]]:
    """Return entities that are `PARTNER_OF` any entity that is `COMPETITOR_OF` `entity_name`."""
    query = """
        WITH start AS (SELECT id FROM entities WHERE name = %(name)s),
        competitors AS (
            SELECT DISTINCT
                CASE WHEN ge.src_entity_id = s.id THEN ge.dst_entity_id ELSE ge.src_entity_id END AS id
            FROM graph_edges ge
            JOIN start s ON ge.label = 'COMPETITOR_OF' AND (ge.src_entity_id = s.id OR ge.dst_entity_id = s.id)
        )
        SELECT DISTINCT e.id AS entity_id, e.name, e.kind, e.country
        FROM graph_edges ge
        JOIN competitors c ON ge.label = 'PARTNER_OF' AND (ge.src_entity_id = c.id OR ge.dst_entity_id = c.id)
        JOIN entities e
          ON e.id = CASE WHEN ge.src_entity_id = c.id THEN ge.dst_entity_id ELSE ge.src_entity_id END
        JOIN start s ON e.id <> s.id
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, {"name": entity_name})
        return cur.fetchall()


def suppliers_of_program_bidders(program_name: str) -> list[dict[str, Any]]:
    """Return entities that `SUPPLIER_OF` any entity `BIDS_AGAINST` the named program.

    Assumes programs are `entities` rows (`kind='program'`) and that bidding
    companies carry a directed `BIDS_AGAINST` edge to the program row itself;
    `SUPPLIER_OF` is directed supplier -> bidder, matching `add_edge`'s convention.
    """
    query = """
        WITH program AS (SELECT id FROM entities WHERE name = %(name)s),
        bidders AS (
            SELECT DISTINCT
                CASE WHEN ge.src_entity_id = p.id THEN ge.dst_entity_id ELSE ge.src_entity_id END AS id
            FROM graph_edges ge
            JOIN program p ON ge.label = 'BIDS_AGAINST' AND (ge.src_entity_id = p.id OR ge.dst_entity_id = p.id)
        )
        SELECT DISTINCT e.id AS entity_id, e.name, e.kind, e.country
        FROM graph_edges ge
        JOIN bidders b ON ge.label = 'SUPPLIER_OF' AND ge.dst_entity_id = b.id
        JOIN entities e ON e.id = ge.src_entity_id
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, {"name": program_name})
        return cur.fetchall()


def startups_linked_to_majors(min_links: int = 2) -> list[dict[str, Any]]:
    """Return entities with at least `min_links` distinct edges to other entities.

    There is no explicit "startup"/"major" flag in the schema, so this is a
    connectivity heuristic, sorted by link count descending. Each returned dict
    carries an added `link_count` key.
    """
    query = """
        SELECT e.id AS entity_id, e.name, e.kind, e.country, counts.link_count
        FROM entities e
        JOIN (
            SELECT node AS id, count(DISTINCT other) AS link_count
            FROM (
                SELECT src_entity_id AS node, dst_entity_id AS other FROM graph_edges
                UNION
                SELECT dst_entity_id AS node, src_entity_id AS other FROM graph_edges
            ) links
            GROUP BY node
        ) counts ON counts.id = e.id
        WHERE counts.link_count >= %(min_links)s
        ORDER BY counts.link_count DESC
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, {"min_links": min_links})
        return cur.fetchall()


def entity_timeline(entity_id: int) -> list[dict[str, Any]]:
    """Return events (chronological) that mention the given entity.

    This is a relational join (`entities` -> `events`), not graph traversal: it
    matches the entity's `name` against `events.parties` (array containment) and
    the `customer`/`program` text fields, since events do not carry a direct
    entity foreign key.
    """
    query = """
        SELECT ev.*
        FROM entities e
        JOIN events ev
          ON e.name = ANY(ev.parties)
          OR ev.customer = e.name
          OR ev.program = e.name
        WHERE e.id = %s
        ORDER BY ev.date NULLS LAST, ev.created_at
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, (entity_id,))
        return cur.fetchall()
