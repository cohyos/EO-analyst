#!/usr/bin/env python
"""Round-17 repair (2026-09-17, feedback: reports spelled Rafael "ראפאל" instead of "רפאל"):
normalise a known Hebrew company-name misspelling/alternate transliteration onto its canonical
spelling -- ``eoa.report.textnorm.canonicalize_hebrew_names``, driven by the ``hebrew_names``
registry in config/company_facts.yaml -- across every already-persisted row that can carry
LLM-authored Hebrew prose.

Root cause: nothing canonicalised a company name before this round -- the LLM output
transliterates freely (e.g. "ראפאל"/"רפא\"ל" for Rafael, "ריינמטאל" for Rheinmetall, "לאונרדו" for
Leonardo, "סאב" for Saab) and the mistake then gets baked into `items`/`events`/investigation
answers/dossier data, then copied verbatim into every rendered report. The pipeline write sites
(``eoa.pipeline.analyze``/``classify``, ``eoa.search.deep_search``, ``eoa.api.routes.ask``) and the
report-rendering path (``eoa.report.textnorm.normalize_draft``/``eoa.report.docx_builder``) now
canonicalise every *new* write/render; this script is the one-off backfill over rows written before
that existed.

Scope (only where the task's own brief asked for it -- narrower than repair_gershayim.py's blanket
"every text/text[] column" sweep, since a company-name fix should only ever touch columns that can
actually carry a company name in free prose, never a title/URL/raw-fetched-source column):

- ``items``: ``summary_he``, ``so_what_he``, ``key_facts`` (text[]), ``uncertainty_he``.
- ``events``: ``summary_he``, ``title``.
- ``jobs.result`` for ``kind='deep_search'`` rows (investigation answers) -- walked recursively as
  JSON, same convention as ``repair_gershayim.py``'s own ``repair_investigation_results``.
- ``product_dossiers.data`` (dossier data text fields) -- walked recursively as JSON.

A cell containing zero registered Hebrew-name variants is left completely untouched
(byte-identical) -- this script never rewrites a column it didn't need to.

Use ``--dry-run`` to see what would change (per-table counts + before/after excerpts for the first
few rows) without writing anything. Every affected row's *original* values for the touched columns
are written to ``runtime/backups/repair_hebrew_names_<stamp>.json`` before any UPDATE is issued (or
would be issued, for ``--dry-run`` -- the backup file is still written so a dry run's own report is
reproducible, but no UPDATE is ever sent to the DB in that mode).

Run with the same ``DATABASE_URL`` as the app, e.g. (never print/log the value itself):

    set -a; . runtime/eoa.env; set +a
    PYTHONPATH=agent python scripts/repair_hebrew_names.py [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_AGENT_DIR = _REPO_ROOT / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from eoa.report.textnorm import canonicalize_hebrew_names, canonicalize_hebrew_names_deep  # noqa: E402

_BACKUP_DIR = _REPO_ROOT / "runtime" / "backups"

#: {table: [(column, kind)]} -- kind is "scalar" or "array" (text[]).
_ITEM_COLUMNS: list[tuple[str, str]] = [
    ("summary_he", "scalar"),
    ("so_what_he", "scalar"),
    ("key_facts", "array"),
    ("uncertainty_he", "scalar"),
]
_EVENT_COLUMNS: list[tuple[str, str]] = [
    ("summary_he", "scalar"),
    ("title", "scalar"),
]


def _needs_fix(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return canonicalize_hebrew_names(value) != value


def _row_updates(row: dict[str, Any], columns: list[tuple[str, str]]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for col, kind in columns:
        value = row.get(col)
        if kind == "scalar":
            if _needs_fix(value):
                updates[col] = canonicalize_hebrew_names(value)
        else:  # array
            if value and any(_needs_fix(v) for v in value):
                updates[col] = [canonicalize_hebrew_names(v) if isinstance(v, str) else v for v in value]
    return updates


def _repair_plain_table(
    cur: Any, table: str, columns: list[tuple[str, str]], *, dry_run: bool
) -> list[dict[str, Any]]:
    select_cols = ", ".join(["id", *(c for c, _ in columns)])
    cur.execute(f"SELECT {select_cols} FROM {table}")
    rows = cur.fetchall()

    report: list[dict[str, Any]] = []
    for row in rows:
        updates = _row_updates(row, columns)
        if not updates:
            continue
        report.append(
            {
                "id": row["id"],
                "fields": sorted(updates),
                "before": {c: row.get(c) for c in updates},
                "after": updates,
            }
        )
        if not dry_run:
            set_clause = ", ".join(f"{c} = %({c})s" for c in updates)
            params: dict[str, Any] = dict(updates)
            params["id"] = row["id"]
            cur.execute(f"UPDATE {table} SET {set_clause} WHERE id = %(id)s", params)
    return report


def _json_needs_fix(obj: Any) -> bool:
    if isinstance(obj, str):
        return _needs_fix(obj)
    if isinstance(obj, list):
        return any(_json_needs_fix(v) for v in obj)
    if isinstance(obj, dict):
        return any(_json_needs_fix(v) for v in obj.values())
    return False


def _repair_json_column(
    cur: Any, table: str, json_col: str, *, where: str = "", dry_run: bool
) -> list[dict[str, Any]]:
    cur.execute(f"SELECT id, {json_col} FROM {table} {where}")
    rows = cur.fetchall()

    report: list[dict[str, Any]] = []
    for row in rows:
        original = row[json_col]
        if original is None or not _json_needs_fix(original):
            continue
        new_value = canonicalize_hebrew_names_deep(original)
        report.append({"id": row["id"], "before": original, "after": new_value})
        if not dry_run:
            from psycopg.types.json import Json

            cur.execute(
                f"UPDATE {table} SET {json_col} = %(value)s WHERE id = %(id)s",
                {"value": Json(new_value), "id": row["id"]},
            )
    return report


def repair(*, dry_run: bool = False) -> dict[str, list[dict[str, Any]]]:
    from eoa.db import connection

    report: dict[str, list[dict[str, Any]]] = {}
    with connection() as conn, conn.cursor() as cur:
        report["items"] = _repair_plain_table(cur, "items", _ITEM_COLUMNS, dry_run=dry_run)
        report["events"] = _repair_plain_table(cur, "events", _EVENT_COLUMNS, dry_run=dry_run)
        report["jobs.result (deep_search)"] = _repair_json_column(
            cur, "jobs", "result", where="WHERE kind = 'deep_search' AND result IS NOT NULL", dry_run=dry_run
        )
        report["product_dossiers.data"] = _repair_json_column(
            cur, "product_dossiers", "data", where="WHERE data IS NOT NULL", dry_run=dry_run
        )
    return report


def _write_backup(report: dict[str, list[dict[str, Any]]]) -> Path | None:
    total = sum(len(rows) for rows in report.values())
    if total == 0:
        return None
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    path = _BACKUP_DIR / f"repair_hebrew_names_{stamp}.json"
    backup_payload = {
        table: [{"id": r["id"], "before": r["before"]} for r in rows] for table, rows in report.items()
    }
    path.write_text(json.dumps(backup_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="report what would change without writing it")
    args = parser.parse_args()

    report = repair(dry_run=args.dry_run)
    backup_path = _write_backup(report)
    mode = "DRY RUN" if args.dry_run else "APPLIED"

    print(f"\n{'=' * 70}\nHebrew company-name canonicalisation repair ({mode})\n{'=' * 70}")
    total = 0
    for key, rows in report.items():
        print(f"\n{key}: {len(rows)} row(s)")
        for r in rows[:20]:
            print(f"  id={r['id']} fields={r.get('fields', ['(json)'])}")
        if len(rows) > 20:
            print(f"  ... and {len(rows) - 20} more")
        total += len(rows)

    print(f"\n{'=' * 70}\nTotal rows repaired: {total}")
    if backup_path is not None:
        print(f"Backup written: {backup_path}")
    else:
        print("No rows needed a fix -- no backup written.")
    print("=" * 70)
    if args.dry_run:
        print("(dry run -- nothing written to the DB; re-run without --dry-run to apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
