#!/usr/bin/env python
"""Q3-8/Q3-9/Q3-10 (docs/qa/findings_Q3_r2.md) repair: backfill the analysis gaps left by r1's
fixes -- ``entities_mentioned`` still empty in ~90% of classified items, ``key_facts`` still empty
in ~83%, and 'stub'-content items that were fully analyzed *before* the Q3-10 content-quality gate
existed still carry that (unreliable) analysis.

Three independent passes:

1. **Deterministic entities backfill** (``--deterministic``, no LLM): for every item with a
   non-null ``level`` (i.e. reached classify) and empty ``entities_mentioned``, matches
   ``eoa.pipeline.entity_normalize.find_watchlist_aliases_in_text`` against ``title`` + first
   ``analyze.MAX_CHARS`` of ``clean_text`` -- the exact same helper ``analyze.persist_analysis``
   already applies to *new* items (Q3-8 r1), just swept once over every *existing* one. Free,
   instant, safe to re-run.

2. **LLM re-analyze pass** (``--llm``, gated, capped by ``--limit``): for items with
   ``level in (red, orange, yellow)``, ``content_status != 'stub'`` (stub items are pass 3's job,
   not this one's -- their text isn't trustworthy enough to re-analyze), and still-empty
   ``key_facts`` or ``entities_mentioned`` after pass 1, re-runs the real
   ``eoa.pipeline.analyze.analyze_item`` + ``persist_analysis`` (the same functions the normal
   pipeline uses -- no separate "fields-only" extraction path; the analyze prompt/schema always
   emits summary/so_what/key_facts/entities/events/edges together, so a partial re-run buys
   nothing over the real one) against the model's own ``resident`` role, red/orange first
   (most operationally important). Every call goes through
   ``eoa.resources.gate`` exactly as the normal pipeline does -- this script adds no separate
   concurrency control beyond that shared gate. Stops early (does not raise) on
   ``ResourceUnavailable`` -- the gate is telling every caller, including the normal night
   pipeline, that the host is busy right now, and this backfill is opportunistic, not
   time-critical. Continues past a single-item ``LLMOutputError``, counting it as a failure.

3. **Pre-gate stub cleanup** (``--stub-cleanup``, no LLM): a ``content_status='stub'`` item that
   still carries ``summary_he``/``so_what_he``/``key_facts`` was fully analyzed *before* the Q3-10
   content-quality gate (``analyze.run_analyze``'s ``_content_status_precheck``) existed to skip
   it -- that analysis was extracted from near-empty/blocked text and is not trustworthy. Clears
   ``summary_he``/``so_what_he``/``key_facts`` unconditionally. ``level``/``domain`` are reset to
   ``NULL``/``'out_of_scope'`` too, *unless* a title-only classification is defensible: the title
   itself names a recognised watchlist/curated-org entity (``find_watchlist_aliases_in_text``) and
   the item already has both a ``level`` and a ``domain`` on record -- in that case the
   triage-level classification (which only ever needed the title/summary, not full body text) is
   left as-is, only the deep-analysis text fields are cleared.

Usage:
    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \
    PYTHONPATH=agent python scripts/backfill_analysis_gaps.py --deterministic [--dry-run]
    PYTHONPATH=agent python scripts/backfill_analysis_gaps.py --llm --limit 40 [--dry-run]
    PYTHONPATH=agent python scripts/backfill_analysis_gaps.py --stub-cleanup [--dry-run]
    PYTHONPATH=agent python scripts/backfill_analysis_gaps.py --all --limit 40 [--dry-run]
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

_LEVEL_ORDER = {"red": 0, "orange": 1, "yellow": 2}


# --------------------------------------------------------------------------
# pass 1: deterministic entities backfill (Q3-8)
# --------------------------------------------------------------------------


def deterministic_entities_backfill(*, dry_run: bool = False) -> dict[str, Any]:
    from eoa.db import connection
    from eoa.memory.relational import update_item_fields
    from eoa.pipeline.analyze import MAX_CHARS
    from eoa.pipeline.entity_normalize import find_watchlist_aliases_in_text

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, clean_text FROM items "
            "WHERE level IS NOT NULL AND (entities_mentioned IS NULL OR entities_mentioned = '{}') "
            "ORDER BY id"
        )
        rows = cur.fetchall()

    filled: list[dict[str, Any]] = []
    still_empty = 0
    for row in rows:
        text = " ".join(filter(None, [row.get("title"), (row.get("clean_text") or "")[:MAX_CHARS]]))
        matched = find_watchlist_aliases_in_text(text)
        if not matched:
            still_empty += 1
            continue
        filled.append({"id": row["id"], "entities_mentioned": matched})
        if not dry_run:
            update_item_fields(row["id"], entities_mentioned=matched)

    return {
        "candidates": len(rows),
        "filled": filled,
        "still_empty_after": still_empty,
    }


# --------------------------------------------------------------------------
# pass 2: LLM re-analyze (Q3-8/Q3-9)
# --------------------------------------------------------------------------


def _llm_candidates(limit: int) -> list[dict[str, Any]]:
    from eoa.db import connection

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM items
            WHERE level IN ('red', 'orange', 'yellow')
              AND COALESCE(content_status, 'full') <> 'stub'
              AND security_status = 'clean'
              AND dedup_of IS NULL
              AND (
                    key_facts IS NULL OR key_facts = '{}'
                    OR entities_mentioned IS NULL OR entities_mentioned = '{}'
              )
            ORDER BY
                CASE level WHEN 'red' THEN 0 WHEN 'orange' THEN 1 WHEN 'yellow' THEN 2 ELSE 3 END,
                published_at DESC NULLS LAST
            LIMIT %(limit)s
            """,
            {"limit": limit},
        )
        return cur.fetchall()


