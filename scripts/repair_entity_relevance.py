#!/usr/bin/env python
"""Backfill `entities.relevance` / `entities.is_watchlist` for every existing row (F15).

`eoa.pipeline.entity_relevance.score_and_persist_entity` is wired into the `analyze`
pipeline stage going forward, but the 408 entities already in the DB (as of migration
0007) were never scored -- their `relevance` defaults to 0, which would hide every one
of them under the Entities list's `relevance >= 0.4` default filter. This script computes
the same score for every existing entity in one pass and prints a report: how many land
above/below the 0.4 threshold, and ten examples of what the default (relevance-filtered)
view would hide, so a human can sanity-check the formula before trusting it in the UI.

One aggregate query up front (mention_count + in_scope_mentions per entity name, joined
against `items` once) rather than one query per entity, per
`eoa.pipeline.entity_relevance.compute_relevance_row`'s docstring.

Usage:
    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \
    PYTHONPATH=agent python scripts/repair_entity_relevance.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, "agent")

import structlog

from eoa.pipeline.entity_relevance import RELEVANCE_THRESHOLD, compute_relevance_row

log = structlog.get_logger(__name__)


def run_repair() -> tuple[int, int, int, list[dict]]:
    """Score every `entities` row. Returns (total, above_threshold, below_threshold, hidden_examples)."""
    from eoa.db import connection

    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, kind, aliases FROM entities ORDER BY id")
        entities = cur.fetchall()

        cur.execute(
            """
            SELECT unnest(entities_mentioned) AS name,
                   count(*) AS mention_count,
                   count(*) FILTER (
                       WHERE domain IS DISTINCT FROM 'out_of_scope'
                         AND level IN ('red', 'orange', 'yellow')
                   ) AS in_scope_mentions
            FROM items
            WHERE entities_mentioned IS NOT NULL
            GROUP BY 1
            """
        )
        stats_by_name = {r["name"]: (r["mention_count"], r["in_scope_mentions"]) for r in cur.fetchall()}

        above = 0
        below = 0
        hidden: list[dict] = []
        for row in entities:
            mention_count, in_scope = stats_by_name.get(row["name"], (0, 0))
            score, watchlist = compute_relevance_row(row, mention_count, in_scope)
            cur.execute(
                "UPDATE entities SET relevance = %s, is_watchlist = %s WHERE id = %s",
                (score, watchlist, row["id"]),
            )
            if score >= RELEVANCE_THRESHOLD:
                above += 1
            else:
                below += 1
                hidden.append(
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "kind": row["kind"],
                        "mention_count": mention_count,
                        "in_scope_mentions": in_scope,
                        "relevance": score,
                    }
                )
        conn.commit()

    hidden.sort(key=lambda h: h["relevance"])
    return len(entities), above, below, hidden[:10]


if __name__ == "__main__":
    total, above, below, examples = run_repair()
    print(f"\n{'=' * 70}\nEntity relevance backfill\n{'=' * 70}")
    print(f"  Total entities scored:        {total}")
    print(f"  Above threshold (>= {RELEVANCE_THRESHOLD}):  {above}")
    print(f"  Below threshold (hidden):     {below}")
    print("\n  10 examples of what the default (relevance-filtered) view now hides:")
    for e in examples:
        print(
            f"    #{e['id']:<5} {e['name']:<40} kind={e['kind']:<8} "
            f"mentions={e['mention_count']:<3} in_scope={e['in_scope_mentions']:<3} "
            f"relevance={e['relevance']}"
        )
    print(f"{'=' * 70}\n")
