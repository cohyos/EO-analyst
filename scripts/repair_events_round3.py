"""Round-3 data repair for the events table (docs/qa/loop/round_2_judge.md, D3).

Two defects the round-1 and round-2 judges both confirmed on the live DB:

1. `amount_usd` stored in millions ("$464.8 million" persisted as 464.8) -- events 55/84/89/121.
   Repaired by anchoring each suspicious amount to its item's source text with
   ``eoa.pipeline.analyze.normalize_amount_from_source`` (only a figure the text shows with a
   magnitude word is scaled; a genuine small price is left alone).
2. Re-worded same-kind duplicate events (item 81: three Norkin appointments, two funding rounds,
   two Elbit partnerships). Repaired with ``eoa.memory.relational.merge_duplicate_events``.

Dry-run by default; ``--apply`` writes. Prints the DB target (host:port/db, never the password) and
the alembic head first, per docs/qa/loop/round_1_fixes.md's lesson (repairs once ran against the
retired 5433 instance).

    PYTHONPATH=agent python scripts/repair_events_round3.py            # report only
    PYTHONPATH=agent python scripts/repair_events_round3.py --apply    # write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from psycopg.rows import dict_row

from eoa.db import connection
from eoa.memory.relational import merge_duplicate_events
from eoa.pipeline.analyze import _AMOUNT_SUSPICIOUS_BELOW, normalize_amount_from_source


def _load_env() -> None:
    env = Path(__file__).resolve().parents[1] / "runtime" / "eoa.env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _print_target() -> None:
    u = urlsplit(os.environ.get("DATABASE_URL", ""))
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        head = cur.fetchone()
    print(
        f"[repair] target {u.hostname}:{u.port}{u.path} alembic={head['version_num'] if head else '?'}",
        file=sys.stderr,
    )
    if u.port == 5433:
        print("[repair] refusing to run against port 5433 (retired Docker DB)", file=sys.stderr)
        sys.exit(2)


def repair_amounts(apply: bool) -> list[dict]:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT e.id, e.item_id, e.amount_usd, e.title, i.title AS item_title, i.clean_text, i.summary_he AS summary "
            "FROM events e JOIN items i ON i.id = e.item_id "
            "WHERE e.amount_usd IS NOT NULL AND e.amount_usd > 0 AND e.amount_usd < %(cap)s ORDER BY e.id",
            {"cap": _AMOUNT_SUSPICIOUS_BELOW},
        )
        rows = cur.fetchall()
    out: list[dict] = []
    for r in rows:
        text = " ".join(filter(None, [r["item_title"], r["clean_text"], r["summary"], r["title"]]))
        scaled, mag = normalize_amount_from_source(float(r["amount_usd"]), text)
        rec = {
            "event_id": r["id"],
            "item_id": r["item_id"],
            "before": float(r["amount_usd"]),
            "after": scaled,
            "magnitude": mag,
        }
        out.append(rec)
        if mag and apply:
            with connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE events SET amount_usd = %(a)s, updated_at = now() WHERE id = %(id)s",
                    {"a": scaled, "id": r["id"]},
                )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the repairs (default: dry run)")
    ap.add_argument("--item-id", type=int, default=None, help="limit the duplicate merge to one item")
    args = ap.parse_args()
    _load_env()
    _print_target()
    amounts = repair_amounts(args.apply)
    merges = merge_duplicate_events(item_id=args.item_id, dry_run=not args.apply)
    report = {
        "mode": "apply" if args.apply else "dry_run",
        "amounts": {
            "examined": len(amounts),
            "scaled": [a for a in amounts if a["magnitude"]],
            "left_alone": [a for a in amounts if not a["magnitude"]],
        },
        "duplicates": {"merged": len(merges), "merges": merges},
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
