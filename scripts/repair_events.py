#!/usr/bin/env python
"""Repair the `events` table (F9/F16 backfill).

Two independent passes, in this order:

1. **Dedup** — collapse exact duplicates: rows sharing the same `item_id` plus the same
   normalised identity (`kind` + sorted/casefolded `parties` + casefolded `customer` +
   casefolded `program`) are the same underlying business event re-extracted more than once by
   the analyze stage (F9/F16: the weekly events table showed ~170 rows with the same event
   repeated 3-4 times). Within each duplicate group the row with the **lowest id** is kept; the
   rest are deleted.
2. **Date backfill** — for rows (post-dedup) whose `date` is NULL, or whose `title`/`summary_he`
   text says "today"/"yesterday"/"this week" (Hebrew or English), set `date` from the linked
   item's `published_at`: "today"/"this week" -> the published date itself; "yesterday" -> the
   published date minus one day; a NULL date with no relative-day wording -> the published date
   as a best-effort fallback (same reasoning as F3's report-window fix: most items with no
   explicit event date are simply reporting something that happened around publication time).
   Every row touched by this pass gets `confidence = confidence * 0.9` (rounded to 3 decimals) to
   reflect that the date is inferred, not stated — left untouched (including a NULL confidence)
   when there is nothing to discount.

Usage:
    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \
    PYTHONPATH=agent python scripts/repair_events.py [--dry-run]

`--dry-run` computes and prints the same counts without writing anything.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import Any

# Ensure agent/ is in path (mirrors scripts/repair_titles.py)
sys.path.insert(0, "agent")

import structlog

log = structlog.get_logger(__name__)

_RELATIVE_TODAY = ("היום", "today")
_RELATIVE_YESTERDAY = ("אתמול", "yesterday")
_RELATIVE_THIS_WEEK = ("השבוע", "this week")


def _normalize_parties(parties: list[str] | None) -> tuple[str, ...]:
    return tuple(sorted(p.strip().casefold() for p in (parties or []) if p and p.strip()))


def _dedup_key(row: dict[str, Any]) -> tuple[int, str, tuple[str, ...], str, str]:
    return (
        row["item_id"],
        row.get("kind") or "",
        _normalize_parties(row.get("parties")),
        (row.get("customer") or "").strip().casefold(),
        (row.get("program") or "").strip().casefold(),
    )


def find_duplicate_ids(rows: list[dict[str, Any]]) -> list[int]:
    """Ids to delete: every row in a duplicate group except the one with the lowest id."""
    groups: dict[tuple, list[int]] = {}
    for row in rows:
        groups.setdefault(_dedup_key(row), []).append(row["id"])
    to_delete: list[int] = []
    for ids in groups.values():
        if len(ids) > 1:
            keep = min(ids)
            to_delete.extend(i for i in ids if i != keep)
    return sorted(to_delete)


def _mentions(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(kw.casefold() in lowered for kw in keywords)


def compute_date_backfill(row: dict[str, Any]) -> dt.date | None:
    """The date this row's `date` column should be set to, or ``None`` if no backfill applies."""
    published_at = row.get("published_at")
    if published_at is None:
        return None
    published_date = published_at.date() if hasattr(published_at, "date") else published_at
    text = f"{row.get('title') or ''} {row.get('summary_he') or ''}"

    if _mentions(text, _RELATIVE_YESTERDAY):
        return published_date - dt.timedelta(days=1)
    if _mentions(text, _RELATIVE_TODAY) or _mentions(text, _RELATIVE_THIS_WEEK):
        return published_date
    if row.get("date") is None:
        # No date at all and no relative-day wording either -- the item's own publication date is
        # still the best available estimate (per F3's "prefer the item's published date" spirit).
        return published_date
    return None


def run_repair(*, dry_run: bool = False) -> dict[str, int]:
    """Dedup then date-backfill. Returns counts: rows_scanned, duplicate_groups,
    duplicates_deleted, dates_backfilled."""
    from eoa.db import connection

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id, e.item_id, e.kind, e.title, e.date, e.parties, e.customer, e.program,
                   e.summary_he, e.confidence, i.published_at
            FROM events e
            JOIN items i ON i.id = e.item_id
            ORDER BY e.id
            """
        )
        rows = cur.fetchall()

    duplicate_ids = find_duplicate_ids(rows)
    duplicate_groups = len({_dedup_key(r) for r in rows if r["id"] in duplicate_ids})
    kept_rows = [r for r in rows if r["id"] not in duplicate_ids]

    backfills: list[tuple[int, dt.date, float | None]] = []
    for row in kept_rows:
        new_date = compute_date_backfill(row)
        if new_date is None:
            continue
        confidence = row.get("confidence")
        new_confidence = round(confidence * 0.9, 3) if confidence is not None else None
        backfills.append((row["id"], new_date, new_confidence))

    counts = {
        "rows_scanned": len(rows),
        "duplicate_groups": duplicate_groups,
        "duplicates_deleted": len(duplicate_ids),
        "dates_backfilled": len(backfills),
    }

    if dry_run:
        log.info("repair_events.dry_run", **counts)
        return counts

    with connection() as conn, conn.cursor() as cur:
        if duplicate_ids:
            cur.execute("DELETE FROM events WHERE id = ANY(%s)", (duplicate_ids,))
        for event_id, new_date, new_confidence in backfills:
            cur.execute(
                "UPDATE events SET date = %s, confidence = %s WHERE id = %s",
                (new_date, new_confidence, event_id),
            )
        conn.commit()

    log.info("repair_events.complete", **counts)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="compute and print counts only")
    args = parser.parse_args()

    counts = run_repair(dry_run=args.dry_run)
    mode = "DRY RUN" if args.dry_run else "APPLIED"
    print(
        f"\n{'=' * 60}"
        f"\nEvents Repair Summary ({mode}):"
        f"\n  Rows scanned:          {counts['rows_scanned']}"
        f"\n  Duplicate groups:      {counts['duplicate_groups']}"
        f"\n  Duplicates deleted:    {counts['duplicates_deleted']}"
        f"\n  Dates backfilled:      {counts['dates_backfilled']}"
        f"\n{'=' * 60}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
