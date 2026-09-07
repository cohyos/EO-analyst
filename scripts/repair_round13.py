#!/usr/bin/env python
"""Round-13 data repair (docs/qa/loop/round_12_judge.md worst-list #5 / D2-D1: "11 in-scope items
have so_what_he IS NULL despite completed analyze stage").

Live verification (this round) confirmed the exact count and the exact query shape the judge's own
phrasing implies: "in-scope" here means the report-facing severity tiers red/orange/yellow (not a
``domain <> 'out_of_scope'`` filter -- 9 of the 11 items actually carry ``domain = 'out_of_scope'``,
a separate, pre-existing level/domain mismatch this round does not touch), and "completed analyze
stage" is read loosely: every one of the 11 already has ``summary_he`` populated except items
5122/6872 (whose ``clean_text``/``title`` still give :func:`eoa.pipeline.analyze.repair_so_what_text`
enough to work from, same as the round-6 ``so_what`` subcommand's own template-phrase repairs did
for items with a populated but generic ``so_what_he``).

One subcommand:

    so_what   -- every item with ``level IN ('red','orange','yellow')`` and ``so_what_he IS NULL``,
                 re-generated through ``eoa.pipeline.analyze.repair_so_what_text`` on the cloud
                 chain (this script forces ``EOA_PIPELINE=1``, same as round-6's script), validated
                 (non-empty, starts with "להערכתנו", 1-3 Hebrew sentences, no
                 ``SO_WHAT_TEMPLATE_PHRASES_HE`` banned phrase) before being written. Defaults to a
                 dry run (list only); pass --apply to write. A shared --llm-budget (default 15, per
                 the round-13 brief's own "<= 15 calls" cap) bounds how many of the 11 this single
                 invocation will repair; anything left over is reported under "skipped_budget" for
                 a follow-up run.

Prints the DB target (host:port/db, never the password) and refuses port 5433, per
docs/qa/loop/round_1_fixes.md's lesson. --apply re-verifies its own writes from a *separate* new
connection before exiting.

    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round13.py so_what              # dry run
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round13.py so_what --apply       # write
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from psycopg.rows import dict_row

_AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from eoa.db import connection  # noqa: E402
from eoa.memory.relational import update_item_fields  # noqa: E402
from eoa.pipeline.analyze import repair_so_what_text  # noqa: E402
from eoa.report.qa_citations import SO_WHAT_TEMPLATE_PHRASES_HE, split_sentences  # noqa: E402

#: The round-12 brief's own generic-phrase repair prompt (`eoa.pipeline.analyze
#: ._SO_WHAT_REPAIR_INSTRUCTION_HE`) is worded for an already-generic ``so_what_he`` ("the
#: so_what_he you wrote uses a generic formula ({phrase})"); a totally-missing field has no such
#: phrase to name, so this placeholder stands in for it -- the LLM still gets the item's own
#: summary/original text via `_analyze_prompt` for real context, this string only fills the
#: template's own ``{phrase}`` slot.
_MISSING_SO_WHAT_PLACEHOLDER_HE = "so_what_he לא נכתב כלל (השדה ריק)"

_SO_WHAT_RE = re.compile(
    "|".join(re.escape(p) for p in sorted(set(SO_WHAT_TEMPLATE_PHRASES_HE), key=len, reverse=True))
)


class LLMBudget:
    """A shared counter the ``so_what`` repair checks/decrements before every LLM call -- enforces
    this run's total-call cap (round-13 brief: "<= 15 calls")."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    def use(self) -> None:
        self.used += 1


def _load_env() -> None:
    env = Path(__file__).resolve().parents[1] / "runtime" / "eoa.env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    # The so_what repair must run on the cloud chain, never silently fall back to the local model
    # (same rule round-6's script enforces for its own so_what subcommand).
    os.environ["EOA_PIPELINE"] = "1"


