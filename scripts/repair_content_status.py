#!/usr/bin/env python
"""Q3-10 (docs/qa/findings_Q3_r1.md) backfill: classify `items.content_status` ('full' | 'partial'
| 'stub') for existing rows via `eoa.fetch.content_quality.assess`, so the pipeline's paywall/
partial-content handling (skip full analysis for 'stub', flag 'partial') isn't limited to items
analyzed after this fix landed.

Every `items` row defaults to `content_status = 'full'` (migration 0015) -- this backfill
re-classifies every row (default included) from its actual `clean_text` length and content, since
the default itself is what needs correcting for a paywalled/stub item stored before this fix.

Usage:
    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \
    PYTHONPATH=agent python scripts/repair_content_status.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from typing import Any

sys.path.insert(0, "agent")

import structlog

log = structlog.get_logger(__name__)


def run_repair(*, dry_run: bool = False) -> dict[str, Any]:
    from eoa.db import connection
    from eoa.fetch.content_quality import assess

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, clean_text, raw_text, security_status, content_status FROM items ORDER BY id"
        )
        rows = cur.fetchall()

        changes: list[tuple[int, str, str]] = []  # (id, old_status, new_status)
        by_new_status: Counter[str] = Counter()
        for row in rows:
            new_status = assess(
                row.get("clean_text"),
                html_len=len(row.get("raw_text") or ""),
                status=row.get("security_status"),
            )
            by_new_status[new_status] += 1
            old_status = row.get("content_status") or "full"
            if new_status != old_status:
                changes.append((row["id"], old_status, new_status))

        counts = {
            "items_scanned": len(rows),
            "changed": len(changes),
            "now_full": by_new_status.get("full", 0),
            "now_partial": by_new_status.get("partial", 0),
            "now_stub": by_new_status.get("stub", 0),
        }

        if dry_run:
            log.info("repair_content_status.dry_run", **counts)
            for item_id, old, new in changes[:50]:
                print(f"  item {item_id}: {old} -> {new}")
            if len(changes) > 50:
                print(f"  ... and {len(changes) - 50} more")
            return counts

        for item_id, _old, new_status in changes:
            cur.execute(
                "UPDATE items SET content_status = %(status)s WHERE id = %(id)s",
                {"status": new_status, "id": item_id},
            )
        conn.commit()

    log.info("repair_content_status.complete", **counts)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="compute and print counts only")
    args = parser.parse_args()

    counts = run_repair(dry_run=args.dry_run)
    mode = "DRY RUN" if args.dry_run else "APPLIED"
    print(
        f"\n{'=' * 60}"
        f"\nContent-Status Backfill Summary ({mode}):"
        f"\n  Items scanned:   {counts['items_scanned']}"
        f"\n  Changed:         {counts['changed']}"
        f"\n  -> full:         {counts['now_full']}"
        f"\n  -> partial:      {counts['now_partial']}"
        f"\n  -> stub:         {counts['now_stub']}"
        f"\n{'=' * 60}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
