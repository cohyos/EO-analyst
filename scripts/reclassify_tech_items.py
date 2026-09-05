#!/usr/bin/env python
"""A12 (מעקב טכנולוגי, 2026-09-06): backfill existing items into the new `tech_dev` domain.

The `tech_dev` domain, its classification rule, and its keyword-filtered sources
(`config/sources.yaml`) are all new as of this feature -- every item ingested *before* today was
classified without any of that, so a genuine tech-watch item (an academic paper on a digital-pixel
FPA, say) that predates this feature is very likely sitting in `domain = 'out_of_scope'` (no
defense customer, so the old classify rule dropped it) or `level = 'archive'` (a low
novelty/magnitude/core_relevance triage score, since the old rubric has no notion of "in-scope
pure research").

This script finds that backlog and re-classifies it:

1. **Candidate pool**: every item with `domain = 'out_of_scope'` OR `level = 'archive'`.
2. **Keyword pre-filter** (cheap, in Python, no LLM call): candidates whose `title` or
   `clean_text` contains at least one of `TECH_KEYWORDS` (case-insensitive substring) -- the same
   subject-matter terms the classify.md tech-watch rule and the new sources' `keywords_any`
   filters use (FPA/digital-pixel/DROIC, SWIR/eSWIR, HOT MCT/T2SL, event camera, metasurface,
   on-sensor AI/ATR, LiDAR, super-resolution/turbulence mitigation, microbolometer). This is a
   recall-oriented pre-filter, not the classification decision itself -- the LLM (step 3) still
   makes the actual domain call and can say "no" (e.g. a story that merely mentions "SWIR" in
   passing about an unrelated platform).
3. **Re-classification** (`eoa.pipeline.classify.classify_item` + `persist_classification`, the
   exact same code path `run_classify` uses -- no bespoke prompt or schema): each keyword-matched
   candidate is re-classified against the *current* taxonomy/prompt. An item the LLM now places in
   `tech_dev` is persisted as such (domain/subdomain/report_kind/trl/entities, and this script also
   clears the old `level='archive'`/`score` so the item is picked up fresh by the next
   triage/analyze pass, exactly like a brand-new item would be); everything else keeps its
   original classification untouched (`--dry-run`, the default, changes nothing at all).

Usage (mirrors `scripts/repair_entity_relevance.py`):

    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \\
    PYTHONPATH=agent python scripts/reclassify_tech_items.py --dry-run

    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \\
    PYTHONPATH=agent python scripts/reclassify_tech_items.py --apply
"""

from __future__ import annotations

import argparse
import re
import sys

sys.path.insert(0, "agent")

import structlog

log = structlog.get_logger(__name__)

# Same subject-matter vocabulary as classify.md's tech-watch rule + the new sources'
# `keywords_any` filters (config/sources.yaml) -- kept as one place so both stay in sync in
# spirit, even though they live in different files for different reasons (a prompt rule vs. an
# RSS pre-filter vs. this one-off backfill's recall-oriented pre-filter).
TECH_KEYWORDS: list[str] = [
    "digital pixel",
    "digital-pixel",
    "droic",
    "focal plane array",
    "focal-plane array",
    "readout integrated circuit",
    "in-pixel adc",
    "swir",
    "eswir",
    "ingaas",
    "colloidal quantum dot",
    "hot mct",
    "t2sl",
    "xbn",
    "event camera",
    "event-based",
    "neuromorphic",
    "metasurface",
    "meta-optic",
    "flat optics",
    "freeform optics",
    "in-sensor",
    "on-sensor",
    "edge ai",
    "atr on fpga",
    "laser dazzler",
    "lidar",
    "super-resolution",
    "super resolution",
    "turbulence mitigation",
    "microbolometer",
    "uncooled detector",
    "uncooled infrared",
    "quantum dot photodetector",
]


