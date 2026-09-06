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
from eoa.payloads.models import CATEGORIES  # noqa: E402

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
    family = (entry.get("family") or "").strip() or None
    category = entry.get("category") or "other"
    if category not in CATEGORIES:
        log.warning("seed_payloads_invalid_category", canonical_name=canonical_name, category=category)
        category = "other"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO payloads (canonical_name, vendor_entity_name, family, category, first_seen, last_seen)
            VALUES (%(name)s, %(vendor)s, %(family)s, %(category)s, %(today)s, %(today)s)
            ON CONFLICT (canonical_name) DO UPDATE SET
                vendor_entity_name = COALESCE(payloads.vendor_entity_name, EXCLUDED.vendor_entity_name),
                family = COALESCE(payloads.family, EXCLUDED.family),
                last_seen = EXCLUDED.last_seen
            RETURNING id
            """,
            {
                "name": canonical_name,
                "vendor": vendor,
                "family": family,
                "category": category,
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
