#!/usr/bin/env python
"""Q4-1 (docs/qa/findings_Q4_r1.md): retroactively mark already-stored bot-block/WAF challenge
pages as ``security_status='blocked'``.

`eoa.fetch.service._store_item` now detects a block page (`eoa.fetch.sanitize.detect_block_page`)
*before* it is ever written to the DB (see that module), but items ingested before this fix exist
with the challenge page's own text sitting in `title`/`clean_text` and `security_status='clean'`
-- e.g. 13 Safran pressroom items whose stored content is literally "This website is using a
security service..." / "Attention Required! | Cloudflare". This script finds every such row
across the whole `items` table (not just Safran) and repairs it in place:

    security_status = 'blocked'
    clean_text      = NULL
    title           = the neutral Hebrew note (BLOCKED_ITEM_TITLE_HE)

Safe by construction: `detect_block_page` is the exact function the live ingest path uses, so a
row this script would touch is a row that -- fetched today -- would already have been stored as
blocked. Nothing else on the row (url, raw_text, source_id, ...) is changed, so the original HTML
that was stored in `raw_text` remains available for audit if the source is later fixed and the
item can be genuinely re-fetched.

Usage (dry-run is the default -- prints what *would* change without writing anything):

    PYTHONPATH=agent .venv/Scripts/python scripts/repair_blocked_items.py
    PYTHONPATH=agent .venv/Scripts/python scripts/repair_blocked_items.py --apply
    PYTHONPATH=agent .venv/Scripts/python scripts/repair_blocked_items.py --apply --limit 500
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

sys.path.insert(0, "agent")

import structlog

log = structlog.get_logger(__name__)


@dataclass
class RepairStats:
    scanned: int = 0
    matched: int = 0
    updated: int = 0
    failed: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    examples: list[dict] = field(default_factory=list)


def _scan_candidates(cur, limit: int | None) -> list[dict]:
    """Rows worth checking: never re-flag an item already 'blocked', and skip rows with no text
    at all (nothing to match a phrase against, and a plain empty/NULL body is not itself proof of
    a block page -- `detect_block_page`'s tiny-body branch also needs a status we don't have here,
    so it is intentionally not applied retroactively; the phrase match alone drives this repair)."""
    query = """
        SELECT i.id, i.url, i.title, i.clean_text, i.security_status,
               COALESCE(s.name, 'unknown') AS source_name
        FROM items i
        LEFT JOIN sources s ON s.id = i.source_id
        WHERE i.security_status <> 'blocked'
          AND (COALESCE(i.clean_text, '') <> '' OR COALESCE(i.title, '') <> '')
        ORDER BY i.id
    """
    if limit:
        query += " LIMIT %(limit)s"
    cur.execute(query, {"limit": limit} if limit else None)
    return cur.fetchall()


def run_repair(*, apply: bool, limit: int | None = None) -> RepairStats:
    from eoa.db import connection
    from eoa.fetch.sanitize import BLOCKED_ITEM_TITLE_HE, detect_block_page

    stats = RepairStats()

    with connection() as conn, conn.cursor() as cur:
        rows = _scan_candidates(cur, limit)
        stats.scanned = len(rows)

        for row in rows:
            title = row["title"] or ""
            clean_text = row["clean_text"] or ""
            # No HTTP status is stored on the row, so only the phrase-match signal of
            # `detect_block_page` applies here (status=None never satisfies its status branch).
            is_blocked = detect_block_page(html=None, text=f"{title}\n{clean_text}", status=None)
            if not is_blocked:
                continue

            stats.matched += 1
            stats.by_source[row["source_name"]] = stats.by_source.get(row["source_name"], 0) + 1
            if len(stats.examples) < 10:
                stats.examples.append(
                    {
                        "id": row["id"],
                        "url": row["url"],
                        "source": row["source_name"],
                        "old_title": title[:100],
                    }
                )

            if not apply:
                continue

            try:
                cur.execute(
                    "UPDATE items SET security_status = 'blocked', clean_text = NULL, title = %(title)s "
                    "WHERE id = %(id)s",
                    {"title": BLOCKED_ITEM_TITLE_HE, "id": row["id"]},
                )
                stats.updated += 1
            except Exception as exc:
                stats.failed += 1
                log.warning("repair_blocked.update_failed", item_id=row["id"], error=repr(exc))

        if apply:
            conn.commit()
        else:
            conn.rollback()

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="Actually write the updates (default: dry-run).")
    parser.add_argument(
        "--limit", type=int, default=None, help="Cap the number of items scanned (debugging)."
    )
    args = parser.parse_args()

    stats = run_repair(apply=args.apply, limit=args.limit)

    print(f"\n{'=' * 64}")
    print(f"Blocked-item repair ({'APPLY' if args.apply else 'DRY-RUN'})")
    print(f"{'=' * 64}")
    print(f"  Scanned (candidate rows):     {stats.scanned}")
    print(f"  Matched block-page phrases:   {stats.matched}")
    if args.apply:
        print(f"  Updated:                      {stats.updated}")
        print(f"  Failed:                       {stats.failed}")
    print("  By source:")
    for name, count in sorted(stats.by_source.items(), key=lambda kv: -kv[1]):
        print(f"    {name:<30} {count}")
    if stats.examples:
        print("  Examples (up to 10):")
        for ex in stats.examples:
            print(f"    id={ex['id']:<6} source={ex['source']:<25} url={ex['url']}")
            print(f"           old_title={ex['old_title']!r}")
    print(f"{'=' * 64}\n")
    return 0 if stats.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
