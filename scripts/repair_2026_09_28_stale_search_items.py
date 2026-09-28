#!/usr/bin/env python
"""R4 (SOL-REVIEW3-2026-09-24 carryover, 2026-09-28) approved narrow data repair.

Five `kind: search` sources were added 2026-09-27 (army_technology_search,
naval_technology_search, airforce_technology_search, defense_update_search,
unmanned_systems_technology_search) to replace RSS/HTML feeds blocked by bot protection. Their
`site:` search hits carried no date of their own, so `published_at` was left NULL and report
recency (`COALESCE(published_at, created_at)`, see e.g. `eoa.report.daily`) treated months-old
articles as today's news -- the 2026-09-28 daily report's bottom line was a February-2026 Fortem/
2026-World-Cup story (unmannedsystemstechnology.com/2026/02/...), and the tech report rested on
similarly stale July/February items. Index/listing pages (army-technology.com/news/,
naval-technology.com/, defense-update.com/2026, defense-update.com/2026/09, .../latest-news/)
were also stored as if they were articles. Separately, L3Harris newsroom items whose URL says
`/newsroom/editorial/2024/09/...` or `.../2025/12/...` got `published_at` stamped with today
(2026-09-22) somewhere in the pipeline.

`eoa.fetch.url_dates.date_from_url` / `is_probable_article_url` and the ingest-time fixes in
`eoa.fetch.service` (search_hit_published_at's URL-date fallback, `_ingest_search_source`'s
listing-page/undated-hit rejection, `_store_item`'s URL-date sanity check) stop this going
forward. This script repairs what already landed in the DB before that fix -- scope limited to
items created since 2026-09-26 (`_SINCE`):

  (a) ALL such items: if `date_from_url(url)` finds a date and `published_at` is NULL, or more
      than `URL_DATE_MAX_LATER_DAYS` (45) days later than that URL date, set `published_at` to
      the URL date -- same rule as `eoa.fetch.service`'s own ingest-time check (imported from
      there, not duplicated).
  (b) The five `*_search` sources' items only: if the URL fails `is_probable_article_url` (an
      index/category/tag/pagination page, not a real article), set `level='archive'`,
      `domain='out_of_scope'`.

Rows are NEVER deleted -- (b) demotes them to 'archive'/'out_of_scope' so they drop out of
report recency/relevance without losing the row. Before `--apply`, every affected row's
(id, url, published_at, level, domain) -- as they were BEFORE this script touches them -- is
dumped to a backup CSV in the scratchpad.

Usage (dry run -- default, prints the plan, writes nothing):
    set -a; . runtime/eoa.env; set +a
    PYTHONPATH=agent .venv/Scripts/python.exe scripts/repair_2026_09_28_stale_search_items.py

Apply (one transaction; backup CSV written first):
    set -a; . runtime/eoa.env; set +a
    PYTHONPATH=agent .venv/Scripts/python.exe scripts/repair_2026_09_28_stale_search_items.py --apply
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, "agent")

import structlog

log = structlog.get_logger(__name__)

#: The five `kind: search` sources added 2026-09-27 that replaced RSS/HTML feeds blocked by bot
#: protection -- see config/sources.yaml. Keyed by `sources.name` (the DB upsert key), not the
#: yaml `id` slug -- see `eoa.fetch.sources_loader.upsert_sources_to_db`.
SEARCH_SOURCE_NAMES = (
    "Army Technology (search)",
    "Naval Technology (search)",
    "Airforce Technology (search)",
    "Defense Update (search)",
    "Unmanned Systems Technology (search)",
)

#: Only items created on/after this date are in scope -- the five sources above (and the bug
#: they exposed) only started ingesting 2026-09-27; this deliberately excludes any older,
#: unrelated item that might coincidentally have a URL-shaped date.
SINCE = "2026-09-26"

_BACKUP_DIR = Path(
    r"C:\Users\cohyo\AppData\Local\Temp\claude\C--Users-cohyo-Documents-EO-analyst"
    r"\3b24a26d-4bdd-49a3-b2dd-f67f85df545c\scratchpad"
)
_BACKUP_CSV = _BACKUP_DIR / "repair_2026_09_28_stale_search_items_backup.csv"


def _fetch_candidates(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT i.id, i.url, i.published_at, i.level, i.domain, s.name AS source_name
        FROM items i
        LEFT JOIN sources s ON s.id = i.source_id
        WHERE i.created_at >= %(since)s
        ORDER BY i.id
        """,
        {"since": SINCE},
    )
    return list(cur.fetchall())


