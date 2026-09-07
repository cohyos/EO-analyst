#!/usr/bin/env python
"""Cross-source corroboration (2026-09-07 user requirement): one-off backfill of
``item_corroboration`` for existing in-scope items -- ``eoa.pipeline.corroboration``'s
``corroborate`` pipeline stage only ever runs for items processed *after* migration 0026 landed
(and for the last 7 days on every nightly re-check); this script computes the same deterministic
(no LLM, no network) check for whatever already exists in the DB.

Safety convention: **dry-run by default**. Pass ``--apply`` to actually write. This is the
opposite default of some older backfill scripts in this directory (``--dry-run`` opt-in, apply by
default) -- deliberately safer here per this feature's own standing rules.

Usage:
    DATABASE_URL=postgresql://eoa@127.0.0.1:5432/eoanalyst \\
    PYTHONPATH=agent python scripts/backfill_corroboration.py [--apply] [--days 90] [--limit N]
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


def _candidate_item_ids(days: int, limit: int | None) -> list[int]:
    from eoa.db import connection

    query = """
        SELECT id FROM items
        WHERE level IN ('red', 'orange', 'yellow')
          AND security_status = 'clean'
          AND dedup_of IS NULL
          AND COALESCE(published_at, fetched_at, created_at) >= now() - (%(days)s || ' days')::interval
        ORDER BY id
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, {"days": days})
        ids = [row["id"] for row in cur.fetchall()]
    return ids[:limit] if limit else ids


def _distribution() -> dict[str, int]:
    from eoa.db import connection

    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT status, count(*) AS n FROM item_corroboration GROUP BY status")
        rows = cur.fetchall()
    dist = {"single_source": 0, "corroborated": 0, "official_primary": 0, "unknown": 0}
    for row in rows:
        dist[row["status"]] = row["n"]
    return dist


def run(*, days: int, limit: int | None, apply: bool) -> dict[str, Any]:
    ids = _candidate_item_ids(days, limit)
    computed = 0
    failed = 0
    if apply:
        from eoa.pipeline.corroboration import compute_for_item

        for item_id in ids:
            try:
                if compute_for_item(item_id) is not None:
                    computed += 1
            except Exception as exc:
                failed += 1
                log.warning("backfill_corroboration_item_failed", item_id=item_id, error=str(exc)[:200])
    return {"candidates": len(ids), "computed": computed, "failed": failed}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply", action="store_true", help="actually write item_corroboration rows (default: dry run)"
    )
    parser.add_argument("--days", type=int, default=90, help="lookback window in days (default: 90)")
    parser.add_argument(
        "--limit", type=int, default=None, help="cap the number of items processed (debug only)"
    )
    args = parser.parse_args()

    report = run(days=args.days, limit=args.limit, apply=args.apply)
    print(
        f"[corroboration] candidates={report['candidates']} computed={report['computed']} failed={report['failed']}"
    )

    if not args.apply:
        print("\n(dry run -- nothing written; re-run with --apply to write)")
        return 0

    dist = _distribution()
    total = sum(dist.values())
    print(f"\n{'=' * 70}")
    print("Distribution (item_corroboration, all rows, after backfill):")
    for status in ("single_source", "corroborated", "official_primary", "unknown"):
        n = dist.get(status, 0)
        pct = (100.0 * n / total) if total else 0.0
        print(f"  {status:<18} {n:>6}  ({pct:.1f}%)")
    print(f"  {'total':<18} {total:>6}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
