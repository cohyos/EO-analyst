#!/usr/bin/env python
"""R9-reports repair script (round-9 QA loop, closing round-8 judge findings D9 #6/#10 --
docs/qa/loop/round_8_judge.md, worst-list items #6/#10).

Two independent, idempotent repairs over ``tenders``, each dry-run by default (pass ``--apply`` to
actually write) -- print either way, so a dry run's report is directly comparable to the applied
run's own counts, same convention as ``scripts/backfill_product_lines.py``.

1. **Product-line tagging** (:func:`tag_missing_product_lines`, round-8 judge D9 #6: "the one open
   TED tender [id 42] itself carries no product_lines tag"). Any tender whose ``product_lines`` is
   still empty (``'{}'``) is re-tagged with the same deterministic (no LLM, no network)
   ``eoa.product_lines.tagging.tag_product_lines`` call ``eoa.tenders.scan._insert_tender_and_item``
   now runs at intake (R9-reports #3a, closing this same finding for every *future* notice) --
   title + description (``summary``/``summary_he``) + CPV codes as ``text_en``/``text_he``, unioned
   with the linked item's own tags (``tenders.item_id``) when one exists, mirroring
   ``scripts/backfill_product_lines.py``'s own ``_tenders_pass``. Covers every tender inserted
   *before* that scan.py fix shipped (id 42 included).

2. **Unknown-status resolution** (:func:`resolve_unknown_status`, round-8 judge D9 #10: "tender id
   38 still carries a non-standard status='unknown'"). ``status='unknown'`` (``_initial_status``'s
   own rubric: no ``deadline`` AND no ``published_at`` at all -- no date evidence either way, see
   ``eoa.tenders.scan``'s module docstring) is not itself wrong, but it is a dead end: nothing in
   the pipeline ever revisits it without an actual date. This repair borrows the *same* relevance
   gate + learned threshold the W2b operator-feedback loop already uses to decide
   ``intake='accepted'`` vs ``'candidate'`` at insert time (``eoa.tenders.feedback
   .get_relevance_threshold``) to give a date-less tender a real disposition instead of leaving it
   stuck: ``relevance_score >= threshold`` -> ``status='open'`` (accepted into the active queue
   despite the missing dates -- the notice is topically relevant enough to treat as live); otherwise
   -> ``status='archived'`` (the same terminal status ``eoa.tenders.scan._archive_stale_closed``
   already uses for stale/rejected content elsewhere in this module -- dropped out of the active
   queue, never deleted). Every decision is logged (``tender_status_resolved``, structlog) with the
   exact relevance_score/threshold that drove it. Deliberately never touches ``tender_feedback`` --
   this is a deterministic content-relevance repair over the tender's own already-stored
   ``relevance_score``, not a substitute for real operator 👍/👎 feedback.

Both repairs read/write only ``tenders`` (plus a read-only ``items`` lookup for repair 1's
item-tag union) -- neither touches ``tender_feedback``. Verify any ``--apply`` run from a *separate*
connection afterward (standing round-9 rule) -- see this round's "### R9-reports status" section in
docs/qa/loop/round_9_fixes.md for the actual before/after counts.

Usage:
    set -a; . runtime/eoa.env; set +a
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round9.py [--apply] \
        [--limit N] [--skip-tagging] [--skip-status]
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import structlog

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

log = structlog.get_logger(__name__)


def tag_missing_product_lines(*, apply: bool = False, limit: int | None = None) -> list[dict[str, Any]]:
    """Round-8 judge D9 #6. Returns one entry per tender that got (or, dry-run, would get) a
    non-empty ``product_lines`` tag -- a tender that genuinely matches none of the six configured
    product lines even after this pass is correctly left untagged (never invented)."""
    from eoa.db import connection
    from eoa.product_lines.tagging import tag_product_lines

    results: list[dict[str, Any]] = []
    with connection() as conn, conn.cursor() as cur:
        sql = (
            "SELECT id, title, summary_he, cpv_naics, entities, item_id FROM tenders "
            "WHERE product_lines = '{}' ORDER BY id"
        )
        params: dict[str, Any] = {}
        if limit:
            sql += " LIMIT %(limit)s"
            params["limit"] = limit
        cur.execute(sql, params)
        rows = cur.fetchall()

        item_tags: dict[int, list[str]] = {}
        item_ids = [r["item_id"] for r in rows if r.get("item_id") is not None]
        if item_ids:
            cur.execute("SELECT id, product_lines FROM items WHERE id = ANY(%(ids)s)", {"ids": item_ids})
            item_tags = {r["id"]: (r.get("product_lines") or []) for r in cur.fetchall()}

        for row in rows:
            cpv_text = " ".join(row.get("cpv_naics") or [])
            text_en = " ".join(filter(None, [row.get("title"), cpv_text]))
            lines = set(
                tag_product_lines(
                    text_he=row.get("summary_he"),
                    text_en=text_en,
                    entities=row.get("entities") or [],
                    subdomain=None,
                )
            )
            lines |= set(item_tags.get(row.get("item_id")) or [])
            if not lines:
                continue
            lines_list = sorted(lines)
            results.append({"id": row["id"], "title": row.get("title"), "product_lines": lines_list})
            if apply:
                cur.execute(
                    "UPDATE tenders SET product_lines = %(lines)s, updated_at = now() WHERE id = %(id)s",
                    {"lines": lines_list, "id": row["id"]},
                )
    return results


def resolve_unknown_status(*, apply: bool = False, limit: int | None = None) -> list[dict[str, Any]]:
    """Round-8 judge D9 #10. Returns one entry per ``status='unknown'`` tender resolved (or, dry-run,
    that would be resolved) to ``'open'``/``'archived'`` via the learned relevance threshold."""
    from eoa.db import connection
    from eoa.tenders.feedback import get_relevance_threshold

    threshold = get_relevance_threshold()
    results: list[dict[str, Any]] = []
    with connection() as conn, conn.cursor() as cur:
        sql = "SELECT id, title, relevance_score FROM tenders WHERE status = 'unknown' ORDER BY id"
        params: dict[str, Any] = {}
        if limit:
            sql += " LIMIT %(limit)s"
            params["limit"] = limit
        cur.execute(sql, params)
        rows = cur.fetchall()

        for row in rows:
            raw_score = row.get("relevance_score")
            # Same "LLM unavailable -> neutral" baseline eoa.tenders.scan uses at insert time
            # (_RELEVANCE_SCORE_WHEN_LLM_UNAVAILABLE) when a row somehow carries no score at all.
            score = float(raw_score) if raw_score is not None else 0.5
            new_status = "open" if score >= threshold else "archived"
            comparator = ">=" if new_status == "open" else "<"
            reason = f"relevance_score={score:.2f} {comparator} learned_threshold={threshold:.2f}"
            results.append(
                {
                    "id": row["id"],
                    "title": row.get("title"),
                    "relevance_score": score,
                    "threshold": threshold,
                    "new_status": new_status,
                    "reason": reason,
                }
            )
            log.info(
                "tender_unknown_status_resolved",
                tender_id=row["id"],
                relevance_score=score,
                threshold=threshold,
                new_status=new_status,
                reason=reason,
                apply=apply,
            )
            if apply:
                cur.execute(
                    "UPDATE tenders SET status = %(status)s, updated_at = now() WHERE id = %(id)s",
                    {"status": new_status, "id": row["id"]},
                )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply", action="store_true", help="write the computed repairs (default: dry run, report only)"
    )
    parser.add_argument("--limit", type=int, default=None, help="cap rows processed per repair (debug only)")
    parser.add_argument(
        "--skip-tagging", action="store_true", help="skip the missing-product-line-tag repair"
    )
    parser.add_argument(
        "--skip-status", action="store_true", help="skip the status='unknown' resolution repair"
    )
    args = parser.parse_args()
    apply = args.apply

    print("APPLIED" if apply else "DRY RUN (pass --apply to write)")
    print("=" * 70)

    if not args.skip_tagging:
        tagged = tag_missing_product_lines(apply=apply, limit=args.limit)
        verb = "tagged" if apply else "would be tagged"
        print(f"product-line tagging: {len(tagged)} tender(s) {verb}")
        for r in tagged:
            title = (r["title"] or "")[:60]
            print(f"  #{r['id']:<6d} {', '.join(r['product_lines']):30s} {title}")
        print("-" * 70)

    if not args.skip_status:
        resolved = resolve_unknown_status(apply=apply, limit=args.limit)
        verb = "resolved" if apply else "would be resolved"
        print(f"status='unknown' resolution: {len(resolved)} tender(s) {verb}")
        for r in resolved:
            title = (r["title"] or "")[:50]
            print(f"  #{r['id']:<6d} -> {r['new_status']:<9s} ({r['reason']}) {title}")
        print("-" * 70)

    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
