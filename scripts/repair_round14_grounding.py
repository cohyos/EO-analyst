#!/usr/bin/env python
"""Round-14 data repair (user report 2026-09-07, severe): the analysis stage had no grounding
guard at all before this round (``eoa.pipeline.analysis_grounding``, wired into
``eoa.pipeline.analyze.persist_analysis``/``repair_so_what_text`` for every future analysis) --
this script is the one-time (and re-runnable) sweep over rows that were already persisted before
that guard existed.

Root cause: item 39 (edrmagazine.eu, "The all-new 15-300 mm f/4 MWIR zoom") had its ``summary_he``
claim "Ophir Optronics, חברה בת של תעשייה אווירית (תע"א)" -- false (Ophir Optronics is a subsidiary
of MKS Instruments, not IAI) -- and its ``so_what_he`` invent two non-existent competitors
("פלנטריוניקס"/"Planar Optics" and "טלסקופיקס"/"Telescopeics"). Both fabrications then flowed into
``weekly_2026-09-07.md`` and ``bd_il_2026-09-07.md``.

Two subcommands:

    items     -- every item with ``domain <> 'out_of_scope'`` AND ``level <> 'archive'`` (the
                 task brief's own "in-scope" definition for this round) and a non-null
                 ``summary_he``/``so_what_he``: runs ``eoa.pipeline.analysis_grounding
                 .ground_analysis_fields`` over summary_he/so_what_he/key_facts/entities_mentioned.
                 A row where grounding removed nothing is left untouched. A row where grounding
                 left summary_he or so_what_he "too thin" (``analysis_grounding.is_too_thin``) gets
                 one fresh re-analysis via ``eoa.pipeline.analyze.analyze_item`` on the cloud chain
                 (this script forces ``EOA_PIPELINE=1``), re-grounded before being accepted -- an
                 LLM budget (default 15, ``--llm-budget``) caps how many of those this invocation
                 will spend.
    patents   -- every ``patents`` row with a non-null ``claims_summary_he``/``so_what_he``: same
                 grounding pass (mapped ``claims_summary_he`` -> ``summary_he``,
                 patents.so_what_he -> ``so_what_he``) -- patents has no analyze-stage LLM entry
                 point this script is allowed to call (agent/eoa/patents/** is out of scope for
                 this round), so a too-thin patent row after stripping is reported under
                 "needs_manual_review" instead of being re-analyzed.

Defaults to a dry run (report only); pass --apply to write. Every row about to change is backed up
first to ``runtime/backups/repair_round14_grounding_<UTC timestamp>.json`` (before/after values for
every changed field). Prints the DB target (host:port/db, never the password) and refuses port
5433, per docs/qa/loop/round_1_fixes.md's lesson. --apply re-verifies its own writes from a
*separate* new connection before exiting.

    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round14_grounding.py items              # dry run
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round14_grounding.py items --apply       # write
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round14_grounding.py patents --apply    # write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from psycopg.rows import dict_row

_AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from eoa.db import connection  # noqa: E402
from eoa.errors import LLMOutputError, ResourceUnavailable  # noqa: E402
from eoa.pipeline.analysis_grounding import GroundingResult, ground_analysis_fields, is_too_thin  # noqa: E402
from eoa.pipeline.analyze import analyze_item  # noqa: E402

_BACKUP_DIR = Path(__file__).resolve().parents[1] / "runtime" / "backups"
_SO_WHAT_PREFIX_HE = "להערכתנו"


class LLMBudget:
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
    # A re-analysis of a too-thin field must run on the cloud chain, never silently fall back to
    # the local model (same rule round-13's script enforces for its own so_what repairs).
    os.environ["EOA_PIPELINE"] = "1"


def _print_target() -> None:
    u = urlsplit(os.environ.get("DATABASE_URL", ""))
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        head = cur.fetchone()
    print(
        f"[repair_round14_grounding] target {u.hostname}:{u.port}{u.path} "
        f"alembic={head['version_num'] if head else '?'}",
        file=sys.stderr,
    )
    if u.port == 5433:
        print("[repair_round14_grounding] refusing to run against port 5433 (retired Docker DB)", file=sys.stderr)
        sys.exit(2)


def _backup(rows: list[dict]) -> Path | None:
    if not rows:
        return None
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = _BACKUP_DIR / f"repair_round14_grounding_{ts}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[repair_round14_grounding] backed up {len(rows)} row(s) -> {path}", file=sys.stderr)
    return path


# --------------------------------------------------------------------------
# items
# --------------------------------------------------------------------------


def find_in_scope_items() -> list[dict]:
    """Every item in this round's own "in-scope" definition (task brief: "domain not in
    out_of_scope/archive") with at least one field the guard can act on."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT *
            FROM items
            WHERE domain IS DISTINCT FROM 'out_of_scope'
              AND level IS DISTINCT FROM 'archive'
              AND (summary_he IS NOT NULL OR so_what_he IS NOT NULL)
            ORDER BY id
            """
        )
        return cur.fetchall()


def _reanalyze_item(item: dict, budget: LLMBudget) -> GroundingResult | None:
    """One fresh cloud-chain re-analysis (item-39-shaped fixup: stripping the guard just applied
    left summary_he/so_what_he too thin to keep) -- returns the freshly-grounded result, or
    ``None`` on budget exhaustion / LLM failure / still-too-thin after regeneration."""
    if budget.exhausted:
        return None
    budget.use()
    try:
        out = analyze_item(item)
    except (LLMOutputError, ResourceUnavailable) as exc:
        log_error = str(exc)[:160]
        print(f"[repair_round14_grounding] item {item['id']} re-analysis failed: {log_error}", file=sys.stderr)
        return None
    from eoa.pipeline.analyze import _dedupe_key_facts  # local import: private helper, same module

    grounded = ground_analysis_fields(
        item,
        summary_he=out.summary_he,
        so_what_he=out.so_what_he,
        key_facts=_dedupe_key_facts(list(out.key_facts)),
        entities_mentioned=item.get("entities_mentioned"),
    )
    if is_too_thin(grounded.summary_he) or is_too_thin(grounded.so_what_he, require_prefix=_SO_WHAT_PREFIX_HE):
        return None
    return grounded


def repair_items(apply: bool, budget: LLMBudget) -> dict:
    targets = find_in_scope_items()
    report: dict = {
        "checked": len(targets),
        "changed": [],
        "removed_by_category": {},
        "reanalyzed": [],
        "needs_manual_review": [],
    }
    backup_rows: list[dict] = []

    for item in targets:
        result = ground_analysis_fields(
            item,
            summary_he=item.get("summary_he") or "",
            so_what_he=item.get("so_what_he") or "",
            key_facts=item.get("key_facts") or [],
            entities_mentioned=item.get("entities_mentioned") or [],
        )
        if not result.removed:
            continue

        for r in result.removed:
            report["removed_by_category"][r["category"]] = report["removed_by_category"].get(r["category"], 0) + 1

        too_thin = is_too_thin(result.summary_he) or is_too_thin(
            result.so_what_he, require_prefix=_SO_WHAT_PREFIX_HE
        )
        final = result
        reanalyzed = False
        if too_thin:
            if not apply:
                # Dry run: report what *would* need a fresh LLM call rather than actually spending
                # one -- a dry run must never have a side effect or cost.
                report["needs_manual_review"].append(
                    {"id": item["id"], "reason": "too_thin_after_grounding_would_reanalyze_on_apply"}
                )
            else:
                fresh = _reanalyze_item(item, budget)
                if fresh is not None:
                    final = fresh
                    reanalyzed = True
                    report["reanalyzed"].append(item["id"])
                else:
                    report["needs_manual_review"].append(
                        {"id": item["id"], "reason": "too_thin_after_grounding_and_reanalysis_attempt"}
                    )

        change_record = {
            "id": item["id"],
            "title": (item.get("title") or "")[:160],
            "removed": result.removed,
            "reanalyzed": reanalyzed,
            "before": {
                "summary_he": item.get("summary_he"),
                "so_what_he": item.get("so_what_he"),
                "key_facts": item.get("key_facts"),
                "entities_mentioned": item.get("entities_mentioned"),
            },
            "after": {
                "summary_he": final.summary_he,
                "so_what_he": final.so_what_he,
                "key_facts": final.key_facts,
                "entities_mentioned": final.entities_mentioned,
            },
        }
        report["changed"].append(change_record)
        backup_rows.append({"table": "items", "id": item["id"], "before": change_record["before"]})

    if apply and backup_rows:
        _backup(backup_rows)
        for change_record in report["changed"]:
            from eoa.memory.relational import update_item_fields

            update_item_fields(
                change_record["id"],
                summary_he=change_record["after"]["summary_he"],
                so_what_he=change_record["after"]["so_what_he"],
                key_facts=change_record["after"]["key_facts"],
                entities_mentioned=change_record["after"]["entities_mentioned"],
            )

    report["llm_calls_used"] = budget.used
    return report


# --------------------------------------------------------------------------
# patents
# --------------------------------------------------------------------------


def find_patents() -> list[dict]:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, pub_number, title, abstract, url, assignees, claims_summary_he, so_what_he
            FROM patents
            WHERE claims_summary_he IS NOT NULL OR so_what_he IS NOT NULL
            ORDER BY id
            """
        )
        return cur.fetchall()