def llm_reanalyze_pass(*, limit: int = 60, role: str = "resident", dry_run: bool = False) -> dict[str, Any]:
    from eoa.errors import LLMOutputError, ResourceUnavailable
    from eoa.pipeline.analyze import analyze_item, persist_analysis

    candidates = _llm_candidates(limit)
    done: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    deferred = False

    for it in candidates:
        if dry_run:
            done.append({"id": it["id"], "level": it["level"], "title": (it.get("title") or "")[:80]})
            continue
        try:
            out = analyze_item(it, role=role)
        except ResourceUnavailable as exc:
            log.warning("backfill_llm_pass_deferred_resources", item_id=it["id"], error=str(exc)[:160])
            deferred = True
            break
        except LLMOutputError as exc:
            log.error("backfill_llm_pass_bad_output", item_id=it["id"], error=str(exc)[:200])
            failed.append({"id": it["id"], "error": str(exc)[:200]})
            continue
        except Exception as exc:  # never let one bad item stop the whole backfill
            log.error("backfill_llm_pass_unexpected_error", item_id=it["id"], error=str(exc)[:200])
            failed.append({"id": it["id"], "error": str(exc)[:200]})
            continue
        try:
            persist_analysis(it, out)
        except Exception as exc:
            log.error("backfill_llm_pass_persist_failed", item_id=it["id"], error=str(exc)[:200])
            failed.append({"id": it["id"], "error": str(exc)[:200]})
            continue
        done.append({"id": it["id"], "level": it["level"], "title": (it.get("title") or "")[:80]})

    return {
        "candidates": len(candidates),
        "done": done,
        "failed": failed,
        "deferred_resources": deferred,
    }


# --------------------------------------------------------------------------
# pass 3: pre-gate stub cleanup (Q3-10)
# --------------------------------------------------------------------------


def _title_only_classification_defensible(item: dict[str, Any]) -> bool:
    """True when `item`'s existing level/domain classification is defensible from its title
    alone -- the title names a recognised watchlist/curated-org entity, and a level/domain are
    already on record (triage only ever needed the title, not the stub body text)."""
    from eoa.pipeline.entity_normalize import find_watchlist_aliases_in_text

    if not item.get("level") or not item.get("domain"):
        return False
    return bool(find_watchlist_aliases_in_text(item.get("title") or ""))


