"""D9 -- tenders / forecasts / conferences deterministic checks (docs/QA_CONTINUOUS_LOOP.md D9).

Global (not per-sampled-item) checks over the current ``tenders``, ``conferences`` and ``sources``
tables -- these are small, config-driven tables where "the whole table" is the natural QA unit,
mirroring how ``scripts/purge_stale_tenders.py`` re-checks every row rather than a sample.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from eoa.qa.types import Check, DomainScore, weighted_score

_VALID_CONFERENCE_STATUSES = frozenset({"confirmed", "estimated", "past", "cancelled"})
_SOURCE_FRESHNESS_DAYS = 7


def score_D9(conn: Any) -> DomainScore:  # noqa: N802 -- score_Dn matches docs/QA_CONTINUOUS_LOOP.md naming
    """D9: tenders/conferences/sources deterministic checks over the whole current table state."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, status, deadline, published_at FROM tenders")
        tenders = cur.fetchall()
        cur.execute("SELECT id, status, start_date, organizer FROM conferences")
        conferences = cur.fetchall()
        cur.execute(
            "SELECT id, name, last_fetched_at FROM sources WHERE active = true"
        )
        sources = cur.fetchall()

    n = len(tenders) + len(conferences) + len(sources)
    if n == 0:
        return DomainScore(domain="D9", score_0_100=None, checks=[], n=0, note="empty tables")

    # 1. an 'open' tender with neither a deadline nor a published_at date carries no evidence it
    #    was ever actually verified against a live page (mirrors purge_stale_tenders.py's rule 5)
    #    -- it should have been stored as 'unknown', never 'open'.
    open_undated = [
        t["id"] for t in tenders if t["status"] == "open" and t["deadline"] is None and t["published_at"] is None
    ]

    # 2. conferences: status must be one of the known lifecycle values, start_date should be
    #    present for anything not cancelled, and a "1..N sequential day-of-month" pattern across
    #    the whole table is a red flag for synthetic/placeholder dates (never observed in a real
    #    scrape, where dates cluster around actual event calendars).
    status_bad = [c["id"] for c in conferences if c["status"] not in _VALID_CONFERENCE_STATUSES]
    missing_date = [c["id"] for c in conferences if c["status"] != "cancelled" and c["start_date"] is None]
    days = sorted(c["start_date"].day for c in conferences if c["start_date"] is not None)
    synthetic_sequence = len(days) >= 8 and days == list(range(1, len(days) + 1))

    # 3. every active source should have been fetched within the last week -- a source that never
    #    fetches is silently dead weight (or a scheduler regression) even if it's still marked
    #    active.
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=_SOURCE_FRESHNESS_DAYS)
    stale_sources = [s["id"] for s in sources if s["last_fetched_at"] is None or s["last_fetched_at"] < cutoff]

    checks = [
        Check(
            "tenders_open_rows_have_dates",
            passed=len(open_undated) == 0,
            weight=2.0,
            evidence=f"{len(tenders)} tenders; open-with-no-dates ids: {open_undated[:10]}",
        ),
        Check(
            "conferences_status_and_dates_real",
            passed=len(status_bad) == 0 and len(missing_date) == 0 and not synthetic_sequence,
            weight=2.0,
            evidence=(
                f"{len(conferences)} conferences; bad status ids: {status_bad[:10]}; "
                f"missing start_date ids: {missing_date[:10]}; synthetic day sequence: {synthetic_sequence}"
            ),
        ),
        Check(
            "sources_enabled_fetched_recently",
            passed=len(stale_sources) == 0,
            weight=1.0,
            evidence=(
                f"{len(sources) - len(stale_sources)}/{len(sources)} fetched within "
                f"{_SOURCE_FRESHNESS_DAYS}d; stale ids: {stale_sources[:10]}"
            ),
        ),
    ]
    return DomainScore(domain="D9", score_0_100=weighted_score(checks), checks=checks, n=n)
