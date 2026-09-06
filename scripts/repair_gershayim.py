#!/usr/bin/env python
"""D1 round-1 repair (docs/qa/loop/round_1_fixes.md, ``gershayim_no_ascii_quote``): normalise an
ASCII ``"`` sitting directly between two Hebrew letters into the Hebrew gershayim ״ (U+05F4) across
every already-persisted row that can carry free Hebrew prose.

Root cause: Ollama's schema-constrained JSON decoding legally closes a JSON string the instant it
emits an ASCII ``"`` -- exactly the character a Hebrew acronym like כטב"ם/מטע"ד/תע"א/צה"ל/מ"מ/ק"מ/
מכ"ם/אמ"ן needs before its final letter(s). ``eoa.llm.ollama_client.chat_structured`` now
normalises this for every *new* generation (see its ``_normalize_model_hebrew_quotes`` post-
validation step), but rows written before that guard existed still carry the raw ASCII quote. This
script is the one-off backfill over already-persisted rows -- deterministic, no LLM call, reusing
the exact same regex/substitution the live guard uses
(:data:`eoa.llm.ollama_client._ASCII_QUOTE_BETWEEN_HEBREW_RE` /
:func:`eoa.llm.ollama_client._normalize_hebrew_quotes`), so it can never invent new text, only
retype an already-written quote character.

Scope, across every table that can carry LLM-authored Hebrew free text:

- ``items``: every ``text``/``text[]`` column (dynamically discovered via
  ``information_schema.columns`` -- covers ``summary_he``/``so_what_he``/``triage_reason``/
  ``uncertainty_he``/``key_facts`` on every schema version, plus any additive Hebrew column a
  later migration adds, e.g. ``tech_readiness_note_he``/``israel_reasons``, without needing this
  script edited again).
- ``events``, ``tenders``, ``tender_forecasts``, ``entities`` (its ``aliases``/``focus``/``notes``
  columns): same dynamic text/text[] column discovery.
- ``jobs.result`` for ``kind='deep_search'`` rows (investigation results): walked recursively as
  JSON (dict/list/str) since ``InvestigationOut`` is stored as a single JSONB blob, not discrete
  columns -- covers ``answer_he``/``contradictions_he``/``what_was_tried_he``/``key_facts`` and any
  nested string this schema (or a future one) adds.

Explicitly OUT of scope: already-*rendered* report files under ``output/reports/**``
(``daily``/``weekly``/``bd_*``/``patent_survey_*`` ``.md``/``.html``/``.docx``) -- these are a
rendering of already-QA'd DB rows, not a source of truth, and get regenerated wholesale by the
next scheduled report run once the underlying items are clean; patching rendered files in place
would violate docs/CONVENTIONS.md rule 4 (provenance follows the DB row, not a stale render).

A cell containing zero Hebrew-adjacent ASCII quotes is left completely untouched (byte-identical)
-- this script never rewrites a column it didn't need to.

Use ``--dry-run`` to see what would change (per-table/column counts + before/after excerpts for
the first few rows) without writing anything.

Run with the same ``DATABASE_URL`` as the app, e.g.:

    DATABASE_URL=<from runtime/eoa.env — port 5432> \\
        PYTHONPATH=agent python scripts/repair_gershayim.py [--dry-run]
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

from eoa.llm.ollama_client import (  # noqa: E402
    _ASCII_QUOTE_BETWEEN_HEBREW_RE,
    _normalize_hebrew_quotes,
)

#: Tables (besides ``jobs``, handled separately as JSON) that can carry LLM-authored Hebrew free
#: text in a plain ``text``/``text[]`` column.
_TABLES = ("items", "events", "tenders", "tender_forecasts", "entities")

#: Per-table columns to skip even though they are ``text``/``text[]`` -- verbatim *fetched* source
#: content (docs/CONVENTIONS.md rule 4, provenance: a report claim traces back to what a source
#: actually published) or plain non-Hebrew identifiers, never LLM-authored analysis prose. Rewriting
#: e.g. ``items.raw_text``/``clean_text`` would alter the historical record of what was actually
#: fetched, even though the substitution itself is narrowly scoped to an ASCII quote between two
#: Hebrew letters. ``items.title`` is likewise the article's own (possibly source-site-authored)
#: headline, chosen by ``eoa.fetch.sanitize.choose_title`` -- not LLM output.
_EXCLUDE_COLUMNS: dict[str, frozenset[str]] = {
    "items": frozenset(
        {"raw_text", "clean_text", "title", "url", "canonical_url", "source_name", "text_hash"}
    ),
}


def _needs_fix(value: Any) -> bool:
    return isinstance(value, str) and bool(_ASCII_QUOTE_BETWEEN_HEBREW_RE.search(value))


# -- plain text/text[] columns (items/events/tenders/tender_forecasts/entities) -----------------


def _text_columns(cur: Any, table: str) -> list[tuple[str, str]]:
    """``[(column_name, 'scalar' | 'array')]`` for every ``text``/``text[]`` column of ``table``,
    discovered dynamically so this script stays correct across schema versions -- this DB may sit
    behind ``HEAD`` (see docs/MODULES.md) and not have every column a newer migration adds, or a
    future migration may add a new Hebrew free-text column without this script needing an edit."""
    cur.execute(
        """
        SELECT column_name, data_type, udt_name
        FROM information_schema.columns
        WHERE table_name = %(table)s
        """,
        {"table": table},
    )
    excluded = _EXCLUDE_COLUMNS.get(table, frozenset())
    cols: list[tuple[str, str]] = []
    for row in cur.fetchall():
        if row["column_name"] in excluded:
            continue
        if row["data_type"] == "text":
            cols.append((row["column_name"], "scalar"))
        elif row["data_type"] == "ARRAY" and row["udt_name"] == "_text":
            cols.append((row["column_name"], "array"))
    return cols


def _row_needs_fix(row: dict[str, Any], cols: list[tuple[str, str]]) -> dict[str, Any]:
    """``{column: new_value}`` for every column of ``row`` that needs a quote fix."""
    updates: dict[str, Any] = {}
    for col, kind in cols:
        value = row.get(col)
        if kind == "scalar":
            if _needs_fix(value):
                updates[col] = _normalize_hebrew_quotes(value)
        else:  # array
            if value and any(_needs_fix(v) for v in value):
                updates[col] = [_normalize_hebrew_quotes(v) if isinstance(v, str) else v for v in value]
    return updates


def repair_table(cur: Any, table: str, *, dry_run: bool) -> list[dict[str, Any]]:
    cols = _text_columns(cur, table)
    if not cols:
        return []
    id_col = "id"
    select_cols = ", ".join([id_col, *(c for c, _ in cols)])
    cur.execute(f"SELECT {select_cols} FROM {table}")
    rows = cur.fetchall()

    report: list[dict[str, Any]] = []
    for row in rows:
        updates = _row_needs_fix(row, cols)
        if not updates:
            continue
        report.append({"id": row[id_col], "fields": sorted(updates)})
        if not dry_run:
            set_clause = ", ".join(f"{c} = %({c})s" for c in updates)
            params: dict[str, Any] = dict(updates)
            params["id"] = row[id_col]
            cur.execute(f"UPDATE {table} SET {set_clause} WHERE {id_col} = %(id)s", params)
    return report


# -- jobs.result (JSON, deep_search investigation results) --------------------------------------


def _normalize_json(obj: Any) -> tuple[Any, bool]:
    """Recursively normalise every string leaf of a JSON-shaped value (dict/list/str/other);
    returns ``(new_value, changed)``. Mirrors ``eoa.llm.ollama_client._iter_model_strings``'s
    coverage but for plain JSON rather than a pydantic model (``jobs.result`` is one JSONB blob,
    not discrete columns)."""
    if isinstance(obj, str):
        fixed = _normalize_hebrew_quotes(obj)
        return fixed, fixed != obj
    if isinstance(obj, list):
        new_list = []
        changed = False
        for item in obj:
            new_item, item_changed = _normalize_json(item)
            new_list.append(new_item)
            changed = changed or item_changed
        return new_list, changed
    if isinstance(obj, dict):
        new_dict = {}
        changed = False
        for key, value in obj.items():
            new_value, value_changed = _normalize_json(value)
            new_dict[key] = new_value
            changed = changed or value_changed
        return new_dict, changed
    return obj, False


def repair_investigation_results(cur: Any, *, dry_run: bool) -> list[dict[str, Any]]:
    cur.execute("SELECT id, result FROM jobs WHERE kind = 'deep_search' AND result IS NOT NULL")
    rows = cur.fetchall()

    report: list[dict[str, Any]] = []
    for row in rows:
        new_result, changed = _normalize_json(row["result"])
        if not changed:
            continue
        report.append({"id": row["id"]})
        if not dry_run:
            from psycopg.types.json import Json

            cur.execute(
                "UPDATE jobs SET result = %(result)s, updated_at = now() WHERE id = %(id)s",
                {"result": Json(new_result), "id": row["id"]},
            )
    return report


def repair(*, dry_run: bool = False) -> dict[str, list[dict[str, Any]]]:
    from eoa.db import connection

    report: dict[str, list[dict[str, Any]]] = {}
    with connection() as conn, conn.cursor() as cur:
        for table in _TABLES:
            report[table] = repair_table(cur, table, dry_run=dry_run)
        report["jobs.result"] = repair_investigation_results(cur, dry_run=dry_run)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="report what would change without writing it")
    args = parser.parse_args()

    report = repair(dry_run=args.dry_run)
    mode = "DRY RUN" if args.dry_run else "APPLIED"

    print(f"\n{'=' * 70}\ngershayim (ASCII-quote-between-Hebrew-letters) repair ({mode})\n{'=' * 70}")
    total = 0
    for key, rows in report.items():
        print(f"\n{key}: {len(rows)} row(s)")
        for r in rows[:20]:
            print(f"  {r}")
        if len(rows) > 20:
            print(f"  ... and {len(rows) - 20} more")
        total += len(rows)

    print(f"\n{'=' * 70}\nTotal rows repaired: {total}\n{'=' * 70}")
    if args.dry_run:
        print("(dry run -- nothing written; re-run without --dry-run to apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
