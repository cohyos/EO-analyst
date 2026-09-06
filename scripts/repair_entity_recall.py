#!/usr/bin/env python
"""Round-2 repair (docs/qa/loop/round_0_judge.md item 2, D3): backfills the entity-recall gap
fixed in ``eoa.pipeline.analyze`` -- a watchlist/curated-org name plainly present in an item's own
title/text (or an event's own extracted ``summary_he``) but missing from ``entities_mentioned`` /
``events.parties`` because the LLM's own extraction omitted it, even when *some* other entity was
already found (the old ``_backfill_entities_from_watchlist`` only ever ran when the list was
completely empty). Verified live: item 50's TITAN award ($192M) named Palantir alongside Anduril in
its own text, but ``entities_mentioned`` only ever carried ``[US Army, Anduril, ...]``.

Two independent, idempotent passes, both pure Python/SQL (no LLM calls) -- reuses
``eoa.pipeline.analyze``'s own (now-fixed) ``_backfill_entities_from_watchlist``/
``_recall_event_parties`` helpers so the repair logic can never drift from the live pipeline logic:

1. **items.entities_mentioned**: every item is re-checked; a name found in its title/clean_text
   that isn't already in ``entities_mentioned`` is unioned in.
2. **events.parties**: every event is re-checked against its own ``summary_he``; a name found there
   that isn't already in ``parties`` is unioned in.

Run *after* ``scripts/repair_watchlist_strict_aliases.py`` (which removes a false attribution
first) -- the two are independent and safe in either order, but running strict-alias cleanup first
avoids this script ever having to reconcile a name that both scripts would otherwise touch.

Use ``--dry-run`` to print the plan without writing anything.

Run with the same ``DATABASE_URL`` as the app, e.g.:

    PYTHONPATH=agent DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \\
        python scripts/repair_entity_recall.py [--dry-run]
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

from eoa import db  # noqa: E402
from eoa.llm.schemas.analysis import EventOut  # noqa: E402
from eoa.pipeline.analyze import _backfill_entities_from_watchlist, _recall_event_parties  # noqa: E402


def _fetch_items() -> list[dict[str, Any]]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, clean_text, entities_mentioned
            FROM items
            WHERE domain IS NOT NULL AND domain <> 'out_of_scope'
            ORDER BY id
            """
        )
        return cur.fetchall()


def _fetch_events() -> list[dict[str, Any]]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, item_id, kind, title, parties, summary_he FROM events ORDER BY id")
        return cur.fetchall()


def repair(*, dry_run: bool = False) -> dict[str, Any]:
    items_fixed: list[dict[str, Any]] = []
    for item in _fetch_items():
        recalled = _backfill_entities_from_watchlist(item)
        if not recalled:
            continue
        before = item.get("entities_mentioned") or []
        items_fixed.append({"id": item["id"], "before": before, "after": recalled})
        if not dry_run:
            with db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE items SET entities_mentioned = %(names)s WHERE id = %(id)s",
                    {"names": recalled, "id": item["id"]},
                )

    events_fixed: list[dict[str, Any]] = []
    for ev in _fetch_events():
        parties_before = ev.get("parties") or []
        ev_out = EventOut(
            kind=ev.get("kind") or "other",
            title=ev.get("title") or "",
            parties=parties_before,
            summary_he=ev.get("summary_he") or "",
            confidence=0.5,
        )
        parties_after = _recall_event_parties(ev_out)
        if parties_after == parties_before:
            continue
        events_fixed.append(
            {"id": ev["id"], "item_id": ev["item_id"], "before": parties_before, "after": parties_after}
        )
        if not dry_run:
            with db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE events SET parties = %(parties)s WHERE id = %(id)s",
                    {"parties": parties_after, "id": ev["id"]},
                )

    return {"items_fixed": items_fixed, "events_fixed": events_fixed}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report the plan without writing it")
    args = parser.parse_args()

    report = repair(dry_run=args.dry_run)

    print(
        f"{'=' * 70}\nRound-2 entity-recall repair {'(DRY RUN)' if args.dry_run else '(APPLIED)'}\n{'=' * 70}"
    )
    print(f"\nitems.entities_mentioned fixed: {len(report['items_fixed'])}")
    for r in report["items_fixed"][:40]:
        print(f"  item={r['id']} {r['before']} -> {r['after']}")

    print(f"\nevents.parties fixed: {len(report['events_fixed'])}")
    for r in report["events_fixed"][:40]:
        print(f"  event={r['id']} item={r['item_id']} {r['before']} -> {r['after']}")

    print(f"\n{'=' * 70}")
    if args.dry_run:
        print("(dry run -- nothing written; re-run without --dry-run to apply)")


if __name__ == "__main__":
    main()
