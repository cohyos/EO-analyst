#!/usr/bin/env python
"""A12 (מעקב טכנולוגי, 2026-09-06): one-off restricted live ingest of the 9 new tech_dev sources.

`eo run ingest` (agent/eoa/cli.py) has no `--source-ids` flag, so per the task's fallback this
calls `eoa.fetch.service.run_ingest(source_ids=[...])` directly, restricted to just the tech_dev
source ids (resolved from their config/sources.yaml slugs via `upsert_sources_to_db`) -- every
other configured source is left untouched, and no LLM/classify/analyze/triage stage runs (fetch +
sanitize + store only), so this does not touch the resource gate at all.

Usage:
    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \\
    PYTHONPATH=agent python scripts/ingest_tech_sources.py
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "agent")

TECH_SOURCE_SLUGS = [
    "arxiv_eess_iv_tech",
    "arxiv_cs_cv_tech",
    "arxiv_physics_optics_tech",
    "arxiv_physics_ins_det_tech",
    "ieee_sensors_journal_toc",
    "ieee_tgrs_toc",
    "laser_focus_world_tech",
    "vision_systems_design_tech",
    "nature_photonics_tech",
]


async def main() -> None:
    from eoa.fetch.service import run_ingest
    from eoa.fetch.sources_loader import load_sources, upsert_sources_to_db

    sources = load_sources()
    id_map = upsert_sources_to_db(sources)
    wanted_db_ids = [id_map[slug] for slug in TECH_SOURCE_SLUGS if slug in id_map]
    missing = [slug for slug in TECH_SOURCE_SLUGS if slug not in id_map]
    if missing:
        print(f"WARNING: slugs not found in config/sources.yaml: {missing}")

    print(f"Ingesting {len(wanted_db_ids)} tech_dev sources (since_days=14, wider than the default "
          f"3 to give the arXiv/journal feeds a real first-run sample)...")
    stats = await run_ingest(source_ids=wanted_db_ids, since_days=14)

    print("-" * 78)
    print(f"sources_attempted: {stats.sources_attempted}")
    print(f"sources_failed:    {stats.sources_failed}")
    print(f"entries_seen:      {stats.entries_seen}")
    print(f"items_inserted:    {stats.items_inserted}")
    print(f"items_skipped:     {stats.items_skipped}")
    if stats.errors:
        print("errors:")
        for e in stats.errors:
            print(f"  - {e}")


if __name__ == "__main__":
    asyncio.run(main())
