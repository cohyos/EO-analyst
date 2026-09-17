#!/usr/bin/env python
"""One-off backfill of ``items.story_id`` (2026-09-17, "improve same-story grouping across
outlets and languages" task) -- runs ``eoa.pipeline.story_clustering.assign_story_ids`` once over
the last ``--since-days`` days (default 60, per the task brief) so every already-ingested item
gets a story grouping key, instead of waiting for the nightly ``stories`` stage to reach it item by
item going forward.

Reversible: writes a JSON backup of every touched item's ``{id, story_id}`` PAIR AS IT WAS BEFORE
this run to ``runtime/backups/story_id_backfill_<timestamp>.json`` before making any change, and
supports ``--restore <path>`` to write those exact values back (a plain ``bulk_set_story_ids`` call
over the backup file's contents -- no re-computation, so a restore is always exact).

Use ``--dry-run`` to compute and print the stats (including the SPICE-1000/F-35 sanity check)
without writing anything or creating a backup.

Run with the same ``DATABASE_URL`` as the app, e.g.:

    DATABASE_URL=<from runtime/eoa.env -- port 5432> \\
        PYTHONPATH=agent python scripts/backfill_story_ids.py --since-days 60 [--dry-run]

    # revert a previous run:
    DATABASE_URL=<...> PYTHONPATH=agent python scripts/backfill_story_ids.py \\
        --restore runtime/backups/story_id_backfill_20260917T120000Z.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_ROOT = Path(__file__).resolve().parent.parent
_AGENT_DIR = _ROOT / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

_BACKUP_DIR = _ROOT / "runtime" / "backups"

#: The six items the task brief measured by hand (Rafael SPICE 1000 on the F-35) -- printed as an
#: explicit sanity check that they all land on one `story_id` after this run.
_SPICE_ITEM_IDS = [24089, 25948, 22396, 22798, 23002, 26284]


def _backup_current_story_ids(since_days: int) -> Path:
    from eoa.memory.relational import get_items_for_story_clustering

    items = get_items_for_story_clustering(since_days)
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = _BACKUP_DIR / f"story_id_backfill_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    payload = [{"id": it["id"], "story_id": it.get("story_id")} for it in items]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _restore(path: Path) -> int:
    from eoa.memory.relational import bulk_set_story_ids

    rows = json.loads(path.read_text(encoding="utf-8"))
    # bulk_set_story_ids's UPDATE...FROM unnest needs a bigint for every value -- a backed-up
    # `story_id: null` (never clustered before this run) restores as NULL via a plain UPDATE
    # instead, since the bulk helper is int-only by design (see its own docstring).
    non_null = {r["id"]: r["story_id"] for r in rows if r["story_id"] is not None}
    null_ids = [r["id"] for r in rows if r["story_id"] is None]
    n = bulk_set_story_ids(non_null)
    if null_ids:
        from eoa.db import connection

        with connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE items SET story_id = NULL WHERE id = ANY(%(ids)s)", {"ids": null_ids})
            n += cur.rowcount
    return n


def _top_stories(stats_items: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    from eoa.memory.relational import get_items_for_story_clustering

    items = get_items_for_story_clustering(9999)  # re-read post-write story_id for reporting
    by_story: dict[int, list[dict[str, Any]]] = {}
    for it in items:
        sid = it.get("story_id")
        if sid is None:
            continue
        by_story.setdefault(sid, []).append(it)
    ranked = sorted(by_story.items(), key=lambda kv: len(kv[1]), reverse=True)[:limit]
    return [
        {"story_id": sid, "size": len(members), "titles": [m.get("title") for m in members[:6]]}
        for sid, members in ranked
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since-days", type=int, default=60, help="lookback window in days (default 60)")
    parser.add_argument("--dry-run", action="store_true", help="compute and print stats without writing")
    parser.add_argument("--restore", type=Path, default=None, help="restore story_id values from a backup JSON file")
    args = parser.parse_args()

    if args.restore is not None:
        n = _restore(args.restore)
        print(f"Restored story_id for {n} item(s) from {args.restore}")
        return 0

    from eoa.pipeline.story_clustering import assign_story_ids

    if args.dry_run:
        # A dry run still calls the real computation (pure, no DB write happens inside
        # `_build_components` itself) but must not persist -- monkeypatch the one write call for
        # the duration of this process.
        import eoa.pipeline.story_clustering as sc

        original_bulk = sc.bulk_set_story_ids
        original_mark = sc.mark_stage
        sc.bulk_set_story_ids = lambda assignment: None  # type: ignore[assignment]
        sc.mark_stage = lambda item_id, stage: None  # type: ignore[assignment]
        try:
            stats = assign_story_ids(since_days=args.since_days)
        finally:
            sc.bulk_set_story_ids = original_bulk
            sc.mark_stage = original_mark
        print("DRY RUN -- nothing written\n")
    else:
        backup_path = _backup_current_story_ids(args.since_days)
        print(f"Backup written: {backup_path}")
        stats = assign_story_ids(since_days=args.since_days)

    print(f"\n{'=' * 70}\nitems.story_id backfill (since_days={args.since_days})\n{'=' * 70}")
    print(f"items processed:        {stats.items_processed}")
    print(f"edges from dedup_of:    {stats.edges_dedup}")
    print(f"edges from corroboration: {stats.edges_corroboration}")
    print(f"edges from embedding:   {stats.edges_embedding}")
    print(f"edges from cross-lang title: {stats.edges_title}")
    print(f"stories total:          {stats.stories_total}")
    print(f"stories with >=2 members: {stats.stories_multi_member}")
    print(f"story_id values (re)written: {stats.reassigned}")

    if not args.dry_run:
        print("\nTop 10 largest stories:")
        for row in _top_stories([]):
            print(f"  story_id={row['story_id']} size={row['size']}")
            for t in row["titles"]:
                print(f"    - {t}")

        from eoa.memory.relational import get_items_for_story_clustering

        spice_items = {
            it["id"]: it.get("story_id")
            for it in get_items_for_story_clustering(9999)
            if it["id"] in _SPICE_ITEM_IDS
        }
        spice_story_ids = set(spice_items.values())
        print(f"\nSPICE-1000/F-35 sanity check: {spice_items}")
        print("  -> ONE story_id" if len(spice_story_ids) == 1 else f"  -> STILL SPLIT into {spice_story_ids}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
