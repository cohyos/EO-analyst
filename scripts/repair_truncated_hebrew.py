#!/usr/bin/env python
"""Q3-1 repair (docs/qa/findings_Q3_r1.md): fix Hebrew text truncated mid-acronym across
``items``, ``events``, ``tenders``, and ``tender_forecasts``.

Root cause (see ``eoa.llm.ollama_client``'s Q3-1 note): Ollama's schema-constrained decoding
legally closes a JSON string the instant it emits an ASCII ``"`` -- exactly the character a Hebrew
acronym like כטב"ם/מטע"ד/מ"מ needs before its final letter(s). The prompts now instruct the model
to use the Hebrew gershayim ״ instead, and ``chat_structured`` now retries once when it detects
this at generation time, but pre-existing rows in the DB were written before that fix and need a
one-off repair:

- ``items.summary_he`` / ``so_what_he`` / ``uncertainty_he`` / ``tech_readiness_note_he``: these
  all come from one ``AnalyzeOut`` call, so a flagged row gets the ``analyze`` stage re-run in full
  (``eoa.pipeline.analyze.analyze_item`` + ``persist_analysis``) with the corrected prompts/guard.
- ``items.triage_reason``: re-runs the ``triage`` stage (``eoa.pipeline.triage.triage_item``),
  updating ``score``/``level``/``triage_reason`` together (consistent with Q3-4's own
  score/component contract).
- ``events.summary_he``, ``tenders.summary_he``, ``tender_forecasts.rationale_he``: there is no
  single-row "regenerate just this field" pipeline entry point for these (they're derived
  alongside other fields, or belong to the tenders pipeline this pass doesn't own), so these get
  the same cheap, safe, deterministic quote-normalisation ``chat_structured``'s own guard applies
  (ASCII ``"`` between two Hebrew letters -> ״) via a direct ``UPDATE`` -- no LLM call, no risk of
  inventing new text -- reported separately from the two LLM-repaired categories above.

Use ``--dry-run`` to see what would change (and how many rows in each category) without writing
anything or calling Ollama.

Run with the same ``DATABASE_URL`` as the app and Ollama reachable, e.g.:

    PYTHONPATH=agent DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \\
        python scripts/repair_truncated_hebrew.py [--dry-run] [--role resident]
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
from eoa.llm.ollama_client import (  # noqa: E402
    _looks_truncated_mid_hebrew_acronym,
    _normalize_hebrew_quotes,
)

# Text columns to scan per table -- (table, id_column, [text columns]).
_ITEM_ANALYZE_FIELDS = ("summary_he", "so_what_he", "uncertainty_he", "tech_readiness_note_he")
_ITEM_TRIAGE_FIELDS = ("triage_reason",)

# D1 round-1 fix (docs/qa/loop/round_1_fixes.md, ``hebrew_truncation_zero_hits``, items 8/33/39/
# 55/1091): ``agent/eoa/qa/d1_classify.py``'s own D1 check passes ``triage_reason`` to
# ``_looks_truncated_mid_hebrew_acronym`` under the synthetic field name ``"reason_he"`` (not the
# literal column name) specifically so the *generic* "long sentence, no terminal punctuation" net
# applies to it (that net only fires for a field name literally ending in ``"_he"`` -- see the
# function's own docstring). This repair script used to pass the literal column name
# (``"triage_reason"``, which does not end in ``"_he"``) instead, so it silently missed every one
# of those five items: none of them happen to end on an exact known acronym stem or a complete
# acronym pattern (item 55 ends on the Hebrew-prefixed token "לרק", item 8/1091 end mid ordinary
# word, not mid-acronym at all) -- only the generic net catches them, and it was never reached.
# Kept as an explicit mapping (not a hardcoded ``if``) so any future *_he-style column gets the
# same treatment automatically if it's ever added to ``_ITEM_TRIAGE_FIELDS``.
_CHECK_FIELD_NAME: dict[str, str] = {"triage_reason": "reason_he"}


def _row_flagged_fields(row: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [
        f
        for f in fields
        if row.get(f) and _looks_truncated_mid_hebrew_acronym(row[f], _CHECK_FIELD_NAME.get(f, f))
    ]


def _existing_items_columns(cur: Any) -> set[str]:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'items'")
    return {row["column_name"] for row in cur.fetchall()}


def _fetch_items() -> list[dict[str, Any]]:
    wanted = {
        "id",
        "title",
        "url",
        "clean_text",
        "source_name",
        "report_kind",
        "published_at",
        "domain",
        "subdomain",
        "trl",
        "entities_mentioned",
        *_ITEM_ANALYZE_FIELDS,
        *_ITEM_TRIAGE_FIELDS,
    }
    with db.connection() as conn, conn.cursor() as cur:
        # A DB may sit behind HEAD (see docs/MODULES.md) and not yet have an additive analyze
        # column a later migration adds (e.g. `tech_readiness_note_he`, migration 0011) -- select
        # only the columns that actually exist rather than failing the whole repair run outright.
        present = _existing_items_columns(cur)
        cols = ", ".join(sorted(wanted & present))
        cur.execute(f"SELECT {cols} FROM items")
        return cur.fetchall()


def _fetch_simple(table: str, id_col: str, text_col: str) -> list[dict[str, Any]]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {id_col} AS id, {text_col} AS text FROM {table}")
        return cur.fetchall()


def repair(
    *, dry_run: bool = False, role: str = "resident", item_ids: set[int] | None = None
) -> dict[str, Any]:
    """``item_ids``, when given, restricts the LLM-repairing item passes (analyze/triage) to
    exactly those ids -- e.g. a QA round's specific flagged rows -- rather than every flagged item
    in the DB (useful for a small, targeted re-triage without also re-running a much larger
    backlog the same detector fix newly surfaces). The events/tenders/tender_forecasts
    quote-normalization pass below is unaffected (it's a cheap, deterministic UPDATE, not an LLM
    call, so there's no reason to scope it down)."""
    from eoa.memory.relational import update_item_fields
    from eoa.pipeline.analyze import analyze_item, persist_analysis
    from eoa.pipeline.triage import triage_item

    report: dict[str, Any] = {
        "items_analyze_repaired": [],
        "items_analyze_failed": [],
        "items_triage_repaired": [],
        "items_triage_failed": [],
        "events_quote_normalized": [],
        "tenders_quote_normalized": [],
        "tender_forecasts_quote_normalized": [],
        "events_truncated_unrepairable": [],
        "tenders_truncated_unrepairable": [],
        "tender_forecasts_truncated_unrepairable": [],
    }

    items = _fetch_items()
    if item_ids is not None:
        items = [it for it in items if it["id"] in item_ids]
    for item in items:
        analyze_fields = _row_flagged_fields(item, _ITEM_ANALYZE_FIELDS)
        if analyze_fields:
            before = {f: item.get(f) for f in analyze_fields}
            if dry_run:
                report["items_analyze_repaired"].append(
                    {"id": item["id"], "fields": analyze_fields, "before": before}
                )
                continue
            try:
                out = analyze_item(item, role=role)
                persist_analysis(item, out)
                report["items_analyze_repaired"].append(
                    {"id": item["id"], "fields": analyze_fields, "before": before, "after": out.summary_he}
                )
            except Exception as exc:  # a repair run must not die on one bad row
                report["items_analyze_failed"].append({"id": item["id"], "error": str(exc)[:200]})

        triage_fields = _row_flagged_fields(item, _ITEM_TRIAGE_FIELDS)
        if triage_fields:
            before = item.get("triage_reason")
            if dry_run:
                report["items_triage_repaired"].append({"id": item["id"], "before": before})
                continue
            try:
                out = triage_item(item, role=role)
                update_item_fields(
                    item["id"], score=out.score, level=out.level, triage_reason=out.reason_he[:600]
                )
                report["items_triage_repaired"].append(
                    {"id": item["id"], "before": before, "after": out.reason_he}
                )
            except Exception as exc:
                report["items_triage_failed"].append({"id": item["id"], "error": str(exc)[:200]})

    for table, id_col, text_col, report_key in (
        ("events", "id", "summary_he", "events_quote_normalized"),
        ("tenders", "id", "summary_he", "tenders_quote_normalized"),
        ("tender_forecasts", "id", "rationale_he", "tender_forecasts_quote_normalized"),
    ):
        for row in _fetch_simple(table, id_col, text_col):
            text = row.get("text")
            if not text:
                continue
            fixed = _normalize_hebrew_quotes(text)
            if fixed != text:
                # An ASCII quote sitting between two Hebrew letters -- a *complete* acronym
                # written with the wrong quote character, safely fixable without regenerating
                # anything.
                report[report_key].append({"id": row["id"], "before": text, "after": fixed})
                if not dry_run:
                    with db.connection() as conn, conn.cursor() as cur:
                        cur.execute(
                            f"UPDATE {table} SET {text_col} = %(text)s WHERE {id_col} = %(id)s",
                            {"text": fixed, "id": row["id"]},
                        )
                continue
            # No quote to normalise -- but if the text still ends bare on a known acronym stem
            # (genuine truncation: content is actually *missing*, not just mis-quoted), there is
            # no single-row regeneration path for this table, so it's reported as a known,
            # currently-unrepairable gap rather than silently skipped.
            if _looks_truncated_mid_hebrew_acronym(text, "raw"):  # "raw": skip the generic *_he-suffix branch
                report[f"{report_key.rsplit('_', 2)[0]}_truncated_unrepairable"].append(
                    {"id": row["id"], "text": text}
                )

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report what would change without writing it")
    parser.add_argument(
        "--role", default="resident", help="LLM role for re-analysis/re-triage (default: resident)"
    )
    parser.add_argument(
        "--ids",
        default=None,
        help="comma-separated items.id list to restrict the LLM analyze/triage passes to (default: all flagged)",
    )
    args = parser.parse_args()

    item_ids = {int(x) for x in args.ids.split(",") if x.strip()} if args.ids else None
    report = repair(dry_run=args.dry_run, role=args.role, item_ids=item_ids)

    print(
        f"{'=' * 70}\nQ3-1 Hebrew-truncation repair {'(DRY RUN)' if args.dry_run else '(APPLIED)'}\n{'=' * 70}"
    )
    for key, rows in report.items():
        print(f"\n{key}: {len(rows)}")
        for r in rows[:15]:
            print(f"  id={r.get('id')} { ({k: v for k, v in r.items() if k not in ('id',)}) }")
        if len(rows) > 15:
            print(f"  ... and {len(rows) - 15} more")

    total = sum(len(v) for k, v in report.items() if not k.endswith("_failed"))
    total_failed = len(report["items_analyze_failed"]) + len(report["items_triage_failed"])
    print(f"\n{'=' * 70}\nTotal rows flagged/repaired: {total}  |  failed: {total_failed}\n{'=' * 70}")
    if args.dry_run:
        print("(dry run -- nothing written; re-run without --dry-run to apply)")


if __name__ == "__main__":
    main()
