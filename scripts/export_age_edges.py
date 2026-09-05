"""One-off: export Apache AGE `eo_graph` edges into the plain-SQL `graph_edges` table.

Usage::

    python scripts/export_age_edges.py [--database-url URL]

Part of the Windows-native migration (ADR-004, docs/PLAN_WINDOWS_NATIVE.md step 1a).
Run this against the **live docker DB** (which still holds real data in the AGE
`eo_graph` graph) **after** `alembic upgrade head` has been applied there, so the
`graph_edges` table (migration 0006) already exists. Idempotent: `graph_edges`'s
`UNIQUE (src_entity_id, dst_entity_id, label, item_id)` constraint means running
this script twice (or after new edges were added between runs) upserts rather than
duplicates.

This script -- not the application code -- is the last thing in the codebase that
still speaks Cypher/AGE. `agent/eoa/memory/graph.py` was rewritten in the same
change to run entirely on plain SQL (`graph_edges`) and no longer knows how to talk
to AGE at all, so the minimal agtype parsing this script needs is duplicated here
rather than imported. Migration 0006 deliberately does **not** drop the `age`
extension, the `eo_graph` graph, or any of its data -- the docker DB is being
retired wholesale, not migrated in place, so this script is the one-time bridge
that carries the graph's edges across before that DB is decommissioned. It does not
touch `entities` (assumed already migrated via the normal relational path, e.g.
`pg_dump`/`pg_restore`) and never writes to AGE.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

DEFAULT_DATABASE_URL = "postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst"

# Must match `agent/eoa/memory/graph.py`'s `GRAPH_NAME` / `EDGE_LABELS` -- duplicated
# here (not imported) since this script intentionally has no dependency on that
# (AGE-free) module.
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
    """Strip a trailing `::vertex`/`::edge`/`::path` suffix and JSON-decode the remainder.

    Same logic `eoa.memory.graph._parse_agtype` used to implement before that module
    was rewritten onto plain SQL; kept standalone here since this script is the last
    caller of Cypher left in the codebase.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    match = _AGTYPE_SUFFIX_RE.search(text)
    if match:
        text = text[: match.start()]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return raw


def _vertex_entity_id(vertex: Any) -> int | None:
    """Resolve an AGE `Entity` vertex back to its relational `entities.id`."""
    if not isinstance(vertex, dict):
        return None
    props = vertex.get("properties", vertex)
    if not isinstance(props, dict):
        return None
    eid = props.get("entity_id")
    return int(eid) if eid is not None else None


def _edge_fields(edge: Any) -> tuple[str | None, int | None, dict[str, Any]]:
    """Return `(label, item_id, other_props)` from a parsed AGE edge."""
    if not isinstance(edge, dict):
        return None, None, {}
    label = edge.get("label")
    props = edge.get("properties", edge)
    if not isinstance(props, dict):
        props = {}
    item_id = props.get("item_id")
    extra = {k: v for k, v in props.items() if k != "item_id"}
    return label, (int(item_id) if item_id is not None else None), extra


def fetch_age_edges(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Return every AGE edge as raw agtype rows: `{"a": <vertex>, "r": <edge>, "b": <vertex>}`."""
    with conn.cursor() as cur:
        cur.execute("LOAD 'age'")
        cur.execute('SET search_path = ag_catalog, "$user", public')
        cur.execute(
            f"""
            SELECT * FROM cypher('{GRAPH_NAME}', $$
                MATCH (a)-[r]->(b) RETURN a, r, b
            $$) AS (a agtype, r agtype, b agtype)
            """
        )
        return cur.fetchall()


def export(database_url: str) -> dict[str, int]:
    """Read every AGE edge and upsert it into `graph_edges`. Returns a counts dict."""
    counts = {"read": 0, "upserted": 0, "skipped_unresolvable": 0, "skipped_unknown_label": 0}
    with psycopg.connect(database_url, row_factory=dict_row, autocommit=False) as conn:
        rows = fetch_age_edges(conn)
        counts["read"] = len(rows)
        with conn.cursor() as cur:
            for row in rows:
                a = _parse_agtype(row["a"])
                r = _parse_agtype(row["r"])
                b = _parse_agtype(row["b"])
                src_id = _vertex_entity_id(a)
                dst_id = _vertex_entity_id(b)
                label, item_id, extra_props = _edge_fields(r)

                if src_id is None or dst_id is None or label is None:
                    counts["skipped_unresolvable"] += 1
                    continue
                if label not in EDGE_LABELS:
                    counts["skipped_unknown_label"] += 1
                    continue

                cur.execute(
                    """
                    INSERT INTO graph_edges (src_entity_id, dst_entity_id, label, item_id, props)
                    VALUES (%(src)s, %(dst)s, %(label)s, %(item_id)s, %(props)s)
                    ON CONFLICT (src_entity_id, dst_entity_id, label, item_id) DO UPDATE SET
                        props = EXCLUDED.props,
                        updated_at = now()
                    """,
                    {
                        "src": src_id,
                        "dst": dst_id,
                        "label": label,
                        "item_id": item_id,
                        "props": Json(extra_props),
                    },
                )
                counts["upserted"] += 1
        conn.commit()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        help="Postgres connection string (default: $DATABASE_URL or the docker-compose local default)",
    )
    args = parser.parse_args(argv)

    counts = export(args.database_url)
    print(f"AGE edges read:              {counts['read']}")
    print(f"graph_edges upserted:        {counts['upserted']}")
    print(f"skipped (unresolvable):      {counts['skipped_unresolvable']}")
    print(f"skipped (unknown label):     {counts['skipped_unknown_label']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
