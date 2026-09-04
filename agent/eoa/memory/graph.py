"""The ONLY module allowed to issue Cypher. Talks to the `eo_graph` Apache AGE graph.

Vertex label: ``Entity {entity_id, name, kind, country}`` (entity_id mirrors
``entities.id`` from the relational schema).
Edge labels: ``COMPETITOR_OF, SUPPLIER_OF, PARTNER_OF, ACQUIRED, INTEGRATES_WITH,
BIDS_AGAINST, DERIVED_FROM`` -- every edge carries an ``item_id`` property
tracing back to the item that evidenced it.

Values are passed into Cypher via ``cypher()``'s native third `params` argument
(an agtype-cast JSON blob referenced in the query text as ``$name``), never by
string-interpolating untrusted data into the dollar-quoted Cypher body itself.
Edge/vertex *labels*, which Cypher cannot parameterize, are always checked
against an explicit allow-list before being embedded in a query string.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from eoa.db import connection

log = structlog.get_logger(__name__)

GRAPH_NAME = "eo_graph"

EDGE_LABELS = {
    "COMPETITOR_OF",
    "SUPPLIER_OF",
    "PARTNER_OF",
    "ACQUIRED",
    "INTEGRATES_WITH",
    "BIDS_AGAINST",
    "DERIVED_FROM",
}

_AGTYPE_SUFFIX_RE = re.compile(r"::(vertex|edge|path)\s*$")


def _parse_agtype(raw: Any) -> Any:
    """Parse one agtype text value from a `cypher()` result column.

    Strips a trailing ``::vertex`` / ``::edge`` / ``::path`` type annotation (if
    present) and JSON-decodes the remainder. When a type annotation was found,
    the returned dict carries an extra ``_agtype`` key naming it. Non-string
    input is returned unchanged; unparsable text is returned unchanged too.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw

    text = raw.strip()
    match = _AGTYPE_SUFFIX_RE.search(text)
    kind: str | None = None
    if match:
        kind = match.group(1)
        text = text[: match.start()]

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return raw

    if kind and isinstance(value, dict):
        value = dict(value)
        value["_agtype"] = kind
    return value


def _run_cypher(cypher_body: str, params: dict[str, Any] | None, out_columns: str) -> list[dict[str, Any]]:
    """Execute a Cypher query against `eo_graph` and return the raw (unparsed) result rows."""
    payload = json.dumps(params or {})
    query = f"""
        SELECT * FROM cypher('{GRAPH_NAME}', $$
            {cypher_body}
        $$, %s::agtype) AS ({out_columns})
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute("LOAD 'age'")
        cur.execute('SET search_path = ag_catalog, "$user", public')
        cur.execute(query, (payload,))
        return cur.fetchall()


def _require_edge_label(label: str) -> None:
    if label not in EDGE_LABELS:
        raise ValueError(f"unknown edge label: {label!r}; must be one of {sorted(EDGE_LABELS)}")


def ensure_graph() -> None:
    """Ensure the `eo_graph` graph, the `Entity` vlabel, and all edge labels exist (idempotent)."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("LOAD 'age'")
        cur.execute('SET search_path = ag_catalog, "$user", public')
        cur.execute("SELECT eo_graph_ensure()")
    log.info("graph.ensured", graph=GRAPH_NAME)


def merge_entity(entity_id: int, name: str, kind: str, country: str | None = None) -> dict[str, Any]:
    """Create or update the `Entity` vertex for a relational `entities.id`; returns the parsed vertex."""
    cypher_body = """
        MERGE (e:Entity {entity_id: $entity_id})
        SET e.name = $name, e.kind = $kind, e.country = $country
        RETURN e
    """
    rows = _run_cypher(
        cypher_body,
        {"entity_id": entity_id, "name": name, "kind": kind, "country": country or ""},
        "e agtype",
    )
    log.info("graph.entity_merged", entity_id=entity_id, name=name, kind=kind)
    return _parse_agtype(rows[0]["e"]) if rows else {}


