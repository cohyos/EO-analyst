#!/usr/bin/env python
"""R4 (2026-10-01) approved narrow data repair: old archived documents that reached the reports
as current news.

Incidents: the 2026-10-01 tech report (reports id 303) rested on item 69207, a tender-kind item
created from https://ns1.ld.com/archive/2013/08-August/30-Aug-2013/FBO-03166032.htm (an Aug-2013
FBO notice mirrored on an archive site) plus an undated sam.gov RFI; the monthly report (id 304)
cited item 66855, an undated Defense Industry Daily evergreen page whose text mentions only 2008.
`eoa.fetch.url_dates` (archive-style `DD-Mon-YYYY` paths, `undated_text_published_at`), the
tenders scan's stale-archived guard and `eoa.fetch.service._store_item` stop this going forward;
this script repairs items already stored. Scope: items created in the last `--days` (default 60)
days.

  (a) URL date: if `date_from_url(url)` finds a date and `published_at` is NULL or more than
      `URL_DATE_MAX_LATER_DAYS` (45) days later than it, set `published_at` to the URL date.
  (b) Text year: an item still undated after (a) whose latest year mentioned in title+text
      (`latest_year_mentioned`) is <= current_year - 2 gets `published_at` = Jan 1 of that year.
  (c) Tender-kind items (`report_kind = 'tender'`) whose resulting date is more than 365 days old:
      `level = 'archive'`, and the linked `tenders` row (`tenders.item_id`), if any and still
      `status IN ('open', 'unknown')`, gets `status = 'closed'` (an 'awarded'/'archived' row is a
      deliberate terminal status and is never touched).

Rows are NEVER deleted. Before `--apply`, every affected row's pre-change values (item id, url,
published_at, level, domain, tender id/status) are dumped to a backup CSV in the scratchpad. After
`--apply` commits, the result is verified from a separate, fresh connection.

Usage (dry run -- default, prints the plan, writes nothing):
    set -a; . runtime/eoa.env; set +a
    PYTHONPATH=agent .venv/Scripts/python.exe scripts/repair_2026_10_01_archived_items.py

Apply (one transaction; backup CSV written first; verified afterwards on a new connection):
    set -a; . runtime/eoa.env; set +a
    PYTHONPATH=agent .venv/Scripts/python.exe scripts/repair_2026_10_01_archived_items.py --apply
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

#: Only items created within this many days are in scope.
DEFAULT_DAYS = 60
#: A tender-kind item dated more than this many days ago is archived (matches
#: `eoa.tenders.scan.STALE_ARCHIVED_NOTICE_DAYS`).
TENDER_STALE_DAYS = 365
#: Tender statuses this repair may close; 'awarded'/'archived'/'closed' are left as they are.
CLOSABLE_TENDER_STATUSES = ("open", "unknown")
#: Items we expect the plan to include (incident items) -- reported, not enforced.
EXPECTED_ITEM_IDS = (69207, 66855)

_BACKUP_DIR = Path(
    r"C:\Users\cohyo\AppData\Local\Temp\claude\C--Users-cohyo-Documents-EO-analyst"
    r"\3b24a26d-4bdd-49a3-b2dd-f67f85df545c\scratchpad"
)
_BACKUP_CSV = _BACKUP_DIR / "repair_2026_10_01_archived_items_backup.csv"


def _fetch_candidates(cur, *, days: int) -> list[dict[str, Any]]:
    # clean_text is only pulled for undated rows (the only ones rule (b) can use it for).
    cur.execute(
        """
        SELECT i.id, i.url, i.title, i.published_at, i.created_at, i.level, i.domain, i.report_kind,
               CASE WHEN i.published_at IS NULL THEN i.clean_text END AS clean_text,
               t.id AS tender_id, t.status AS tender_status
        FROM items i
        LEFT JOIN tenders t ON t.item_id = i.id
        WHERE i.created_at >= now() - make_interval(days => %(days)s)
        ORDER BY i.id
        """,
        {"days": days},
    )
    return list(cur.fetchall())


def _aware(ts: datetime | None) -> datetime | None:
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts


def compute_changes(rows: list[dict[str, Any]], *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Pure function (no DB access): for each candidate row decide (a)/(b) the new `published_at`
    and (c) the tender archive/close. Returns only rows with at least one change."""
    from eoa.fetch.url_dates import URL_DATE_MAX_LATER_DAYS, date_from_url, undated_text_published_at

    now = now or datetime.now(UTC)
    changes: list[dict[str, Any]] = []
    for row in rows:
        change: dict[str, Any] = {"id": row["id"]}
        current = _aware(row["published_at"])
        effective = current

        # (a) the URL's own date
        url_date = date_from_url(row["url"] or "", now=now)
        if url_date is not None:
            url_dt = datetime(url_date.year, url_date.month, url_date.day, tzinfo=UTC)
            if current is None or current - url_dt > timedelta(days=URL_DATE_MAX_LATER_DAYS):
                change["published_at_new"] = url_dt
                change["date_source"] = "url"
                effective = url_dt

        # (b) the latest year mentioned, for items still undated
        if effective is None:
            text_dt = undated_text_published_at(row["title"], row.get("clean_text"), now=now)
            if text_dt is not None:
                change["published_at_new"] = text_dt
                change["date_source"] = "text_year"
                effective = text_dt

        # (c) tender-kind items that are old
        if (
            row["report_kind"] == "tender"
            and effective is not None
            and (now - effective).days > TENDER_STALE_DAYS
        ):
            if row["level"] != "archive":
                change["level_new"] = "archive"
            if row["tender_id"] is not None and row["tender_status"] in CLOSABLE_TENDER_STATUSES:
                change["tender_id"] = row["tender_id"]
                change["tender_status_new"] = "closed"

        if len(change) > 1:
            changes.append(change)
    return changes


