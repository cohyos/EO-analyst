#!/usr/bin/env python
"""A13 (מיקוד תעשייה ישראלית, 2026-09-06): one-off backfill of
``eoa.pipeline.israel_focus.israel_relevance()`` over every existing ``items`` row (and
``entities.is_israeli`` over every existing ``entities`` row) -- the classify.py/analyze.py "# ---
A13" hooks only ever run for items processed *after* this feature shipped; this script computes
the same deterministic (no LLM) score for everything that already exists.

Two independent, idempotent, no-LLM sweeps:

1. **Items**: every row in ``items`` gets ``israel_relevance``/``israel_reasons`` (re)computed from
   its own ``title`` + ``clean_text`` + ``entities_mentioned`` + ``lang`` + ``geography``, via the
   exact same ``eoa.pipeline.israel_focus.israel_relevance`` the live pipeline calls. Only ever
   *raises* an existing score already set by a live pipeline run since this script was last run
   (mirrors analyze.py's own "never lowering" contract) -- safe to re-run after every watchlist
   change.
2. **Entities**: every row in ``entities`` gets ``is_israeli`` (re)computed via
   ``eoa.pipeline.israel_focus.score_and_persist_entity_israeli``.

Usage:
    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \
    PYTHONPATH=agent python scripts/backfill_israel_relevance.py [--dry-run] [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, "agent")

import structlog

log = structlog.get_logger(__name__)


def _items_pass(*, limit: int | None = None, dry_run: bool = False) -> dict[str, Any]:
    from eoa.db import connection
    from eoa.memory.relational import update_item_fields
    from eoa.pipeline.israel_focus import israel_relevance

    with connection() as conn, conn.cursor() as cur:
        query = (
            "SELECT id, title, clean_text, entities_mentioned, lang, geography, israel_relevance "
            "FROM items ORDER BY id"
        )
        if limit:
            query += f" LIMIT {int(limit)}"
        cur.execute(query)
        rows = cur.fetchall()

    scored: list[dict[str, Any]] = []
    for row in rows:
        text = " ".join(filter(None, [row.get("title"), row.get("clean_text")]))
        result = israel_relevance(
            text,
            row.get("entities_mentioned") or [],
            lang=row.get("lang"),
            geography=row.get("geography"),
        )
        previous = row.get("israel_relevance") or 0.0
        if result["score"] <= previous and result["score"] > 0:
            # Already at least this high from a live pipeline run -- no-op, don't overwrite reasons.
            continue
        scored.append({"id": row["id"], "title": row.get("title"), **result})
        if not dry_run:
            update_item_fields(row["id"], israel_relevance=result["score"], israel_reasons=result["reasons"])

    return {"candidates": len(rows), "updated": scored}


def _entities_pass(*, dry_run: bool = False) -> dict[str, Any]:
    from eoa.db import connection
    from eoa.pipeline.israel_focus import score_and_persist_entity_israeli

    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name FROM entities ORDER BY id")
        rows = cur.fetchall()

    flagged: list[str] = []
    for row in rows:
        if dry_run:
            continue
        is_il = score_and_persist_entity_israeli(row["name"])
        if is_il:
            flagged.append(row["name"])

    return {"candidates": len(rows), "flagged": flagged}


def _distribution_report() -> dict[str, Any]:
    from eoa.db import connection

    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM items WHERE israel_relevance >= 0.5")
        at_or_above_half = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM items WHERE COALESCE(israel_relevance, 0) > 0")
        any_signal = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM items")
        total_items = cur.fetchone()["n"]
        cur.execute(
            "SELECT id, title, israel_relevance, israel_reasons FROM items "
            "WHERE israel_relevance IS NOT NULL ORDER BY israel_relevance DESC, id LIMIT 15"
        )
        top_items = cur.fetchall()
        cur.execute(
            "SELECT name, country, relevance FROM entities WHERE is_israeli = true "
            "ORDER BY relevance DESC NULLS LAST, name LIMIT 15"
        )
        top_entities = cur.fetchall()
        cur.execute("SELECT count(*) AS n FROM entities WHERE is_israeli = true")
        israeli_entities_total = cur.fetchone()["n"]

    return {
        "total_items": total_items,
        "any_signal": any_signal,
        "at_or_above_half": at_or_above_half,
        "top_items": top_items,
        "israeli_entities_total": israeli_entities_total,
        "top_entities": top_entities,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="compute but do not write")
    parser.add_argument("--limit", type=int, default=None, help="cap the items pass (debug only)")
    args = parser.parse_args()

    items_report = _items_pass(limit=args.limit, dry_run=args.dry_run)
    print(f"[items] candidates={items_report['candidates']} updated={len(items_report['updated'])}")

    entities_report = _entities_pass(dry_run=args.dry_run)
    print(
        f"[entities] candidates={entities_report['candidates']} "
        f"flagged_israeli={len(entities_report['flagged'])}"
    )

    if args.dry_run:
        print("\n(dry run -- nothing written; re-run without --dry-run to apply)")
        return 0

    dist = _distribution_report()
    print(f"\n{'=' * 70}")
    print("Distribution (after backfill):")
    print(f"  total items:                     {dist['total_items']}")
    print(f"  items with any israel signal >0: {dist['any_signal']}")
    print(f"  items with israel_relevance>=0.5: {dist['at_or_above_half']}")
    print(f"  entities flagged is_israeli:      {dist['israeli_entities_total']}")
    print("\nTop 15 items by israel_relevance:")
    for r in dist["top_items"]:
        print(f"  id={r['id']} score={r['israel_relevance']} reasons={r['israel_reasons']} title={r['title']!r}")
    print("\nTop Israeli entities (by relevance):")
    for r in dist["top_entities"]:
        print(f"  {r['name']} (country={r.get('country')}, relevance={r.get('relevance')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