# Word-boundary regexes, compiled once. A naive substring check on short/common terms like
# "lidar" or "swir" produces real false positives -- e.g. "lidar" matches inside "solidarity"
# (`consoLIDARity`), and this pre-filter's whole job is to be a *cheap, cheap-to-verify* recall
# net, not to burn an LLM call re-classifying items that only coincidentally contain the letters.
_TECH_KEYWORD_PATTERNS = [(kw, re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)) for kw in TECH_KEYWORDS]


def _keyword_match(title: str | None, clean_text: str | None) -> str | None:
    """Return the first matching keyword (whole-word/phrase match), or None."""
    haystack = f"{title or ''} {clean_text or ''}"
    for kw, pattern in _TECH_KEYWORD_PATTERNS:
        if pattern.search(haystack):
            return kw
    return None


def _fetch_candidates(cur) -> list[dict]:
    cur.execute(
        """
        SELECT id, url, title, clean_text, domain, subdomain, level, score, report_kind
        FROM items
        WHERE (domain = 'out_of_scope' OR level = 'archive')
          AND security_status = 'clean'
          AND dedup_of IS NULL
        ORDER BY id
        """
    )
    return cur.fetchall()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--apply", action="store_true", help="Actually re-classify and persist (default: dry-run only)."
    )
    parser.add_argument("--dry-run", action="store_true", help="Explicit alias for the default (no --apply).")
    parser.add_argument(
        "--limit", type=int, default=None, help="Cap the number of keyword-matched candidates processed."
    )
    parser.add_argument(
        "--role", default="resident", help="LLM role to classify with (default: resident, per config.yaml)."
    )
    args = parser.parse_args()
    apply = args.apply and not args.dry_run

    from eoa.db import connection
    from eoa.memory.relational import update_item_fields
    from eoa.pipeline.classify import classify_item, persist_classification

    with connection() as conn, conn.cursor() as cur:
        candidates = _fetch_candidates(cur)

    matched = []
    for item in candidates:
        kw = _keyword_match(item.get("title"), item.get("clean_text"))
        if kw:
            matched.append((item, kw))

    print(f"Candidate pool (domain=out_of_scope OR level=archive): {len(candidates)}")
    print(f"Keyword-matched (tech-watch vocabulary): {len(matched)}")

    if args.limit:
        matched = matched[: args.limit]
        print(f"Limited to first {len(matched)} for this run.")

    if not matched:
        print("Nothing to do.")
        return

    print()
    print(f"{'MODE: DRY RUN (no writes)' if not apply else 'MODE: APPLY (re-classifying + persisting)'}")
    print("-" * 78)

    moved_to_tech_dev = 0
    unchanged = 0
    failed = 0

    for item, kw in matched:
        label = f"[{item['id']}] {(item.get('title') or '(no title)')[:90]!r} (matched: {kw!r})"
        if not apply:
            print(f"WOULD RE-CLASSIFY: {label}")
            continue

        try:
            out = classify_item(item, role=args.role)
        except Exception as exc:
            print(f"FAILED: {label} -- {exc!r}")
            failed += 1
            continue

        if out.domain == "tech_dev":
            persist_classification(item["id"], out)
            # F reclassify: clear the stale archive/out_of_scope triage state so the item is
            # picked up fresh by the next triage/analyze pass, exactly like a brand-new item --
            # otherwise it would keep level='archive'/score from its original (wrong) triage run
            # forever, invisible to eoa.report.tech_watch's level-independent daily table but
            # never re-triaged either.
            update_item_fields(item["id"], level=None, score=None, triage_reason=None)
            moved_to_tech_dev += 1
            print(f"-> tech_dev/{out.subdomain or '(no subdomain)'}: {label}")
        else:
            unchanged += 1
            print(f"   still {out.domain}: {label}")

    print("-" * 78)
    print(f"Processed:        {len(matched)}")
    if apply:
        print(f"Moved to tech_dev: {moved_to_tech_dev}")
        print(f"Unchanged:         {unchanged}")
        print(f"Failed:            {failed}")
    else:
        print("(dry run -- nothing written; re-run with --apply to actually reclassify)")


if __name__ == "__main__":
    main()