def add_edge(
    src_entity_id: int,
    dst_entity_id: int,
    label: str,
    item_id: int | None,
    props: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge a `label`-typed edge `src -> dst`, stamped with the evidencing `item_id`."""
    _require_edge_label(label)
    edge_props = dict(props or {})
    edge_props["item_id"] = item_id
    cypher_body = f"""
        MATCH (a:Entity {{entity_id: $src}}), (b:Entity {{entity_id: $dst}})
        MERGE (a)-[r:{label}]->(b)
        SET r += $props
        RETURN r
    """
    rows = _run_cypher(
        cypher_body,
        {"src": src_entity_id, "dst": dst_entity_id, "props": edge_props},
        "r agtype",
    )
    log.info("graph.edge_added", src=src_entity_id, dst=dst_entity_id, label=label, item_id=item_id)
    return _parse_agtype(rows[0]["r"]) if rows else {}


def neighbors(entity_id: int, label: str | None = None, depth: int = 1) -> list[dict[str, Any]]:
    """Return distinct `Entity` vertices reachable from `entity_id` within `depth` hops."""
    if label is not None:
        _require_edge_label(label)
    rel_type = f":{label}" if label else ""
    hop_range = f"*1..{int(depth)}"
    cypher_body = f"""
        MATCH (a:Entity {{entity_id: $eid}})-[r{rel_type}{hop_range}]-(b:Entity)
        RETURN DISTINCT b
    """
    rows = _run_cypher(cypher_body, {"eid": entity_id}, "b agtype")
    return [_parse_agtype(row["b"]) for row in rows]


def partners_of_competitors(entity_name: str) -> list[dict[str, Any]]:
    """Return Entities that are `PARTNER_OF` any Entity that is `COMPETITOR_OF` `entity_name`."""
    cypher_body = """
        MATCH (start:Entity {name: $name})-[:COMPETITOR_OF]-(competitor:Entity)
        MATCH (competitor)-[:PARTNER_OF]-(partner:Entity)
        WHERE partner.entity_id <> start.entity_id
        RETURN DISTINCT partner
    """
    rows = _run_cypher(cypher_body, {"name": entity_name}, "partner agtype")
    return [_parse_agtype(row["partner"]) for row in rows]


def suppliers_of_program_bidders(program_name: str) -> list[dict[str, Any]]:
    """Return Entities that `SUPPLIER_OF` any Entity `BIDS_AGAINST` the named program.

    Assumes programs are modeled as `Entity` vertices (kind='program', per
    `entities.kind`) and that bidding companies carry a `BIDS_AGAINST` edge to
    the program vertex itself.
    """
    cypher_body = """
        MATCH (program:Entity {name: $name})
        MATCH (bidder:Entity)-[:BIDS_AGAINST]-(program)
        MATCH (supplier:Entity)-[:SUPPLIER_OF]->(bidder)
        RETURN DISTINCT supplier
    """
    rows = _run_cypher(cypher_body, {"name": program_name}, "supplier agtype")
    return [_parse_agtype(row["supplier"]) for row in rows]


def startups_linked_to_majors(min_links: int = 2) -> list[dict[str, Any]]:
    """Return Entities with at least `min_links` distinct edges to other Entities.

    There is no explicit "startup"/"major" flag in the schema, so this is a
    connectivity heuristic (well-linked entities are more likely to be
    startups with traction among established players), sorted by link count
    descending. Each returned dict carries an added `link_count` key.
    """
    cypher_body = """
        MATCH (s:Entity)-[r]-(other:Entity)
        WITH s, count(DISTINCT other) AS link_count
        WHERE link_count >= $min_links
        RETURN s, link_count
        ORDER BY link_count DESC
    """
    rows = _run_cypher(cypher_body, {"min_links": min_links}, "s agtype, link_count agtype")
    results: list[dict[str, Any]] = []
    for row in rows:
        entity = _parse_agtype(row["s"])
        if isinstance(entity, dict):
            entity = dict(entity)
            entity["link_count"] = _parse_agtype(row["link_count"])
        results.append(entity)
    return results


def entity_timeline(entity_id: int) -> list[dict[str, Any]]:
    """Return events (chronological) that mention the given entity.

    This is a relational join (`entities` -> `events`), not Cypher: it matches
    the entity's `name` against `events.parties` (array containment) and the
    `customer`/`program` text fields, since events do not carry a direct
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
