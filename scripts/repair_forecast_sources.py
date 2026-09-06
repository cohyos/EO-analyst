#!/usr/bin/env python
"""Q3-11b (docs/qa/findings_Q3_r2.md) repair: dedupe repeated entries in
``tender_forecasts.sources``.

``forecast._upsert_forecast`` built ``sources`` from ``candidate.trigger_item_ids`` without
deduping it (fixed at write time in ``agent/eoa/tenders/forecast.py``, ``dict.fromkeys(...)``) --
existing rows still have repeats, e.g. ``['item:105', 'item:105', 'item:105']``. This script
applies the same order-preserving dedup to every existing row whose ``sources`` array has
duplicate entries.

Idempotent -- safe to re-run (a row with no duplicates is left untouched, counted as unchanged).

Usage:
    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \
    PYTHONPATH=agent python scripts/repair_forecast_sources.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, "agent")

import structlog

log = structlog.get_logger(__name__)


def _dedupe(sources: list[str] | None) -> list[str] | None:
    if not sources:
        return sources
    return list(dict.fromkeys(sources))


def run_repair(*, dry_run: bool = False) -> dict[str, Any]:
    from eoa.db import connection

    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, sources FROM tender_forecasts WHERE sources IS NOT NULL ORDER BY id")
        rows = cur.fetchall()

        changed: list[dict[str, Any]] = []
        for row in rows:
            before = row["sources"] or []
            after = _dedupe(before)
            if after != before:
                changed.append({"id": row["id"], "before_n": len(before), "after_n": len(after)})

        if not dry_run:
            for c in changed:
                cur.execute(
                    "SELECT sources FROM tender_forecasts WHERE id = %(id)s",
                    {"id": c["id"]},
                )
                before = cur.fetchone()["sources"] or []
                cur.execute(
                    "UPDATE tender_forecasts SET sources = %(sources)s, updated_at = now() WHERE id = %(id)s",
                    {"sources": _dedupe(before), "id": c["id"]},
                )
            conn.commit()

    counts = {"rows_scanned": len(rows), "rows_with_duplicates": len(changed), "changed": changed}
    log.info(
        "repair_forecast_sources.complete" if not dry_run else "repair_forecast_sources.dry_run",
        **{k: v for k, v in counts.items() if k != "changed"},
    )
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report the dedup plan without writing it")
    args = parser.parse_args()

    counts = run_repair(dry_run=args.dry_run)
    mode = "DRY RUN" if args.dry_run else "APPLIED"
    print(f"\n{'=' * 60}\nForecast Sources Dedup Repair ({mode}):")
    print(f"  Rows scanned:          {counts['rows_scanned']}")
    print(f"  Rows with duplicates:  {counts['rows_with_duplicates']}")
    for c in counts["changed"][:20]:
        print(f"    id={c['id']}: {c['before_n']} -> {c['after_n']} sources")
    print(f"{'=' * 60}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