def stub_cleanup_pass(*, dry_run: bool = False) -> dict[str, Any]:
    from eoa.db import connection
    from eoa.memory.relational import update_item_fields

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, level, domain FROM items
            WHERE content_status = 'stub'
              AND (
                    summary_he IS NOT NULL OR so_what_he IS NOT NULL
                    OR (key_facts IS NOT NULL AND key_facts <> '{}')
              )
            ORDER BY id
            """
        )
        rows = cur.fetchall()

    cleared: list[dict[str, Any]] = []
    for row in rows:
        defensible = _title_only_classification_defensible(row)
        fields: dict[str, Any] = {"summary_he": None, "so_what_he": None, "key_facts": None}
        if not defensible:
            fields["level"] = None
            fields["domain"] = "out_of_scope"
        cleared.append(
            {"id": row["id"], "title": (row.get("title") or "")[:80], "title_only_defensible": defensible}
        )
        if not dry_run:
            update_item_fields(row["id"], **fields)

    return {"candidates": len(rows), "cleared": cleared}


# --------------------------------------------------------------------------
# pass 4: key_facts dedup backfill (D1 round-1 fix, docs/qa/loop/round_1_fixes.md)
# --------------------------------------------------------------------------


def key_facts_dedupe_backfill(*, dry_run: bool = False) -> dict[str, Any]:
    """Re-applies ``eoa.pipeline.analyze._dedupe_key_facts`` (extended in round 1 to be
    punctuation-insensitive and to collapse near-duplicates, ``difflib`` ratio >= 0.9) to every
    already-persisted ``items.key_facts`` array -- the analyze-stage fix only ever covers a
    *newly*-generated array; rows written before it existed (e.g. items 5/10/51, the
    ``key_facts_no_duplicates`` finding) keep their old duplicates until swept once here.
    Deterministic, no LLM call. Idempotent -- a clean row is left byte-identical."""
    from eoa.db import connection
    from eoa.memory.relational import update_item_fields
    from eoa.pipeline.analyze import _dedupe_key_facts

    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, key_facts FROM items WHERE key_facts IS NOT NULL AND key_facts <> '{}'")
        rows = cur.fetchall()

    repaired: list[dict[str, Any]] = []
    for row in rows:
        before = row["key_facts"] or []
        after = _dedupe_key_facts(before)
        if after == before:
            continue
        repaired.append({"id": row["id"], "before_n": len(before), "after_n": len(after)})
        if not dry_run:
            update_item_fields(row["id"], key_facts=after)

    return {"candidates": len(rows), "repaired": repaired}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--deterministic", action="store_true", help="run pass 1 (Q3-8 watchlist-alias backfill, no LLM)"
    )
    parser.add_argument("--llm", action="store_true", help="run pass 2 (Q3-8/Q3-9 LLM re-analyze, gated)")
    parser.add_argument(
        "--stub-cleanup", action="store_true", help="run pass 3 (Q3-10 pre-gate stub cleanup, no LLM)"
    )
    parser.add_argument(
        "--key-facts-dedupe",
        action="store_true",
        help="run pass 4 (D1 round-1 key_facts near-duplicate backfill, no LLM)",
    )
    parser.add_argument("--all", action="store_true", help="run all four passes in order")
    parser.add_argument("--limit", type=int, default=60, help="max items for the LLM pass (default 60)")
    parser.add_argument("--role", default="resident", help="model role for the LLM pass (default 'resident')")
    parser.add_argument("--dry-run", action="store_true", help="report counts/plan without writing")
    args = parser.parse_args()

    if not (args.deterministic or args.llm or args.stub_cleanup or args.key_facts_dedupe or args.all):
        parser.error("pass one of --deterministic / --llm / --stub-cleanup / --key-facts-dedupe / --all")

    mode = "DRY RUN" if args.dry_run else "APPLIED"
    print(f"\n{'=' * 70}\nQ3-8/Q3-9/Q3-10 analysis-gaps backfill ({mode})\n{'=' * 70}")

    if args.deterministic or args.all:
        report = deterministic_entities_backfill(dry_run=args.dry_run)
        print("\n[pass 1] deterministic entities backfill (Q3-8):")
        print(f"  candidates (empty entities_mentioned): {report['candidates']}")
        print(f"  filled:                                {len(report['filled'])}")
        print(f"  still empty (no watchlist match):      {report['still_empty_after']}")
        for r in report["filled"][:15]:
            print(f"    id={r['id']} entities_mentioned={r['entities_mentioned']}")

    if args.llm or args.all:
        report = llm_reanalyze_pass(limit=args.limit, role=args.role, dry_run=args.dry_run)
        print(f"\n[pass 2] LLM re-analyze (Q3-8/Q3-9), limit={args.limit}:")
        print(f"  candidates: {report['candidates']}")
        print(f"  done:       {len(report['done'])}")
        print(f"  failed:     {len(report['failed'])}")
        print(f"  deferred (resource gate busy): {report['deferred_resources']}")
        for r in report["done"][:15]:
            print(f"    id={r['id']} level={r['level']} title={r['title']!r}")
        for r in report["failed"][:15]:
            print(f"    FAILED id={r['id']}: {r['error']}")

    if args.stub_cleanup or args.all:
        report = stub_cleanup_pass(dry_run=args.dry_run)
        print("\n[pass 3] pre-gate stub cleanup (Q3-10):")
        print(f"  candidates: {report['candidates']}")
        kept = sum(1 for r in report["cleared"] if r["title_only_defensible"])
        print(f"  cleared (level/domain reset):      {len(report['cleared']) - kept}")
        print(f"  cleared (level/domain kept, title-defensible): {kept}")
        for r in report["cleared"][:20]:
            print(f"    id={r['id']} title_only_defensible={r['title_only_defensible']} title={r['title']!r}")

    if args.key_facts_dedupe or args.all:
        report = key_facts_dedupe_backfill(dry_run=args.dry_run)
        print("\n[pass 4] key_facts near-duplicate backfill (D1 round-1 fix, no LLM):")
        print(f"  candidates (non-empty key_facts): {report['candidates']}")
        print(f"  repaired:                          {len(report['repaired'])}")
        for r in report["repaired"][:20]:
            print(f"    id={r['id']} {r['before_n']} -> {r['after_n']} facts")

    print(f"\n{'=' * 70}")
    if args.dry_run:
        print("(dry run -- nothing written; re-run without --dry-run to apply)")
    else:
        print("Note to continue: re-run --llm with a higher --limit (or repeatedly) to work through")
        print("the remaining red/orange/yellow backlog; --deterministic and --stub-cleanup are")
        print("idempotent full sweeps and don't need repeating unless new items arrive.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