def repair_patents(apply: bool) -> dict:
    targets = find_patents()
    report: dict = {
        "checked": len(targets),
        "changed": [],
        "removed_by_category": {},
        "needs_manual_review": [],
    }
    backup_rows: list[dict] = []

    for patent in targets:
        result = ground_analysis_fields(
            patent,
            summary_he=patent.get("claims_summary_he") or "",
            so_what_he=patent.get("so_what_he") or "",
        )
        if not result.removed:
            continue

        for r in result.removed:
            report["removed_by_category"][r["category"]] = report["removed_by_category"].get(r["category"], 0) + 1

        too_thin = is_too_thin(result.summary_he) or is_too_thin(result.so_what_he)
        if too_thin:
            report["needs_manual_review"].append(
                {"id": patent["id"], "pub_number": patent.get("pub_number"), "reason": "too_thin_after_grounding"}
            )

        change_record = {
            "id": patent["id"],
            "pub_number": patent.get("pub_number"),
            "removed": result.removed,
            "before": {"claims_summary_he": patent.get("claims_summary_he"), "so_what_he": patent.get("so_what_he")},
            "after": {"claims_summary_he": result.summary_he, "so_what_he": result.so_what_he},
        }
        report["changed"].append(change_record)
        backup_rows.append({"table": "patents", "id": patent["id"], "before": change_record["before"]})

    if apply and backup_rows:
        _backup(backup_rows)
        for change_record in report["changed"]:
            with connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE patents SET claims_summary_he = %(summary)s, so_what_he = %(so_what)s WHERE id = %(id)s",
                    {
                        "summary": change_record["after"]["claims_summary_he"],
                        "so_what": change_record["after"]["so_what_he"],
                        "id": change_record["id"],
                    },
                )

    return report


