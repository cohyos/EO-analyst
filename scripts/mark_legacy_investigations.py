#!/usr/bin/env python
"""Q3-5 (docs/qa/findings_Q3_r2.md) repair: label pre-provenance ``deep_search`` investigations and
fix two outcome/sources inconsistencies.

Two independent passes, in order:

1. **Legacy "no sources documented" label.** Older ``deep_search`` jobs ran before
   ``investigation_log`` gained its ``url``/``title`` columns (Q3-5 r1,
   ``db/migrations/versions/0015_investigation_log_sources.py``) -- their ``jobs.result`` (an
   ``InvestigationOut``-shaped JSON payload) has an empty ``sources`` list *and* their
   ``investigation_log`` rows have no ``url`` at all, so there is nothing left to backfill from
   (the finding: these can't be recovered, only labelled honestly). For every such job, appends
   ``" (מקורות לא תועדו בגרסה זו)"`` to ``result.what_was_tried_he`` (idempotent -- skipped if
   already present) and sets ``result.legacy_no_sources = true`` so the UI can flag the
   investigation card instead of silently showing zero sources as if none had ever been read.

2. **`not_found` + non-empty `sources` -> `partial`.** A handful of older jobs (the finding calls
   out ids 15/20) recorded ``outcome="not_found"`` despite `sources` being non-empty -- the model
   *did* read something, it just didn't call it a finding. Reclassified to
   ``outcome="partial"``, ``confidence=0.5`` (a conservative, documented value -- not a re-run of
   the model), leaving every other field (`answer_he`, `sources`, `what_was_tried_he`) untouched.

Idempotent -- safe to re-run.

Usage:
    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \
    PYTHONPATH=agent python scripts/mark_legacy_investigations.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, "agent")

import structlog

log = structlog.get_logger(__name__)

LEGACY_NOTE_HE = " (מקורות לא תועדו בגרסה זו)"
PARTIAL_CONFIDENCE_FALLBACK = 0.5


def _find_legacy_no_source_jobs(cur: Any) -> list[dict[str, Any]]:
    """``deep_search`` jobs with an empty `result.sources` and zero `investigation_log` rows
    carrying a `url` -- there is nothing to backfill `sources` from, so this can only be labelled,
    not fixed (Q3-5 r1's confidence-cap/sources-persisted fix already covers every job *after*
    that column existed)."""
    cur.execute(
        """
        SELECT j.id, j.result
        FROM jobs j
        WHERE j.kind = 'deep_search'
          AND j.result IS NOT NULL
          AND COALESCE(jsonb_array_length(j.result -> 'sources'), 0) = 0
          AND NOT EXISTS (
              SELECT 1 FROM investigation_log il WHERE il.job_id = j.id AND il.url IS NOT NULL
          )
        ORDER BY j.id
        """
    )
    return cur.fetchall()


def _find_not_found_with_sources(cur: Any) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT id, result FROM jobs
        WHERE kind = 'deep_search' AND result IS NOT NULL
          AND result ->> 'outcome' = 'not_found'
          AND COALESCE(jsonb_array_length(result -> 'sources'), 0) > 0
        ORDER BY id
        """
    )
    return cur.fetchall()


def apply_legacy_label(result: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Pure transform (no DB): ``(new_result, changed)``. Idempotent -- a `result` already
    labelled (``legacy_no_sources`` true and the note already present in `what_was_tried_he`) is
    returned unchanged with ``changed=False``."""
    result = dict(result)
    if result.get("legacy_no_sources") and LEGACY_NOTE_HE in (result.get("what_was_tried_he") or ""):
        return result, False
    tried = result.get("what_was_tried_he") or ""
    result["what_was_tried_he"] = tried if LEGACY_NOTE_HE in tried else f"{tried}{LEGACY_NOTE_HE}"
    result["legacy_no_sources"] = True
    return result, True


def apply_not_found_to_partial(result: dict[str, Any]) -> dict[str, Any]:
    """Pure transform (no DB): reclassify a `not_found`-with-`sources` result to `partial` at the
    documented fallback confidence (:data:`PARTIAL_CONFIDENCE_FALLBACK`) -- every other field is
    left untouched."""
    result = dict(result)
    result["outcome"] = "partial"
    result["confidence"] = PARTIAL_CONFIDENCE_FALLBACK
    return result


def run_repair(*, dry_run: bool = False) -> dict[str, Any]:
    from eoa.db import connection

    with connection() as conn, conn.cursor() as cur:
        legacy_jobs = _find_legacy_no_source_jobs(cur)
        legacy_report = []
        for job in legacy_jobs:
            result, changed = apply_legacy_label(job["result"] or {})
            if not changed:
                continue
            legacy_report.append({"job_id": job["id"], "what_was_tried_he": result["what_was_tried_he"]})
            if not dry_run:
                cur.execute(
                    "UPDATE jobs SET result = %(result)s, updated_at = now() WHERE id = %(id)s",
                    {"result": _to_json(result), "id": job["id"]},
                )

        not_found_jobs = _find_not_found_with_sources(cur)
        outcome_report = []
        for job in not_found_jobs:
            before = job["result"] or {}
            result = apply_not_found_to_partial(before)
            outcome_report.append(
                {"job_id": job["id"], "before_outcome": "not_found", "after_outcome": "partial",
                 "before_confidence": before.get("confidence"), "after_confidence": PARTIAL_CONFIDENCE_FALLBACK}
            )
            if not dry_run:
                cur.execute(
                    "UPDATE jobs SET result = %(result)s, updated_at = now() WHERE id = %(id)s",
                    {"result": _to_json(result), "id": job["id"]},
                )

        if not dry_run:
            conn.commit()

    return {
        "legacy_no_sources_jobs": legacy_report,
        "not_found_reclassified": outcome_report,
    }


def _to_json(result: dict[str, Any]) -> Any:
    from psycopg.types.json import Json

    return Json(result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report the plan without writing it")
    args = parser.parse_args()

    report = run_repair(dry_run=args.dry_run)
    mode = "DRY RUN" if args.dry_run else "APPLIED"

    print(f"\n{'=' * 70}\nQ3-5 legacy investigations repair ({mode})\n{'=' * 70}")
    print(f"\nlegacy_no_sources labelled: {len(report['legacy_no_sources_jobs'])}")
    for r in report["legacy_no_sources_jobs"]:
        print(f"  job_id={r['job_id']}")
    print(f"\nnot_found -> partial reclassified: {len(report['not_found_reclassified'])}")
    for r in report["not_found_reclassified"]:
        print(f"  job_id={r['job_id']} confidence {r['before_confidence']} -> {r['after_confidence']}")
    print(f"\n{'=' * 70}")
    if args.dry_run:
        print("(dry run -- nothing written; re-run without --dry-run to apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
