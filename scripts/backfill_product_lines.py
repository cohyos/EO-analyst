#!/usr/bin/env python
"""PL-backend (user request 2026-09-07): one-off backfill of ``product_lines`` (migration 0027)
over every existing row in ``items``, ``events``, ``tenders``, ``tender_forecasts`` and ``patents``
-- the ``eoa.pipeline.analyze.persist_analysis`` tagging hook only ever runs for items processed
*after* this feature shipped; this script computes the same deterministic (no LLM)
``eoa.product_lines.tagging.tag_product_lines`` tags for everything that already exists.

Five independent, idempotent, no-LLM sweeps:

1. **Items**: ``title``/``summary_he``/``so_what_he`` + ``entities_mentioned`` + ``subdomain``.
2. **Events**: inherits its parent item's own (freshly recomputed) ``product_lines`` -- an event has
   no ``domain``/``subdomain`` of its own; it was extracted from the same text as its item, so it
   shares that item's product-line tags (same convention the live pipeline hook uses).
3. **Tenders**: ``title``/``summary_he`` + ``entities``, unioned with the linked item's tags
   (``tenders.item_id``) when there is one.
4. **Tender forecasts**: ``platform``/``payload_need`` + ``candidate_vendors``.
5. **Patents**: ``title``/``abstract``/``claims_summary_he``/``so_what_he`` + ``assignees`` +
   ``subdomain``.

Default is a dry run (report only, no writes) -- pass ``--apply`` to actually write. Prints
per-product-line counts either way, so a dry run's report is directly comparable to the applied
run's own counts.

**Optional sixth sweep (R8-tagging, 2026-09-07): ``--llm``**. Runs *after* the items sweep, over
in-scope items (``level IN ('red','orange','yellow')``, ``security_status = 'clean'``,
``dedup_of IS NULL``, published/fetched/created within ``--llm-since-days`` days, default 90) that
the deterministic pass above still left untagged -- one
``eoa.product_lines.llm_tagging.llm_tag_batch`` call per ``BATCH_SIZE``-sized chunk (~15 items),
capped at ``--llm-budget`` calls total (default 40) so a large untagged backlog can't blow the
pipeline's LLM budget. Its results are merged into the same ``by_item_id`` map the deterministic
items sweep built, so the events/tenders sweeps below (which inherit an item's tags) see the
LLM-assisted tags too, not just the deterministic ones. No-op (0 calls) unless ``--llm`` is passed,
regardless of ``config/product_lines.yaml``'s own ``llm_tagging`` switch (that switch gates the
*live pipeline hook* in ``eoa.pipeline.analyze``, not this one-off script -- an operator running
this script explicitly opted in via the flag).

Usage:
    DATABASE_URL=postgresql://eoa@127.0.0.1:5432/eoanalyst \
    PYTHONPATH=agent python scripts/backfill_product_lines.py [--apply] [--limit N] \
        [--llm] [--llm-budget 40] [--llm-since-days 90]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, "agent")


def _items_pass(*, limit: int | None = None, apply: bool = False) -> tuple[Counter, dict[int, list[str]]]:
    from eoa.db import connection
    from eoa.memory.relational import update_item_fields
    from eoa.product_lines.tagging import tag_product_lines

    with connection() as conn, conn.cursor() as cur:
        query = (
            "SELECT id, title, summary_he, so_what_he, entities_mentioned, subdomain FROM items ORDER BY id"
        )
        if limit:
            query += f" LIMIT {int(limit)}"
        cur.execute(query)
        rows = cur.fetchall()

    counts: Counter = Counter()
    by_item_id: dict[int, list[str]] = {}
    for row in rows:
        text_he = " ".join(filter(None, [row.get("summary_he"), row.get("so_what_he")]))
        lines = tag_product_lines(
            text_he=text_he,
            text_en=row.get("title"),
            entities=row.get("entities_mentioned") or [],
            subdomain=row.get("subdomain"),
        )
        by_item_id[row["id"]] = lines
        for line_id in lines:
            counts[line_id] += 1
        if apply and lines:
            update_item_fields(row["id"], product_lines=lines)
    return counts, by_item_id


def _llm_pass(
    by_item_id: dict[int, list[str]],
    *,
    apply: bool = False,
    since_days: int = 90,
    budget: int = 40,
) -> tuple[Counter, int]:
    """R8-tagging (2026-09-07): LLM-assisted fallback over in-scope items the deterministic
    ``_items_pass`` above left untagged (``by_item_id[id] == []``). Mutates ``by_item_id`` in place
    (merges any LLM-assisted tags in) so the events/tenders sweeps that run after this one inherit
    them too. Returns ``(per-line counts, calls used)`` -- ``calls used`` is always
    ``<= budget`` regardless of how many candidate items there were."""
    from eoa.db import connection
    from eoa.memory.relational import update_item_fields
    from eoa.product_lines.llm_tagging import BATCH_SIZE, llm_tag_batch

    candidate_ids = sorted(iid for iid, lines in by_item_id.items() if not lines)
    counts: Counter = Counter()
    calls = 0
    if not candidate_ids:
        return counts, calls

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, summary_he FROM items
            WHERE id = ANY(%(ids)s)
              AND level IN ('red', 'orange', 'yellow')
              AND security_status = 'clean' AND dedup_of IS NULL
              AND COALESCE(published_at, fetched_at, created_at) >= now() - (%(since_days)s || ' days')::interval
            ORDER BY id
            """,
            {"ids": candidate_ids, "since_days": since_days},
        )
        rows = cur.fetchall()

    for i in range(0, len(rows), BATCH_SIZE):
        if calls >= budget:
            print(
                f"LLM budget ({budget} calls) reached -- {len(rows) - i} in-scope untagged item(s) "
                "left unprocessed this run"
            )
            break
        chunk = rows[i : i + BATCH_SIZE]
        result = llm_tag_batch(chunk)
        calls += 1
        for item_id, lines in result.items():
            merged = sorted(set(by_item_id.get(item_id) or []) | set(lines))
            by_item_id[item_id] = merged
            for line_id in lines:
                counts[line_id] += 1
            if apply:
                update_item_fields(item_id, product_lines=merged)
    return counts, calls