# --------------------------------------------------------------------------
# verification (separate connection, per standing rule)
# --------------------------------------------------------------------------


def verify_all(changed_item_ids: list[int], changed_patent_ids: list[int]) -> dict:
    """Re-checks, from a fresh connection, that every changed row's persisted grounding removal
    set is now empty (the guard has nothing left to strip)."""
    remaining_items: list[int] = []
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        for item_id in changed_item_ids:
            cur.execute("SELECT * FROM items WHERE id = %(id)s", {"id": item_id})
            row = cur.fetchone()
            if row is None:
                continue
            result = ground_analysis_fields(
                row,
                summary_he=row.get("summary_he") or "",
                so_what_he=row.get("so_what_he") or "",
                key_facts=row.get("key_facts") or [],
                entities_mentioned=row.get("entities_mentioned") or [],
            )
            if result.removed:
                remaining_items.append(item_id)

    remaining_patents: list[int] = []
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        for patent_id in changed_patent_ids:
            cur.execute("SELECT * FROM patents WHERE id = %(id)s", {"id": patent_id})
            row = cur.fetchone()
            if row is None:
                continue
            result = ground_analysis_fields(
                row, summary_he=row.get("claims_summary_he") or "", so_what_he=row.get("so_what_he") or ""
            )
            if result.removed:
                remaining_patents.append(patent_id)

    return {
        "items_still_ungrounded": remaining_items,
        "patents_still_ungrounded": remaining_patents,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("subcommand", choices=["items", "patents"], help="which repair to run")
    ap.add_argument("--apply", action="store_true", help="write the repairs (default: dry run)")
    ap.add_argument(
        "--llm-budget",
        type=int,
        default=15,
        help="max re-analysis LLM calls this invocation will spend (items subcommand only, default 15)",
    )
    args = ap.parse_args()

    _load_env()
    _print_target()

    report: dict = {"mode": "apply" if args.apply else "dry_run", "subcommand": args.subcommand}

    if args.subcommand == "items":
        budget = LLMBudget(args.llm_budget)
        report["items"] = repair_items(args.apply, budget)
        if args.apply:
            changed_ids = [c["id"] for c in report["items"]["changed"]]
            report["verify"] = verify_all(changed_ids, [])
    else:
        report["patents"] = repair_patents(args.apply)
        if args.apply:
            changed_ids = [c["id"] for c in report["patents"]["changed"]]
            report["verify"] = verify_all([], changed_ids)

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
