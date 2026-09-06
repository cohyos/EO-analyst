#!/usr/bin/env python
"""Q3-5 (docs/qa/findings_Q3_r1.md) backfill: rebuild `sources` for `deep_search` jobs whose
`jobs.result.sources` came back `[]` despite a page having actually been read.

Root cause (fixed going forward in `eoa.search.deep_search`): the ReAct loop's `finish` handler
used to keep only the intersection of the model's own claimed `sources` list with the URLs it
actually read -- so a model that read a page but forgot (or mis-formatted the URL) to list it in
`finish` produced an empty `sources` list despite a page having actually been read. 14/18
historical `deep_search` jobs hit this.

This script is a **best-effort** backfill against `investigation_log`, which -- before migration
0015 added `investigation_log.url`/`.title` -- never recorded *which* URL a successful `fetch`
round actually read, only *that* one happened (`engine='fetch', outcome='partial'`). So for a job
whose successful reads all predate that migration, the exact URL cannot be recovered (this script
never invents one, per docs/CONVENTIONS.md rule 5); it still applies the confidence-cap and
"unverified" corrections to the job's persisted result, and reports the job as "irrecoverable" for
sources specifically. A job with at least one post-migration `investigation_log.url` value gets
`sources` rebuilt from those.

For every ``deep_search`` job with ``result.sources == []`` this script, for the job's ``result``:

1. Rebuilds ``sources`` from `investigation_log` rows for that job with ``engine='fetch'``,
   ``outcome='partial'`` and a non-null ``url`` (post-migration-0015 data only) -- deduplicated,
   in round/id order.
2. Applies the same confidence caps ``eoa.search.deep_search`` now enforces on every finish path:
   ``not_found`` capped at 0.3, ``partial`` with fewer than 2 sources capped at 0.7.
3. Prefixes ``answer_he`` with "לא אומת: " when the outcome is ``partial`` and no source could be
   recovered (rebuilt or already present) -- matching the code fix's contract that an unsourced
   partial claim must say so.

Usage:
    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \
    PYTHONPATH=agent python scripts/repair_investigation_sources.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, "agent")

import structlog
from psycopg.types.json import Json

log = structlog.get_logger(__name__)

NOT_FOUND_MAX_CONFIDENCE = 0.3
PARTIAL_SINGLE_SOURCE_MAX_CONFIDENCE = 0.7
PARTIAL_MIN_SOURCES_FOR_HIGH_CONFIDENCE = 2
UNVERIFIED_PREFIX_HE = "לא אומת: "


def _recovered_sources(conn: Any, job_id: int) -> list[str]:
    """URLs actually read for `job_id`, from `investigation_log.url` (post-migration-0015 rows
    only -- older rows never recorded a URL for a successful read)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT url FROM investigation_log
            WHERE job_id = %(job_id)s AND engine = 'fetch' AND outcome = 'partial' AND url IS NOT NULL
            ORDER BY url
            """,
            {"job_id": job_id},
        )
        return [r["url"] for r in cur.fetchall() if r.get("url")]


def _had_successful_read(conn: Any, job_id: int) -> bool:
    """True if at least one `read` round succeeded for `job_id`, even if its URL wasn't recorded
    (pre-migration-0015 data) -- distinguishes "irrecoverable" from "genuinely no reads"."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM investigation_log WHERE job_id = %(job_id)s AND engine = 'fetch' AND outcome = 'partial' LIMIT 1",
            {"job_id": job_id},
        )
        return cur.fetchone() is not None


def compute_fix(job_id: int, result: dict[str, Any], conn: Any) -> tuple[dict[str, Any], str]:
    """Returns ``(new_result, status)``; ``status`` is one of 'unchanged', 'sources_recovered',
    'capped_only', 'irrecoverable' (a successful read happened but its URL can't be recovered)."""
    if result.get("sources"):
        return result, "unchanged"  # not one of the broken jobs

    new_result = dict(result)
    recovered = _recovered_sources(conn, job_id)
    status = "unchanged"
    if recovered:
        new_result["sources"] = recovered
        status = "sources_recovered"
    elif _had_successful_read(conn, job_id):
        status = "irrecoverable"

    outcome = new_result.get("outcome")
    confidence = new_result.get("confidence")
    if isinstance(confidence, int | float):
        if outcome == "not_found" and confidence > NOT_FOUND_MAX_CONFIDENCE:
            new_result["confidence"] = NOT_FOUND_MAX_CONFIDENCE
            if status == "unchanged":
                status = "capped_only"
        elif (
            outcome == "partial"
            and len(new_result.get("sources") or []) < PARTIAL_MIN_SOURCES_FOR_HIGH_CONFIDENCE
            and confidence > PARTIAL_SINGLE_SOURCE_MAX_CONFIDENCE
        ):
            new_result["confidence"] = PARTIAL_SINGLE_SOURCE_MAX_CONFIDENCE
            if status == "unchanged":
                status = "capped_only"

    if outcome == "partial" and not new_result.get("sources"):
        answer = new_result.get("answer_he") or ""
        if not answer.startswith(UNVERIFIED_PREFIX_HE):
            new_result["answer_he"] = f"{UNVERIFIED_PREFIX_HE}{answer}"
            if status == "unchanged":
                status = "capped_only"

    return new_result, status


def run_repair(*, dry_run: bool = False) -> dict[str, Any]:
    from eoa.db import connection

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, result FROM jobs WHERE kind = 'deep_search' AND result IS NOT NULL ORDER BY id"
        )
        jobs = cur.fetchall()

        broken = [j for j in jobs if not (j.get("result") or {}).get("sources")]
        counts = {
            "jobs_scanned": len(jobs),
            "jobs_with_empty_sources": len(broken),
            "sources_recovered": 0,
            "capped_only": 0,
            "irrecoverable": 0,
            "unchanged": 0,
        }
        updates: list[tuple[int, dict[str, Any]]] = []
        details: list[str] = []
        for job in broken:
            new_result, status = compute_fix(job["id"], job["result"] or {}, conn)
            counts[status] = counts.get(status, 0) + 1
            details.append(f"  job {job['id']}: {status}")
            if status != "unchanged":
                updates.append((job["id"], new_result))

        if dry_run:
            log.info("repair_investigation_sources.dry_run", **counts)
            for line in details:
                print(line)
            return counts

        for job_id, new_result in updates:
            cur.execute(
                "UPDATE jobs SET result = %(result)s WHERE id = %(id)s",
                {"result": Json(new_result), "id": job_id},
            )
        conn.commit()

    log.info("repair_investigation_sources.complete", **counts)
    for line in details:
        print(line)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="compute and print counts only")
    args = parser.parse_args()

    counts = run_repair(dry_run=args.dry_run)
    mode = "DRY RUN" if args.dry_run else "APPLIED"
    print(
        f"\n{'=' * 60}"
        f"\nDeep-Search Sources Repair Summary ({mode}):"
        f"\n  Jobs scanned:              {counts['jobs_scanned']}"
        f"\n  Jobs with sources=[]:      {counts['jobs_with_empty_sources']}"
        f"\n  Sources recovered:         {counts['sources_recovered']}"
        f"\n  Capped/prefixed only:      {counts['capped_only']}"
        f"\n  Irrecoverable (no URL):    {counts['irrecoverable']}"
        f"\n  Unchanged:                 {counts['unchanged']}"
        f"\n{'=' * 60}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
