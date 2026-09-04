"""Seed `entities` and `conferences` from config/watchlist.yaml.

Companies become entities of kind 'company', programs become entities of kind
'program'. Conferences from `conferences_seed` are upserted with
`status='estimated'` (never overwriting a manually confirmed status) and a
`start_date` computed as the next plausible occurrence relative to today,
from each entry's `month` and optional `cadence`.

Usage:
    python db/seed/seed_watchlist.py
(run with the repo root on PYTHONPATH, or with `agent/` importable -- this
script inserts `agent/` onto sys.path itself so it also works standalone.)
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
from eoa.memory.relational import upsert_entity  # noqa: E402

log = structlog.get_logger(__name__)

WATCHLIST_PATH = Path(__file__).resolve().parents[2] / "config" / "watchlist.yaml"


def _load_watchlist(path: Path = WATCHLIST_PATH) -> dict[str, Any]:
    """Load and parse config/watchlist.yaml."""
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _next_occurrence(month: int, cadence: str | None, today: dt.date) -> dt.date:
    """Compute the next plausible occurrence date (day 1 of `month`) for a recurring conference.

    - No cadence (or unrecognized cadence): treated as annual.
    - 'biennial_odd' / 'biennial_even': next year with matching month whose
      year has the required parity.
    - 'biennial' (parity unspecified in config): since no anchor year is
      available to determine odd/even alignment, this falls back to the same
      "next annual slot" logic as the unqualified case -- a documented
      limitation, not a guarantee the conference is actually happening that
      year.
    """
    candidate_year = today.year if today.month <= month else today.year + 1

    if cadence == "biennial_odd":
        while candidate_year % 2 == 0:
            candidate_year += 1
    elif cadence == "biennial_even":
        while candidate_year % 2 != 0:
            candidate_year += 1
    # 'biennial' and None: no further adjustment (see docstring).

    return dt.date(candidate_year, month, 1)


def seed_companies(data: dict[str, Any]) -> list[int]:
    """Upsert every watchlist company as an entity of kind 'company'."""
    ids = []
    for company in data.get("companies", []):
        entity_id = upsert_entity(
            name=company["name"],
            kind="company",
            country=company.get("country"),
            aliases=company.get("aliases") or [],
            focus=company.get("focus") or [],
            notes=company.get("note"),
        )
        ids.append(entity_id)
    log.info("seed.companies_done", count=len(ids))
    return ids


def seed_programs(data: dict[str, Any]) -> list[int]:
    """Upsert every watchlist program as an entity of kind 'program'."""
    ids = []
    for program in data.get("programs", []):
        entity_id = upsert_entity(
            name=program["name"],
            kind="program",
            country=None,
            aliases=program.get("aliases") or [],
            focus=program.get("focus") or [],
            notes=f"owner: {program['owner']}" if program.get("owner") else None,
        )
        ids.append(entity_id)
    log.info("seed.programs_done", count=len(ids))
    return ids


def seed_conferences(data: dict[str, Any], today: dt.date | None = None) -> list[int]:
    """Upsert every `conferences_seed` entry, computing its next occurrence date."""
    today = today or dt.date.today()
    ids = []
    query = """
        INSERT INTO conferences (name, city, relevance, cadence, start_date, registration_url, organizer, status)
        VALUES (%(name)s, %(city)s, %(relevance)s, %(cadence)s, %(start_date)s, %(registration_url)s, %(organizer)s, 'estimated')
        ON CONFLICT (name) DO UPDATE SET
            city = EXCLUDED.city,
            relevance = EXCLUDED.relevance,
            cadence = EXCLUDED.cadence,
            start_date = EXCLUDED.start_date,
            registration_url = CASE WHEN conferences.registration_url IS NULL
                                    THEN EXCLUDED.registration_url ELSE conferences.registration_url END,
            organizer = CASE WHEN conferences.organizer IS NULL
                             THEN EXCLUDED.organizer ELSE conferences.organizer END,
            status = CASE WHEN conferences.status = 'confirmed'
                          THEN conferences.status ELSE 'estimated' END
        RETURNING id
    """
    with connection() as conn, conn.cursor() as cur:
        for conf in data.get("conferences_seed", []):
            next_date = _next_occurrence(conf["month"], conf.get("cadence"), today)
            cur.execute(
                query,
                {
                    "name": conf["name"],
                    "city": conf.get("city"),
                    "relevance": conf.get("relevance"),
                    "cadence": conf.get("cadence"),
                    "start_date": next_date,
                    "registration_url": conf.get("url"),
                    "organizer": conf.get("organizer"),
                },
            )
            ids.append(cur.fetchone()["id"])
    log.info("seed.conferences_done", count=len(ids))
    return ids


def main() -> None:
    """Seed entities and conferences from config/watchlist.yaml into the database."""
    data = _load_watchlist()
    seed_companies(data)
    seed_programs(data)
    seed_conferences(data)


if __name__ == "__main__":
    main()
