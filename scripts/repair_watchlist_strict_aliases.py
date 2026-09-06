#!/usr/bin/env python
"""Round-2 repair (docs/qa/loop/round_0_judge.md item 8, D3): removes the false entity
attributions caused by the ``config/watchlist.yaml`` alias-collision bug -- a product/program-style
alias (e.g. BlueHalo's ``LOCUST``, colliding with AeroVironment's own "Locust X3" product; ``Titan``,
colliding with the US Army's own TITAN program) used to attribute *any* text mention of that bare
word to the aliased company, with no check that the company itself was actually named.

``config/watchlist.yaml`` now marks these as ``strict_aliases`` per company, and
``eoa.pipeline.entity_normalize.find_watchlist_aliases_in_text`` (the function
``eoa.pipeline.analyze._backfill_entities_from_watchlist`` calls to deterministically fill
``items.entities_mentioned``) requires the company's own canonical name (or a non-strict alias) to
independently co-occur in the same text before a strict-alias match counts. This script re-checks
every existing row against that corrected logic and removes the now-unjustified attribution:

1. **items.entities_mentioned**: for every item whose ``entities_mentioned`` contains a watchlist
   company that owns at least one ``strict_aliases`` entry, re-run
   :func:`eoa.pipeline.entity_normalize.find_watchlist_aliases_in_text` against
   ``COALESCE(clean_text, raw_text, '') || ' ' || COALESCE(title, '')`` -- if the company no longer
   comes back, drop it from the array (a company still legitimately mentioned by name, or via a
   non-strict alias, is left untouched).
2. **events.parties / events.customer / events.program**: the same company name, if present on an
   event belonging to one of the affected items, is removed/nulled the same way (an event's own
   ``summary_he`` is checked too, since an event can be extracted with different wording than the
   item's own title/text).

Never deletes the watchlist company's own ``entities`` row -- BlueHalo/Anduril/Rheinmetall/IAI are
real, legitimately-tracked companies that will have other genuine mentions elsewhere in the corpus;
only the specific false per-item/per-event attribution is cleaned up.

Use ``--dry-run`` to print the plan without writing anything. No LLM calls -- pure SQL/Python.

Run with the same ``DATABASE_URL`` as the app, e.g.:

    PYTHONPATH=agent DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \\
        python scripts/repair_watchlist_strict_aliases.py [--dry-run]
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
from eoa.config import settings  # noqa: E402
from eoa.pipeline.entity_normalize import find_watchlist_aliases_in_text  # noqa: E402


def _strict_watchlist_company_names() -> set[str]:
    """Canonical names of every watchlist company/program that owns at least one
    ``strict_aliases`` entry -- only these need re-checking; a company with no strict aliases was
    never affected by this bug."""
    wl = settings().watchlist or {}
    names: set[str] = set()
    for records in (wl.get("companies") or [], wl.get("programs") or []):
        for rec in records:
            if rec.get("strict_aliases"):
                names.add(rec.get("name", ""))
    return {n for n in names if n}


def _fetch_candidate_items(company_names: set[str]) -> list[dict[str, Any]]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, clean_text, raw_text, entities_mentioned
            FROM items
            WHERE entities_mentioned && %(names)s
            ORDER BY id
            """,
            {"names": list(company_names)},
        )
        return cur.fetchall()


def _fetch_events_for_item(item_id: int) -> list[dict[str, Any]]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, parties, customer, program, summary_he FROM events WHERE item_id = %(item_id)s",
            {"item_id": item_id},
        )
        return cur.fetchall()


def _item_text(item: dict[str, Any]) -> str:
    body = item.get("clean_text") or item.get("raw_text") or ""
    title = item.get("title") or ""
    return f"{body} {title}".strip()


def repair(*, dry_run: bool = False) -> dict[str, Any]:
    company_names = _strict_watchlist_company_names()
    items_fixed: list[dict[str, Any]] = []
    events_fixed: list[dict[str, Any]] = []

    if not company_names:
        return {"strict_companies": [], "items_fixed": items_fixed, "events_fixed": events_fixed}

    for item in _fetch_candidate_items(company_names):
        text = _item_text(item)
        still_valid = set(find_watchlist_aliases_in_text(text))
        before = item.get("entities_mentioned") or []
        false_names = {n for n in before if n in company_names and n not in still_valid}
        if not false_names:
            continue
        after = [n for n in before if n not in false_names]
        items_fixed.append(
            {"id": item["id"], "before": before, "after": after, "removed": sorted(false_names)}
        )
        if not dry_run:
            with db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE items SET entities_mentioned = %(names)s WHERE id = %(id)s",
                    {"names": after, "id": item["id"]},
                )

        # events.parties/customer/program for this item: same false names, checked against the
        # event's own summary text too (an event can rephrase/extract differently than the item).
        for ev in _fetch_events_for_item(item["id"]):
            ev_text = f"{text} {ev.get('summary_he') or ''}"
            ev_still_valid = set(find_watchlist_aliases_in_text(ev_text))
            ev_false = false_names - ev_still_valid
            if not ev_false:
                continue
            parties_before = ev.get("parties") or []
            parties_after = [p for p in parties_before if p not in ev_false]
            customer_before = ev.get("customer")
            customer_after = None if customer_before in ev_false else customer_before
            program_before = ev.get("program")
            program_after = None if program_before in ev_false else program_before
            if (
                parties_after == parties_before
                and customer_after == customer_before
                and program_after == program_before
            ):
                continue
            events_fixed.append(
                {
                    "id": ev["id"],
                    "item_id": item["id"],
                    "removed": sorted(ev_false),
                    "parties_before": parties_before,
                    "parties_after": parties_after,
                    "customer_before": customer_before,
                    "customer_after": customer_after,
                    "program_before": program_before,
                    "program_after": program_after,
                }
            )
            if not dry_run:
                with db.connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE events SET parties = %(parties)s, customer = %(customer)s, "
                        "program = %(program)s WHERE id = %(id)s",
                        {
                            "parties": parties_after,
                            "customer": customer_after,
                            "program": program_after,
                            "id": ev["id"],
                        },
                    )

    return {
        "strict_companies": sorted(company_names),
        "items_fixed": items_fixed,
        "events_fixed": events_fixed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report the plan without writing it")
    args = parser.parse_args()

    report = repair(dry_run=args.dry_run)

    print(
        f"{'=' * 70}\nRound-2 watchlist strict-alias repair {'(DRY RUN)' if args.dry_run else '(APPLIED)'}\n{'=' * 70}"
    )
    print(f"\nwatchlist companies with strict_aliases: {report['strict_companies']}")

    print(f"\nitems.entities_mentioned fixed: {len(report['items_fixed'])}")
    for r in report["items_fixed"][:30]:
        print(f"  item={r['id']} removed={r['removed']} {r['before']} -> {r['after']}")

    print(f"\nevents fixed: {len(report['events_fixed'])}")
    for r in report["events_fixed"][:30]:
        print(
            f"  event={r['id']} item={r['item_id']} removed={r['removed']} "
            f"parties {r['parties_before']} -> {r['parties_after']} "
            f"customer {r['customer_before']!r} -> {r['customer_after']!r} "
            f"program {r['program_before']!r} -> {r['program_after']!r}"
        )

    print(f"\n{'=' * 70}")
    if args.dry_run:
        print("(dry run -- nothing written; re-run without --dry-run to apply)")


if __name__ == "__main__":
    main()
