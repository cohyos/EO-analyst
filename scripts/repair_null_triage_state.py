#!/usr/bin/env python
"""Round-2 repair (docs/qa/loop/round_0_judge.md item 9, D1): 4/40 golden items had
``level``/``score``/``triage_reason`` all NULL despite ``processed_stages`` recording ``'triage'``
as done -- a silent triage failure, inconsistent with the rest of the corpus (every other
``out_of_scope`` item has ``level='archive'``, ``score=1`` and a real reason). A live re-check
(2026-09-06) found two distinct patterns and root causes:

- **All three NULL** (the judge's exact finding, e.g. items 1/4/7/14): ``processed_stages`` never
  reached ``'analyze'`` -- the item was marked ``'triage'``-done without ever actually being
  scored. Since ``domain='out_of_scope'`` for every one of these, the fix is the same deterministic
  rule ``eoa.pipeline.classify.run_classify`` already applies to every other out_of_scope item
  (never an LLM call): ``level='archive'``, ``score=1``.
- **Only ``level`` NULL, ``score``/``triage_reason`` stale** (e.g. items 177/263/.../5604, 25 more
  found live): caused by ``scripts/backfill_analysis_gaps.py``'s ``stub_cleanup_pass`` clearing
  ``level`` (and setting ``domain='out_of_scope'``) when discarding an untrustworthy stub analysis,
  without also invalidating the ``score``/``triage_reason`` that stub analysis had produced --
  fixed at the source in this round (see that script's own docstring/code); this script is the
  one-off backfill for rows already written before that fix landed. Same target state: matches the
  stale ``score``/``triage_reason`` up to the same ``level='archive'``, ``score=1`` convention
  (the stale score/reason are discarded too, since they were derived from the same untrustworthy
  stub content the level reset was already discarding).

Both patterns converge on the exact same target state, so one pass handles both:
``domain='out_of_scope' AND level IS NULL`` -> ``level='archive', score=1,
triage_reason='gate:stub_content_cleared_non_defensible'`` (reusing the marker
``eoa.pipeline.classify``'s own deterministic gates use, e.g. ``'gate:no_eoir_vocabulary'``) unless
a ``triage_reason`` is already present, in which case only ``level``/``score`` are set (the
existing reason is kept as an audit trail of why it was originally scored, even though the level is
now the deterministic out_of_scope archive).

No LLM calls -- pure SQL. Use ``--dry-run`` to print the plan without writing anything.

Run with the same ``DATABASE_URL`` as the app, e.g.:

    PYTHONPATH=agent DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \\
        python scripts/repair_null_triage_state.py [--dry-run]
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

_DEFAULT_REASON = "gate:stub_content_cleared_non_defensible"


def _fetch_candidates() -> list[dict[str, Any]]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, domain, level, score, triage_reason
            FROM items
            WHERE domain = 'out_of_scope' AND level IS NULL
            ORDER BY id
            """
        )
        return cur.fetchall()


def repair(*, dry_run: bool = False) -> dict[str, Any]:
    candidates = _fetch_candidates()
    fixed: list[dict[str, Any]] = []
    for row in candidates:
        before = {"level": row["level"], "score": row["score"], "triage_reason": row["triage_reason"]}
        after = {
            "level": "archive",
            "score": row["score"] if row["score"] is not None else 1,
            "triage_reason": row["triage_reason"] or _DEFAULT_REASON,
        }
        fixed.append({"id": row["id"], "before": before, "after": after})
        if dry_run:
            continue
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE items SET level = %(level)s, score = %(score)s, triage_reason = %(reason)s "
                "WHERE id = %(id)s",
                {
                    "level": after["level"],
                    "score": after["score"],
                    "reason": after["triage_reason"],
                    "id": row["id"],
                },
            )
    return {"count": len(fixed), "fixed": fixed}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report the plan without writing it")
    args = parser.parse_args()

    report = repair(dry_run=args.dry_run)

    print(
        f"{'=' * 70}\nRound-2 NULL-triage-state repair {'(DRY RUN)' if args.dry_run else '(APPLIED)'}\n{'=' * 70}"
    )
    print(f"\nitems fixed: {report['count']}")
    for r in report["fixed"]:
        print(f"  item={r['id']} {r['before']} -> {r['after']}")

    print(f"\n{'=' * 70}")
    if args.dry_run:
        print("(dry run -- nothing written; re-run without --dry-run to apply)")


if __name__ == "__main__":
    main()