def _events_pass(by_item_id: dict[int, list[str]], *, apply: bool = False) -> Counter:
    from eoa.db import connection

    counts: Counter = Counter()
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, item_id FROM events ORDER BY id")
        rows = cur.fetchall()
        for row in rows:
            lines = by_item_id.get(row.get("item_id")) or []
            for line_id in lines:
                counts[line_id] += 1
            if apply and lines:
                cur.execute("UPDATE events SET product_lines = %s WHERE id = %s", (lines, row["id"]))
    return counts


def _tenders_pass(by_item_id: dict[int, list[str]], *, apply: bool = False) -> Counter:
    from eoa.db import connection
    from eoa.product_lines.tagging import tag_product_lines

    counts: Counter = Counter()
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, title, summary_he, entities, item_id FROM tenders ORDER BY id")
        rows = cur.fetchall()
        for row in rows:
            lines = set(
                tag_product_lines(
                    text_he=row.get("summary_he"),
                    text_en=row.get("title"),
                    entities=row.get("entities") or [],
                    subdomain=None,
                )
            )
            lines |= set(by_item_id.get(row.get("item_id")) or [])
            lines_list = sorted(lines)
            for line_id in lines_list:
                counts[line_id] += 1
            if apply and lines_list:
                cur.execute("UPDATE tenders SET product_lines = %s WHERE id = %s", (lines_list, row["id"]))
    return counts


