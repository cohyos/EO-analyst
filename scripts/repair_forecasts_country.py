#!/usr/bin/env python
"""Q3-11 (docs/qa/findings_Q3_r1.md) repair: recompute `tender_forecasts.buyer_country` for
existing rows (all 8 came back the literal string `'other'`, since the old code only ever read
the raw, mostly-unset `items.geography` value) and flag rows whose `rationale_he` is the
deterministic-fallback template as `needs_regen = true`, so the nightly `forecast_tenders` run
(`eoa.tenders.forecast._regenerate_flagged_forecasts`) picks them up for a real LLM rationale.

`buyer_country` is recomputed with the same priority order `eoa.tenders.forecast._resolve_buyer_country`
now uses for new candidates: (1) `entities.country` for the row's trigger items; (2) a country
name mentioned in those items' own text; (3) a country name mentioned in the existing
`rationale_he`. A row that still resolves to nothing better than `'other'` is left as `'other'`
-- never invented.

Third pass -- **stale platform match** (the finding's own named example, forecast id 10):
re-checks each row's stored trigger-item text against the *current* `platform_payloads.yaml`
match rules for that row's own platform label. Row 10 ("מסוק קרב"/attack_helicopter, trigger
item 309 "Tekever acquires Flowcopter") only ever matched because the article mentions "Apache"
once, in passing, as an unrelated "UK Apache teaming concept" aside -- the article's actual
subject is a cargo drone acquisition, not a helicopter deal. `platform_payloads.yaml`'s
`attack_helicopter` match list was tightened (bare "Apache" -> "Apache helicopter") to close this
false-positive class going forward; a row whose trigger text no longer matches its own platform
under the corrected rules is reported, and deleted only with `--delete-stale-platform`.

Usage:
    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \
    PYTHONPATH=agent python scripts/repair_forecasts_country.py [--dry-run] [--delete-stale-platform]
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, "agent")

import structlog

log = structlog.get_logger(__name__)

#: The exact tail of eoa.tenders.forecast._fallback_rationale's deterministic template -- a row
#: whose rationale_he contains this was never actually written by the LLM.
_FALLBACK_SIGNATURE_HE = "נימוק זה נוצר באופן דטרמיניסטי"


def _recompute_buyer_country(row: dict[str, Any], conn: Any) -> str:
    from eoa.report.geography import UNKNOWN_COUNTRY, country_mentions_in_text
    from eoa.tenders.forecast import _country_from_entities, _item_ids_from_sources

    current = row.get("buyer_country") or UNKNOWN_COUNTRY
    if current != UNKNOWN_COUNTRY:
        return current

    item_ids = _item_ids_from_sources(row.get("sources"))
    country = _country_from_entities(item_ids)
    if country:
        return country

    if item_ids:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT title, clean_text, summary_he FROM items WHERE id = ANY(%(ids)s)", {"ids": item_ids}
            )
            item_rows = cur.fetchall()
        combined = " ".join(str(x) for r in item_rows for x in (r.get("title"), r.get("summary_he")) if x)
        mentions = country_mentions_in_text(combined)
        if mentions:
            return mentions[0]

    mentions = country_mentions_in_text(row.get("rationale_he") or "")
    if mentions:
        return mentions[0]
    return current


def _stale_platform_match(row: dict[str, Any], conn: Any) -> bool:
    """Q3-11 (forecast id 10): true if the row's own trigger-item text no longer matches its own
    platform under the *current* `platform_payloads.yaml` rules -- i.e. the row was only ever
    created because of a since-tightened, overly-broad keyword (bare "Apache" et al.)."""
    from eoa.tenders.forecast import _item_ids_from_sources, load_platform_payloads

    platforms_by_label = {p.category_he: p for p in load_platform_payloads()}
    spec = platforms_by_label.get(row.get("platform"))
    if spec is None:
        return False  # unrecognized label -- don't touch what we can't identify
    item_ids = _item_ids_from_sources(row.get("sources"))
    if not item_ids:
        return False
    with conn.cursor() as cur:
        cur.execute(
            "SELECT title, clean_text, summary_he FROM items WHERE id = ANY(%(ids)s)", {"ids": item_ids}
        )
        item_rows = cur.fetchall()
    combined = " ".join(
        str(x) for r in item_rows for x in (r.get("title"), r.get("clean_text"), r.get("summary_he")) if x
    )
    return not spec.matches(combined)


def run_repair(*, dry_run: bool = False, delete_stale_platform: bool = False) -> dict[str, Any]:
    from eoa.db import connection

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, platform, buyer_country, sources, rationale_he, needs_regen FROM tender_forecasts ORDER BY id"
        )
        rows = cur.fetchall()

        country_updates: list[tuple[int, str, str]] = []  # (id, old, new)
        regen_flags: list[int] = []
        stale_platform_ids: list[int] = []
        for row in rows:
            new_country = _recompute_buyer_country(row, conn)
            old_country = row.get("buyer_country")
            if new_country != old_country:
                country_updates.append((row["id"], old_country, new_country))
            if not row.get("needs_regen") and _FALLBACK_SIGNATURE_HE in (row.get("rationale_he") or ""):
                regen_flags.append(row["id"])
            if _stale_platform_match(row, conn):
                stale_platform_ids.append(row["id"])

        counts = {
            "rows_scanned": len(rows),
            "country_updated": len(country_updates),
            "flagged_needs_regen": len(regen_flags),
            "stale_platform_found": len(stale_platform_ids),
            "stale_platform_deleted": 0,
        }

        if dry_run:
            log.info("repair_forecasts_country.dry_run", **counts)
            for fid, old, new in country_updates:
                print(f"  forecast {fid}: buyer_country {old!r} -> {new!r}")
            for fid in regen_flags:
                print(f"  forecast {fid}: flagged needs_regen=true (fallback rationale)")
            for fid in stale_platform_ids:
                print(
                    f"  forecast {fid}: stale platform match (trigger text no longer matches its own platform)"
                )
            return counts

        for fid, _old, new_country in country_updates:
            cur.execute(
                "UPDATE tender_forecasts SET buyer_country = %(country)s, updated_at = now() WHERE id = %(id)s",
                {"country": new_country, "id": fid},
            )
        if regen_flags:
            cur.execute(
                "UPDATE tender_forecasts SET needs_regen = true, updated_at = now() WHERE id = ANY(%(ids)s)",
                {"ids": regen_flags},
            )
        if delete_stale_platform and stale_platform_ids:
            cur.execute("DELETE FROM tender_forecasts WHERE id = ANY(%(ids)s)", {"ids": stale_platform_ids})
            counts["stale_platform_deleted"] = len(stale_platform_ids)
        conn.commit()

    log.info("repair_forecasts_country.complete", **counts)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="compute and print counts only")
    parser.add_argument(
        "--delete-stale-platform",
        action="store_true",
        help="also delete rows whose trigger text no longer matches their own platform under the current config (reported only by default)",
    )
    args = parser.parse_args()

    counts = run_repair(dry_run=args.dry_run, delete_stale_platform=args.delete_stale_platform)
    mode = "DRY RUN" if args.dry_run else "APPLIED"
    print(
        f"\n{'=' * 60}"
        f"\nTender Forecasts Repair Summary ({mode}):"
        f"\n  Rows scanned:            {counts['rows_scanned']}"
        f"\n  buyer_country updated:   {counts['country_updated']}"
        f"\n  Flagged needs_regen:     {counts['flagged_needs_regen']}"
        f"\n  Stale platform found:    {counts['stale_platform_found']}"
        f"\n  Stale platform deleted:  {counts['stale_platform_deleted']}"
        f"\n{'=' * 60}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
