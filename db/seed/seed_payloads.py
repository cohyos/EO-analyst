"""Seed `payloads` (identity only -- name/vendor/family/category) from config/payloads_seed.yaml.

A17: NO spec numbers, NO prices here -- those only ever come from a cited source via
`eoa.payloads.extract.run_payload_extract`. This script just gives the system a starting set of
well-known EO payload families to recognize, mirroring `db/seed/seed_watchlist.py`'s role for
companies/programs/conferences.

Idempotent: re-running never overwrites an already-set `vendor_entity_name`/`family`/`category`
with a blank/default one (same non-destructive-backfill convention as
`eoa.patents.scan._backfill_patent_fields`), and never touches `payload_spec_versions`/
`payload_price_refs` at all.

Usage:
    python db/seed/seed_payloads.py
(run with the repo root on PYTHONPATH, or with `agent/` importable -- this script inserts
`agent/` onto sys.path itself so it also works standalone.)
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

import structlog
import yaml

_AGENT_DIR = Path(__file__).resolve().parents[2] / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from eoa.db import connection  # noqa: E402
from eoa.payloads.models import CATEGORIES, parse_family_variant  # noqa: E402

log = structlog.get_logger(__name__)

PAYLOADS_SEED_PATH = Path(__file__).resolve().parents[2] / "config" / "payloads_seed.yaml"


def _load_seed(path: Path = PAYLOADS_SEED_PATH) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return list(data.get("payloads") or [])


def _upsert_payload(entry: dict[str, Any], today: dt.date) -> int:
    canonical_name = (entry.get("canonical_name") or "").strip()
    if not canonical_name:
        raise ValueError("payloads_seed.yaml entry missing canonical_name")
    vendor = (entry.get("vendor_entity_name") or "").strip() or None
    # W19b (docs/REVIEW_2026-09-06_evening.md, migration 0024): deterministic family/variant
    # split -- `eoa.payloads.models.parse_family_variant` (tested against all 62 seed names in
    # tests/unit/test_payload_families.py) is the authoritative source for both, so several
    # variants of one product line always land under one consistent, collapsible tree family
    # (e.g. "Trakka TC-300"/"TrakkaCam TC-215" -> family "TC"; "Elbit DCoMPASS" stays its own
    # family, distinct from "Elbit CoMPASS" -- exactly the two families the user named in the
    # requirement). config/payloads_seed.yaml's own pre-W19b `family:` field predates this
    # parser and is coarser/inconsistent (e.g. it had literally grouped "Collins Aerospace
    # DB-110" under family "DB-110" -- a single-variant "family" identical to the variant, and
    # "Trakka TC-300" under family "TrakkaCam" -- the vendor name, not a product line) -- it is
    # only ever consulted as a last-resort fallback for a name the parser can't do anything with.
    parsed_family, parsed_variant = parse_family_variant(canonical_name)
    family = parsed_family or (entry.get("family") or "").strip() or None
    variant = parsed_variant or (entry.get("variant") or "").strip() or None
    category = entry.get("category") or "other"
    if category not in CATEGORIES:
        log.warning("seed_payloads_invalid_category", canonical_name=canonical_name, category=category)
        category = "other"
    # W19 (migration 0022): image/spec-sheet reference -- identity-level, never versioned (see
    # the migration's docstring). Blank/missing stays NULL, never an empty string, and an
    # already-set value is never overwritten by a blank one (same COALESCE convention as
    # vendor/family above) so a later manual correction in the DB is never clobbered by re-seeding.
    image_url = (entry.get("image_url") or "").strip() or None
    spec_url = (entry.get("spec_url") or "").strip() or None
    spec_source = (entry.get("spec_source") or "").strip() or None
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO payloads
                (canonical_name, vendor_entity_name, family, variant, category, image_url, spec_url, spec_source,
                 first_seen, last_seen)
            VALUES
                (%(name)s, %(vendor)s, %(family)s, %(variant)s, %(category)s, %(image_url)s, %(spec_url)s, %(spec_source)s,
                 %(today)s, %(today)s)
            ON CONFLICT (canonical_name) DO UPDATE SET
                vendor_entity_name = COALESCE(payloads.vendor_entity_name, EXCLUDED.vendor_entity_name),
                -- family/variant (W19b) are always re-derived from `canonical_name` by the pure,
                -- deterministic parser (never invented/guessed) -- unlike vendor/image/spec,
                -- which are externally-sourced facts a re-seed must never clobber, so these two
                -- are force-set from EXCLUDED rather than COALESCE-preserved. A real per-name
                -- exception belongs in `eoa.payloads.models.FAMILY_OVERRIDES`, not a hand-edited
                -- DB row that a later re-seed would otherwise silently keep out of sync with it.
                family = EXCLUDED.family,
                variant = EXCLUDED.variant,
                image_url = COALESCE(payloads.image_url, EXCLUDED.image_url),
                spec_url = COALESCE(payloads.spec_url, EXCLUDED.spec_url),
                spec_source = COALESCE(payloads.spec_source, EXCLUDED.spec_source),
                last_seen = EXCLUDED.last_seen
            RETURNING id
            """,
            {
                "name": canonical_name,
                "vendor": vendor,
                "family": family,
                "variant": variant,
                "category": category,
                "image_url": image_url,
                "spec_url": spec_url,
                "spec_source": spec_source,
                "today": today,
            },
        )
        return cur.fetchone()["id"]


def seed_payloads(path: Path = PAYLOADS_SEED_PATH) -> int:
    """Upsert every entry in ``path``; returns the number of rows processed."""
    entries = _load_seed(path)
    today = dt.date.today()
    count = 0
    for entry in entries:
        try:
            _upsert_payload(entry, today)
            count += 1
        except Exception as exc:
            log.warning("seed_payloads_entry_failed", entry=entry.get("canonical_name"), error=str(exc)[:200])
    log.info("seed_payloads_done", count=count, total=len(entries))
    return count


if __name__ == "__main__":
    seed_payloads()