def _forecasts_pass(*, apply: bool = False) -> Counter:
    from eoa.db import connection
    from eoa.product_lines.tagging import tag_product_lines

    counts: Counter = Counter()
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, platform, payload_need, candidate_vendors FROM tender_forecasts ORDER BY id")
        rows = cur.fetchall()
        for row in rows:
            text_en = " ".join(filter(None, [row.get("platform"), row.get("payload_need")]))
            lines = tag_product_lines(
                text_he=None, text_en=text_en, entities=row.get("candidate_vendors") or [], subdomain=None
            )
            for line_id in lines:
                counts[line_id] += 1
            if apply and lines:
                cur.execute(
                    "UPDATE tender_forecasts SET product_lines = %s WHERE id = %s", (lines, row["id"])
                )
    return counts


def _patents_pass(*, apply: bool = False) -> Counter:
    from eoa.db import connection
    from eoa.product_lines.tagging import tag_product_lines

    counts: Counter = Counter()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, abstract, claims_summary_he, so_what_he, assignees, subdomain "
            "FROM patents ORDER BY id"
        )
        rows = cur.fetchall()
        for row in rows:
            text_he = " ".join(filter(None, [row.get("claims_summary_he"), row.get("so_what_he")]))
            text_en = " ".join(filter(None, [row.get("title"), row.get("abstract")]))
            lines = tag_product_lines(
                text_he=text_he,
                text_en=text_en,
                entities=row.get("assignees") or [],
                subdomain=row.get("subdomain"),
            )
            for line_id in lines:
                counts[line_id] += 1
            if apply and lines:
                cur.execute("UPDATE patents SET product_lines = %s WHERE id = %s", (lines, row["id"]))
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply", action="store_true", help="write the computed tags (default: dry run, report only)"
    )
    parser.add_argument("--limit", type=int, default=None, help="cap the items pass (debug only)")
    parser.add_argument(
        "--llm",
        action="store_true",
        help=(
            "after the deterministic items pass, run eoa.product_lines.llm_tagging over in-scope "
            "still-untagged items (see this script's own docstring) -- costs real LLM calls"
        ),
    )
    parser.add_argument(
        "--llm-budget", type=int, default=40, help="max LLM batch calls for --llm (default: 40)"
    )
    parser.add_argument(
        "--llm-since-days", type=int, default=90, help="--llm candidate window in days (default: 90)"
    )
    args = parser.parse_args()
    apply = args.apply

    items_counts, by_item_id = _items_pass(limit=args.limit, apply=apply)
    llm_counts: Counter = Counter()
    llm_calls = 0
    if args.llm:
        llm_counts, llm_calls = _llm_pass(
            by_item_id, apply=apply, since_days=args.llm_since_days, budget=args.llm_budget
        )
        for line_id, n in llm_counts.items():
            items_counts[line_id] += n
    events_counts = _events_pass(by_item_id, apply=apply)
    tenders_counts = _tenders_pass(by_item_id, apply=apply)
    forecasts_counts = _forecasts_pass(apply=apply)
    patents_counts = _patents_pass(apply=apply)

    print("APPLIED" if apply else "DRY RUN (pass --apply to write)")
    print("=" * 70)
    all_line_ids = sorted(
        set(items_counts)
        | set(events_counts)
        | set(tenders_counts)
        | set(forecasts_counts)
        | set(patents_counts)
    )
    for line_id in all_line_ids:
        print(
            f"{line_id:24s} items={items_counts.get(line_id, 0):5d} events={events_counts.get(line_id, 0):5d} "
            f"tenders={tenders_counts.get(line_id, 0):5d} forecasts={forecasts_counts.get(line_id, 0):5d} "
            f"patents={patents_counts.get(line_id, 0):5d}"
        )
    print("=" * 70)
    print(f"items scanned: {len(by_item_id)}, items tagged: {sum(1 for v in by_item_id.values() if v)}")
    if args.llm:
        print(
            f"LLM pass: {llm_calls} call(s) used (budget {args.llm_budget}), {sum(llm_counts.values())} tag(s) added"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
