#!/usr/bin/env python
"""D9 round-1 repair (docs/qa/loop/round_1_fixes.md, ``sources_recently_fetched``): one-off
backfill of ``sources.last_fetched_at`` for every source that has ingested items but has never had
its bookkeeping touched (the gap fixed going forward by
``eoa.memory.relational.touch_source_fetched`` / ``eoa.fetch.service._touch_source_fetched``, which
only runs on the *next* ``run_ingest`` call, not retroactively).

Sets ``sources.last_fetched_at = MAX(items.fetched_at)`` for every ``source_id`` that has at least
one row in ``items`` with a non-null ``fetched_at`` -- the most recent item actually stored for
that source is, at minimum, proof the source was fetched at that time (a lower bound, not
necessarily the true last-attempt time, but the best signal available after the fact for a source
whose own bookkeeping was never written). A source with items but a ``last_fetched_at`` already
*more recent* than that ``MAX(items.fetched_at)`` (a source already correctly bookkept by the
fix, or freshly attempted with no new items) is left untouched -- this never moves the timestamp
backwards.

Use ``--dry-run`` to see what would change without writing anything.

Run with the same ``DATABASE_URL`` as the app, e.g.:

    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5433/eoanalyst \\
        PYTHONPATH=agent python scripts/backfill_source_last_fetched.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))


def _candidates(cur: Any) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT s.id, s.name, s.last_fetched_at, m.max_fetched_at
        FROM sources s
        JOIN (
            SELECT source_id, max(fetched_at) AS max_fetched_at
            FROM items
            WHERE source_id IS NOT NULL AND fetched_at IS NOT NULL
            GROUP BY source_id
        ) m ON m.source_id = s.id
        WHERE s.last_fetched_at IS NULL OR s.last_fetched_at < m.max_fetched_at
        ORDER BY s.id
        """
    )
    return cur.fetchall()


def repair(*, dry_run: bool = False) -> list[dict[str, Any]]:
    from eoa.db import connection

    report: list[dict[str, Any]] = []
    with connection() as conn, conn.cursor() as cur:
        rows = _candidates(cur)
        for row in rows:
            report.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "before": row["last_fetched_at"],
                    "after": row["max_fetched_at"],
                }
            )
            if not dry_run:
                cur.execute(
                    "UPDATE sources SET last_fetched_at = %(v)s WHERE id = %(id)s",
                    {"v": row["max_fetched_at"], "id": row["id"]},
                )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="report what would change without writing it")
    args = parser.parse_args()

    report = repair(dry_run=args.dry_run)
    mode = "DRY RUN" if args.dry_run else "APPLIED"
    print(f"\n{'=' * 70}\nsources.last_fetched_at backfill from MAX(items.fetched_at) ({mode})\n{'=' * 70}")
    print(f"\nsources updated: {len(report)}")
    for r in report[:30]:
        print(f"  id={r['id']} name={r['name']!r}: {r['before']} -> {r['after']}")
    if len(report) > 30:
        print(f"  ... and {len(report) - 30} more")
    print(f"\n{'=' * 70}")
    if args.dry_run:
        print("(dry run -- nothing written; re-run without --dry-run to apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
