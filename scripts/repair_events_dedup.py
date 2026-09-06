#!/usr/bin/env python
"""Q3-6 (docs/qa/findings_Q3_r1.md) repair: verify/apply the events (item_id, kind, lower(title))
dedup, and report (optionally delete) existing rows that look like a narrative/assessment
sentence rather than a real event.

Two independent passes:

1. **Dedup** (idempotent -- ``db/migrations/versions/0016_events_dedup_unique_index.py`` already
   performs the same merge/delete when that migration is applied; this pass exists so the same
   fix can be (re-)run against a host that hasn't migrated yet, or to double-check nothing slipped
   back in). For each group of rows sharing ``(item_id, kind, lower(title))``, keeps the
   lowest-id row, merging in the first non-null ``date``/``amount_usd``/``currency``/``customer``/
   ``program``/``summary_he`` and the first non-empty ``parties`` array found across the group,
   plus the max ``confidence`` -- then deletes the rest.

2. **Narrative-title report** (uses the exact same detection logic as
   ``eoa.pipeline.analyze._is_narrative_event_title``, which guards new inserts going forward):
   reports every *existing* row whose title reads like an assessment/forecast sentence
   ("השלכות...", "ייתכן...", or a factless, verb-less title) rather than a real event. Deleted
   only when ``--delete-narrative`` is passed -- reporting-only by default, since removing
   historical rows on a heuristic is a stronger action than deduping exact identical rows.

Usage:
    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \
    PYTHONPATH=agent python scripts/repair_events_dedup.py [--dry-run] [--delete-narrative]
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, "agent")

import structlog

log = structlog.get_logger(__name__)


def _normalized_key(row: dict[str, Any]) -> tuple[int, str, str]:
    return (row["item_id"], row.get("kind") or "", (row.get("title") or "").strip().casefold())


def _richer(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Merge ``b`` into ``a`` (``a`` wins on any field both have a value for) -- same policy as
    ``eoa.memory.relational.insert_event``'s ON CONFLICT upsert."""
    merged = dict(a)
    for field in ("date", "amount_usd", "currency", "customer", "program", "summary_he"):
        if merged.get(field) is None and b.get(field) is not None:
            merged[field] = b[field]
    if not merged.get("parties") and b.get("parties"):
        merged["parties"] = b["parties"]
    merged["confidence"] = max(merged.get("confidence") or 0, b.get("confidence") or 0)
    return merged


def find_duplicate_groups(rows: list[dict[str, Any]]) -> dict[tuple[int, str, str], list[dict[str, Any]]]:
    groups: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if not (row.get("title") or "").strip():
            continue  # no title -> no identity to key on, matches the DB unique index's NULL handling
        groups.setdefault(_normalized_key(row), []).append(row)
    return {k: v for k, v in groups.items() if len(v) > 1}


def _is_narrative(row: dict[str, Any]) -> bool:
    """Same detection as ``eoa.pipeline.analyze._is_narrative_event_title``, reimplemented
    against a plain DB row (rather than an ``EventOut``, whose ``kind`` Literal may reject an
    older row's free-text ``kind`` value) using the exact same keyword sets, imported directly."""
    from eoa.pipeline.analyze import _NARRATIVE_TITLE_PREFIXES_HE, _OCCURRENCE_VERBS_HE

    t = (row.get("title") or "").strip()
    if not t:
        return False
    if t.startswith(_NARRATIVE_TITLE_PREFIXES_HE):
        return True
    has_anchor = bool(row.get("parties")) or bool(row.get("customer")) or row.get("amount_usd") is not None or bool(row.get("date"))
    has_occurrence_verb = any(v in t for v in _OCCURRENCE_VERBS_HE)
    return not has_occurrence_verb and not has_anchor


def run_repair(*, dry_run: bool = False, delete_narrative: bool = False) -> dict[str, Any]:
    from eoa.db import connection

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, item_id, kind, title, date, amount_usd, currency, parties, customer, "
            "program, summary_he, confidence FROM events ORDER BY id"
        )
        rows = cur.fetchall()

        dup_groups = find_duplicate_groups(rows)
        to_delete: list[int] = []
        to_update: list[tuple[int, dict[str, Any]]] = []
        for group in dup_groups.values():
            group_sorted = sorted(group, key=lambda r: r["id"])
            keep, rest = group_sorted[0], group_sorted[1:]
            merged = keep
            for other in rest:
                merged = _richer(merged, other)
            if merged != keep:
                to_update.append((keep["id"], merged))
            to_delete.extend(r["id"] for r in rest)

        remaining_after = [r for r in rows if r["id"] not in to_delete]
        narrative_rows = [r for r in remaining_after if _is_narrative(r)]

        counts = {
            "rows_scanned": len(rows),
            "duplicate_groups": len(dup_groups),
            "duplicates_deleted": len(to_delete),
            "narrative_titles_found": len(narrative_rows),
            "narrative_titles_deleted": 0,
        }

        if dry_run:
            log.info("repair_events_dedup.dry_run", **counts)
            for ids_key, group in dup_groups.items():
                print(f"  dup group {ids_key}: ids={sorted(r['id'] for r in group)}")
            for r in narrative_rows:
                print(f"  narrative-looking event id={r['id']} item_id={r['item_id']} title={r['title'][:80]!r}")
            return counts

        for event_id, merged in to_update:
            cur.execute(
                "UPDATE events SET date=%(date)s, amount_usd=%(amount_usd)s, currency=%(currency)s, "
                "customer=%(customer)s, program=%(program)s, summary_he=%(summary_he)s, "
                "parties=%(parties)s, confidence=%(confidence)s, updated_at=now() WHERE id=%(id)s",
                {**merged, "id": event_id},
            )
        if to_delete:
            cur.execute("DELETE FROM events WHERE id = ANY(%(ids)s)", {"ids": to_delete})
        if delete_narrative and narrative_rows:
            narrative_ids = [r["id"] for r in narrative_rows]
            cur.execute("DELETE FROM events WHERE id = ANY(%(ids)s)", {"ids": narrative_ids})
            counts["narrative_titles_deleted"] = len(narrative_ids)
        conn.commit()

    log.info("repair_events_dedup.complete", **counts)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="compute and print counts only")
    parser.add_argument(
        "--delete-narrative",
        action="store_true",
        help="also delete existing narrative/assessment-sentence-titled events (reported only by default)",
    )
    args = parser.parse_args()

    counts = run_repair(dry_run=args.dry_run, delete_narrative=args.delete_narrative)
    mode = "DRY RUN" if args.dry_run else "APPLIED"
    print(
        f"\n{'=' * 60}"
        f"\nEvents Dedup Repair Summary ({mode}):"
        f"\n  Rows scanned:              {counts['rows_scanned']}"
        f"\n  Duplicate groups:          {counts['duplicate_groups']}"
        f"\n  Duplicates deleted:        {counts['duplicates_deleted']}"
        f"\n  Narrative titles found:    {counts['narrative_titles_found']}"
        f"\n  Narrative titles deleted:  {counts['narrative_titles_deleted']}"
        f"\n{'=' * 60}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