def _print_target() -> None:
    u = urlsplit(os.environ.get("DATABASE_URL", ""))
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        head = cur.fetchone()
    print(
        f"[repair_round13] target {u.hostname}:{u.port}{u.path} "
        f"alembic={head['version_num'] if head else '?'}",
        file=sys.stderr,
    )
    if u.port == 5433:
        print("[repair_round13] refusing to run against port 5433 (retired Docker DB)", file=sys.stderr)
        sys.exit(2)


def _fetch_item(item_id: int) -> dict | None:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM items WHERE id = %(id)s", {"id": item_id})
        return cur.fetchone()


# --------------------------------------------------------------------------
# so_what -- missing so_what_he on in-scope (red/orange/yellow) items
# --------------------------------------------------------------------------


def find_missing_so_what_items() -> list[dict]:
    """Every ``level IN ('red','orange','yellow')`` item with ``so_what_he IS NULL`` (DB-wide --
    round-12 judge worst #5, 11 items live as of this round). ``level``, not ``domain``, is the
    in-scope filter here: see the module docstring's note on why (9 of the 11 carry
    ``domain = 'out_of_scope'``, a separate pre-existing mismatch)."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, level, domain, title
            FROM items
            WHERE level IN ('red', 'orange', 'yellow') AND so_what_he IS NULL
            ORDER BY id
            """
        )
        return cur.fetchall()


def repair_so_what(apply: bool, budget: LLMBudget) -> dict:
    targets = find_missing_so_what_items()
    report: dict = {
        "target_count": len(targets),
        "targets": [{"id": r["id"], "level": r["level"], "title": r["title"]} for r in targets],
        "repaired": [],
        "rejected": [],
        "skipped_budget": [],
    }
    if not apply:
        return report

    for row in targets:
        if budget.exhausted:
            report["skipped_budget"].append(row["id"])
            continue
        item = _fetch_item(row["id"])
        if item is None:
            report["rejected"].append({"id": row["id"], "reason": "item_vanished"})
            continue
        budget.use()
        new_text = repair_so_what_text(
            item,
            so_what_he="",
            summary_he=item.get("summary_he") or "",
            phrase=_MISSING_SO_WHAT_PLACEHOLDER_HE,
        )
        if not new_text:
            report["rejected"].append({"id": row["id"], "reason": "llm_repair_failed_or_rejected"})
            continue
        if _SO_WHAT_RE.search(new_text):
            report["rejected"].append({"id": row["id"], "reason": "still_generic", "text": new_text})
            continue
        n_sentences = len(split_sentences(new_text))
        if not (1 <= n_sentences <= 3):
            report["rejected"].append(
                {"id": row["id"], "reason": f"sentence_count={n_sentences}", "text": new_text}
            )
            continue
        update_item_fields(row["id"], so_what_he=new_text)
        report["repaired"].append({"id": row["id"], "level": row["level"], "so_what_he": new_text})
    return report


# --------------------------------------------------------------------------
# verification (separate connection, per standing rule)
# --------------------------------------------------------------------------


def verify_all() -> dict:
    """Re-checks the so_what repair's success criterion from a fresh connection."""
    remaining = find_missing_so_what_items()
    return {
        "so_what_remaining_count": len(remaining),
        "so_what_remaining_ids": [r["id"] for r in remaining],
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("subcommand", choices=["so_what"], help="which repair to run")
    ap.add_argument("--apply", action="store_true", help="write the repairs (default: dry run)")
    ap.add_argument(
        "--llm-budget",
        type=int,
        default=15,
        help="max LLM calls this invocation will spend (default 15, round-13 brief's own cap)",
    )
    args = ap.parse_args()

    _load_env()
    _print_target()

    budget = LLMBudget(args.llm_budget)
    report: dict = {"mode": "apply" if args.apply else "dry_run", "llm_budget": args.llm_budget}

    if args.subcommand == "so_what":
        report["so_what"] = repair_so_what(args.apply, budget)

    report["llm_calls_used"] = budget.used

    if args.apply:
        report["verify"] = verify_all()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
