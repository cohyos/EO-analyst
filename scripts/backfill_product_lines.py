#!/usr/bin/env python
"""PL-backend (user request 2026-09-07): one-off backfill of ``product_lines`` (migration 0027)
over every existing row in ``items``, ``events``, ``tenders``, ``tender_forecasts`` and ``patents``
-- the ``eoa.pipeline.analyze.persist_analysis`` tagging hook only ever runs for items processed
*after* this feature shipped; this script computes the same deterministic (no LLM)
``eoa.product_lines.tagging.tag_product_lines`` tags for everything that already exists.

Five independent, idempotent, no-LLM sweeps:

1. **Items**: ``title``/``summary_he``/``so_what_he`` + ``entities_mentioned`` + ``subdomain``.
2. **Events**: inherits its parent item's own (freshly recomputed) ``product_lines`` -- an event has
   no ``domain``/``subdomain`` of its own; it was extracted from the same text as its item, so it
   shares that item's product-line tags (same convention the live pipeline hook uses).
3. **Tenders**: ``title``/``summary_he`` + ``entities``, unioned with the linked item's tags
   (``tenders.item_id``) when there is one.
4. **Tender forecasts**: ``platform``/``payload_need`` + ``candidate_vendors``.
5. **Patents**: ``title``/``abstract``/``claims_summary_he``/``so_what_he`` + ``assignees`` +
   ``subdomain``.

Default is a dry run (report only, no writes) -- pass ``--apply`` to actually write. Prints
per-product-line counts either way, so a dry run's report is directly comparable to the applied
run's own counts.

Usage:
    DATABASE_URL=postgresql://eoa@127.0.0.1:5432/eoanalyst \
    PYTHONPATH=agent python scripts/backfill_product_lines.py [--apply] [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, "agent")


def _items_pass(*, limit: int | None = None, apply: bool = False) -> tuple[Counter, dict[int, list[str]]]:
    from eoa.db import connection
    from eoa.memory.relational import update_item_fields
    from eoa.product_lines.tagging import tag_product_lines

    with connection() as conn, conn.cursor() as cur:
        query = (
            "SELECT id, title, summary_he, so_what_he, entities_mentioned, subdomain FROM items ORDER BY id"
        )
        if limit:
            query += f" LIMIT {int(limit)}"
        cur.execute(query)
        rows = cur.fetchall()

    counts: Counter = Counter()
    by_item_id: dict[int, list[str]] = {}
    for row in rows:
        text_he = " ".join(filter(None, [row.get("summary_he"), row.get("so_what_he")]))
        lines = tag_product_lines(
            text_he=text_he,
            text_en=row.get("title"),
            entities=row.get("entities_mentioned") or [],
            subdomain=row.get("subdomain"),
        )
        by_item_id[row["id"]] = lines
        for line_id in lines:
            counts[line_id] += 1
        if apply and lines:
            update_item_fields(row["id"], product_lines=lines)
    return counts, by_item_id


def _events_pass(by_item_id: dict[int, list[str]], *, apply: bool = False) -> Counter:
    from eoa.db import connection

    counts: Counter = Counter()
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, item_id FROM events ORDER BY id")
        rows = cur.fetchall()
        for row in rows:
            lines = by_item_id.get(row.get("item_id")) or []
            for line_id in lines:
                counts[line_id] += 1
            if apply and lines:
                cur.execute("UPDATE events SET product_lines = %s WHERE id = %s", (lines, row["id"]))
    return counts


def _tenders_pass(by_item_id: dict[int, list[str]], *, apply: bool = False) -> Counter:
    from eoa.db import connection
    from eoa.product_lines.tagging import tag_product_lines

    counts: Counter = Counter()
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, title, summary_he, entities, item_id FROM tenders ORDER BY id")
        rows = cur.fetchall()
        for row in rows:
            lines = set(
                tag_product_lines(
                    text_he=row.get("summary_he"),
                    text_en=row.get("title"),
                    entities=row.get("entities") or [],
                    subdomain=None,
                )
            )
            lines |= set(by_item_id.get(row.get("item_id")) or [])
            lines_list = sorted(lines)
            for line_id in lines_list:
                counts[line_id] += 1
            if apply and lines_list:
                cur.execute("UPDATE tenders SET product_lines = %s WHERE id = %s", (lines_list, row["id"]))
    return counts


def _forecasts_pass(*, apply: bool = False) -> Counter:
    from eoa.db import connection
    from eoa.product_lines.tagging import tag_product_lines

    counts: Counter = Counter()
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, platform, payload_need, candidate_vendors FROM tender_forecasts ORDER BY id")
        rows = cur.fetchall()
        for row in rows:
            text_en = " ".join(filter(None, [row.get("platform"), row.get("payload_need")]))
            lines = tag_product_lines(
                text_he=None, text_en=text_en, entities=row.get("candidate_vendors") or [], subdomain=None
            )
            for line_id in lines:
                counts[line_id] += 1
            if apply and lines:
                cur.execute(
                    "UPDATE tender_forecasts SET product_lines = %s WHERE id = %s", (lines, row["id"])
                )
    return counts


def _patents_pass(*, apply: bool = False) -> Counter:
    from eoa.db import connection
    from eoa.product_lines.tagging import tag_product_lines

    counts: Counter = Counter()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, abstract, claims_summary_he, so_what_he, assignees, subdomain "
            "FROM patents ORDER BY id"
        )
        rows = cur.fetchall()
        for row in rows:
            text_he = " ".join(filter(None, [row.get("claims_summary_he"), row.get("so_what_he")]))
            text_en = " ".join(filter(None, [row.get("title"), row.get("abstract")]))
            lines = tag_product_lines(
                text_he=text_he,
                text_en=text_en,
                entities=row.get("assignees") or [],
                subdomain=row.get("subdomain"),
            )
            for line_id in lines:
                counts[line_id] += 1
            if apply and lines:
                cur.execute("UPDATE patents SET product_lines = %s WHERE id = %s", (lines, row["id"]))
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply", action="store_true", help="write the computed tags (default: dry run, report only)"
    )
    parser.add_argument("--limit", type=int, default=None, help="cap the items pass (debug only)")
    args = parser.parse_args()
    apply = args.apply

    items_counts, by_item_id = _items_pass(limit=args.limit, apply=apply)
    events_counts = _events_pass(by_item_id, apply=apply)
    tenders_counts = _tenders_pass(by_item_id, apply=apply)
    forecasts_counts = _forecasts_pass(apply=apply)
    patents_counts = _patents_pass(apply=apply)

    print("APPLIED" if apply else "DRY RUN (pass --apply to write)")
    print("=" * 70)
    all_line_ids = sorted(
        set(items_counts)
        | set(events_counts)
        | set(tenders_counts)
        | set(forecasts_counts)
        | set(patents_counts)
    )
    for line_id in all_line_ids:
        print(
            f"{line_id:24s} items={items_counts.get(line_id, 0):5d} events={events_counts.get(line_id, 0):5d} "
            f"tenders={tenders_counts.get(line_id, 0):5d} forecasts={forecasts_counts.get(line_id, 0):5d} "
            f"patents={patents_counts.get(line_id, 0):5d}"
        )
    print("=" * 70)
    print(f"items scanned: {len(by_item_id)}, items tagged: {sum(1 for v in by_item_id.values() if v)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