def _write_backup_csv(changes: list[dict[str, Any]], rows_by_id: dict[int, dict[str, Any]]) -> Path:
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    with _BACKUP_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "url", "published_at", "level", "domain", "tender_id", "tender_status"])
        for change in changes:
            row = rows_by_id[change["id"]]
            writer.writerow(
                [
                    row["id"],
                    row["url"],
                    row["published_at"],
                    row["level"],
                    row["domain"],
                    row["tender_id"],
                    row["tender_status"],
                ]
            )
    return _BACKUP_CSV


def _print_plan(
    changes: list[dict[str, Any]], rows_by_id: dict[int, dict[str, Any]], limit: int = 120
) -> None:
    for change in changes[:limit]:
        row = rows_by_id[change["id"]]
        bits = [f"item {change['id']} ({(row['url'] or '')[:80]})"]
        if "published_at_new" in change:
            bits.append(
                f"published_at: {row['published_at']} -> {change['published_at_new']} [{change['date_source']}]"
            )
        if "level_new" in change:
            bits.append(f"level: {row['level']} -> archive")
        if "tender_status_new" in change:
            bits.append(f"tender {change['tender_id']}: {row['tender_status']} -> closed")
        print("  " + " | ".join(bits))
    if len(changes) > limit:
        print(f"  ... and {len(changes) - limit} more")


def _apply_changes(cur, changes: list[dict[str, Any]]) -> None:
    for change in changes:
        if "published_at_new" in change:
            cur.execute(
                "UPDATE items SET published_at = %(pub)s WHERE id = %(id)s",
                {"pub": change["published_at_new"], "id": change["id"]},
            )
        if "level_new" in change:
            cur.execute("UPDATE items SET level = 'archive' WHERE id = %(id)s", {"id": change["id"]})
        if "tender_status_new" in change:
            cur.execute(
                "UPDATE tenders SET status = 'closed' WHERE id = %(tid)s AND status = ANY(%(ok)s)",
                {"tid": change["tender_id"], "ok": list(CLOSABLE_TENDER_STATUSES)},
            )


def verify_from_fresh_connection(changes: list[dict[str, Any]]) -> list[str]:
    """Re-read every changed row on a NEW connection (not the one that wrote it) and return a list
    of mismatches (empty == everything landed)."""
    from eoa.db import connection

    problems: list[str] = []
    ids = [c["id"] for c in changes]
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT i.id, i.published_at, i.level, t.id AS tender_id, t.status AS tender_status
            FROM items i LEFT JOIN tenders t ON t.item_id = i.id
            WHERE i.id = ANY(%(ids)s)
            """,
            {"ids": ids},
        )
        got = {r["id"]: r for r in cur.fetchall()}
    for change in changes:
        row = got.get(change["id"])
        if row is None:
            problems.append(f"item {change['id']}: missing on verify")
            continue
        if "published_at_new" in change and _aware(row["published_at"]) != change["published_at_new"]:
            problems.append(
                f"item {change['id']}: published_at {row['published_at']} != {change['published_at_new']}"
            )
        if "level_new" in change and row["level"] != "archive":
            problems.append(f"item {change['id']}: level {row['level']} != archive")
        if "tender_status_new" in change and row["tender_status"] != "closed":
            problems.append(f"item {change['id']}: tender status {row['tender_status']} != closed")
    return problems


def run_repair(*, apply: bool = False, days: int = DEFAULT_DAYS) -> dict[str, int]:
    from eoa.db import connection

    with connection() as conn, conn.cursor() as cur:
        rows = _fetch_candidates(cur, days=days)
        rows_by_id = {r["id"]: r for r in rows}
        changes = compute_changes(rows)

        counts = {
            "items_scanned": len(rows),
            "changed": len(changes),
            "date_fixes_url": sum(1 for c in changes if c.get("date_source") == "url"),
            "date_fixes_text_year": sum(1 for c in changes if c.get("date_source") == "text_year"),
            "level_archived": sum(1 for c in changes if "level_new" in c),
            "tenders_closed": sum(1 for c in changes if "tender_status_new" in c),
        }
        print(f"\nScanned {len(rows)} items created in the last {days} days.")
        for key in ("date_fixes_url", "date_fixes_text_year", "level_archived", "tenders_closed"):
            print(f"  {key}: {counts[key]}")
        _print_plan(changes, rows_by_id)
        changed_ids = {c["id"] for c in changes}
        for expected in EXPECTED_ITEM_IDS:
            print(
                f"  expected incident item {expected}: {'IN change list' if expected in changed_ids else 'NOT in change list'}"
            )

        if not apply:
            log.info("repair_2026_10_01_archived_items.dry_run", **counts)
            print("\nDRY RUN -- no changes written. Re-run with --apply to write them.")
            return counts

        backup_path = _write_backup_csv(changes, rows_by_id)
        print(f"\nBackup written: {backup_path}")
        _apply_changes(cur, changes)
        conn.commit()  # single transaction: everything above ran on this one connection/cursor

    log.info("repair_2026_10_01_archived_items.applied", **counts)
    print(f"\nAPPLIED: {len(changes)} items updated.")
    problems = verify_from_fresh_connection(changes)
    if problems:
        print("VERIFY FAILED:")
        for p in problems:
            print("  " + p)
    else:
        print(f"VERIFIED from a separate connection: all {len(changes)} changed rows match.")
    counts["verify_problems"] = len(problems)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="created_at look-back window in days")
    args = parser.parse_args()
    counts = run_repair(apply=args.apply, days=args.days)
    return 1 if counts.get("verify_problems") else 0


if __name__ == "__main__":
    sys.exit(main())