def compute_changes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pure function (no DB access) so it's directly unit-testable: for each candidate row,
    decide whether (a) `published_at` needs a URL-date fix and/or (b) it needs the
    archive/out_of_scope demotion. Returns only rows that need at least one change."""
    from eoa.fetch.service import URL_DATE_MAX_LATER_DAYS
    from eoa.fetch.url_dates import date_from_url, is_probable_article_url

    changes: list[dict[str, Any]] = []
    for row in rows:
        url = row["url"] or ""
        change: dict[str, Any] = {"id": row["id"]}

        url_date = date_from_url(url)
        if url_date is not None:
            url_dt = datetime(url_date.year, url_date.month, url_date.day, tzinfo=UTC)
            published_at = row["published_at"]
            if published_at is not None and published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=UTC)
            needs_date_fix = published_at is None or (
                published_at - url_dt > timedelta(days=URL_DATE_MAX_LATER_DAYS)
            )
            if needs_date_fix:
                change["published_at_new"] = url_dt

        needs_archive = (
            row["source_name"] in SEARCH_SOURCE_NAMES
            and not is_probable_article_url(url)
            and (row["level"] != "archive" or row["domain"] != "out_of_scope")
        )
        if needs_archive:
            change["level_new"] = "archive"
            change["domain_new"] = "out_of_scope"

        if len(change) > 1:  # more than just "id"
            changes.append(change)
    return changes


def _write_backup_csv(changes: list[dict[str, Any]], rows_by_id: dict[int, dict[str, Any]]) -> Path:
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    with _BACKUP_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "url", "published_at", "level", "domain"])
        for change in changes:
            row = rows_by_id[change["id"]]
            writer.writerow([row["id"], row["url"], row["published_at"], row["level"], row["domain"]])
    return _BACKUP_CSV


def _print_plan(changes: list[dict[str, Any]], rows_by_id: dict[int, dict[str, Any]]) -> None:
    for change in changes[:80]:
        row = rows_by_id[change["id"]]
        bits = [f"item {change['id']} ({(row['url'] or '')[:90]})"]
        if "published_at_new" in change:
            bits.append(f"published_at: {row['published_at']} -> {change['published_at_new']}")
        if "level_new" in change:
            bits.append(f"level/domain: {row['level']}/{row['domain']} -> archive/out_of_scope")
        print("  " + " | ".join(bits))
    if len(changes) > 80:
        print(f"  ... and {len(changes) - 80} more")


def run_repair(*, apply: bool = False) -> dict[str, int]:
    from eoa.db import connection

    with connection() as conn, conn.cursor() as cur:
        rows = _fetch_candidates(cur)
        rows_by_id = {r["id"]: r for r in rows}
        changes = compute_changes(rows)

        date_fixes = sum(1 for c in changes if "published_at_new" in c)
        archive_fixes = sum(1 for c in changes if "level_new" in c)
        counts = {
            "items_scanned": len(rows),
            "changed": len(changes),
            "date_fixes": date_fixes,
            "archive_fixes": archive_fixes,
        }

        print(f"\nScanned {len(rows)} items created since {SINCE}.")
        print(f"  published_at fixes:          {date_fixes}")
        print(f"  archive/out_of_scope fixes:  {archive_fixes}")
        _print_plan(changes, rows_by_id)

        if not apply:
            log.info("repair_2026_09_28_stale_search_items.dry_run", **counts)
            print("\nDRY RUN -- no changes written. Re-run with --apply to write them.")
            return counts

        backup_path = _write_backup_csv(changes, rows_by_id)
        print(f"\nBackup written: {backup_path}")

        for change in changes:
            if "published_at_new" in change:
                cur.execute(
                    "UPDATE items SET published_at = %(pub)s WHERE id = %(id)s",
                    {"pub": change["published_at_new"], "id": change["id"]},
                )
            if "level_new" in change:
                cur.execute(
                    "UPDATE items SET level = 'archive', domain = 'out_of_scope' WHERE id = %(id)s",
                    {"id": change["id"]},
                )
        conn.commit()  # single transaction: everything above ran on this one connection/cursor

        log.info("repair_2026_09_28_stale_search_items.applied", **counts)
        print(f"\nAPPLIED: {len(changes)} rows updated ({date_fixes} date fixes, {archive_fixes} archive fixes).")
        return counts


def main() -> int:
    global SINCE
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument("--since", default=None, help="override the created_at lower bound (ISO date)")
    args = parser.parse_args()
    if args.since:
        SINCE = args.since
    run_repair(apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
