"""F40 (SOL-AUDIT-2026-09-24 / SOL-REVIEW-2026-09-24): every weekly/monthly/bd `timestamptz::date`
report bucket must be anchored to `AT TIME ZONE 'Asia/Jerusalem'`, not the database session's own
time zone -- a plain `published_at::date` cast under a UTC session buckets a late-evening Jerusalem
item into the PREVIOUS UTC calendar day.

This test runs the real `eoa.report.weekly.collect_yellow_domain_summary` SQL against a live
PostgreSQL connection whose session time zone is explicitly set to UTC (the worst case the review
calls out), inside a transaction rolled back at teardown -- no rows persist. A month-boundary item
published at 2026-09-01 00:30 Israel Daylight Time (2026-08-31 21:30 UTC) must land in the
September 1-7 week bucket, not August -- the *previous* UTC calendar day.

Skips (does not fail) when Postgres is unreachable, same convention as
`tests/unit/test_patents_heatmap_postgres.py`.

Run with: ``DATABASE_URL=... PYTHONPATH=agent python -m pytest tests/unit/test_report_f40_jerusalem_boundary.py -q``
"""

from __future__ import annotations

import datetime as dt
import os
from contextlib import contextmanager

import pytest

# 2026-09-01 00:30 Israel Daylight Time (UTC+3, Israel is on DST through late October) ==
# 2026-08-31 21:30 UTC -- the exact boundary case the review names: a naive `::date` cast under a
# UTC-session DB buckets this into August 31, one day and one month too early.
_BOUNDARY_UTC = dt.datetime(2026, 8, 31, 21, 30, tzinfo=dt.UTC)
_TEST_DOMAIN = "f40-test-jerusalem-boundary"

# The OLD, unanchored query (SOL-AUDIT-2026-09-24 F40, pre-fix `collect_yellow_domain_summary`):
# casts straight to `::date` with no `AT TIME ZONE` -- correct only when the DB session's own time
# zone happens to be Asia/Jerusalem, silently wrong under a UTC (or any other) session.
_OLD_BUGGY_QUERY = """
    SELECT domain, count(*) AS n
    FROM items
    WHERE security_status = 'clean' AND dedup_of IS NULL AND level = 'yellow'
      AND domain = %(domain)s
      AND COALESCE(published_at, created_at)::date BETWEEN %(start)s AND %(end)s
    GROUP BY domain
"""


@pytest.fixture()
def pg_conn():
    pytest.importorskip("psycopg")
    import psycopg
    from psycopg.rows import dict_row

    url = os.environ.get(
        "DATABASE_URL", "postgresql://eoa@127.0.0.1:5432/eoanalyst"
    ).replace("postgresql+psycopg://", "postgresql://")
    try:
        conn = psycopg.connect(url, row_factory=dict_row)
    except Exception as exc:
        pytest.skip(f"Postgres unreachable: {exc}")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM items LIMIT 1")
    except Exception as exc:
        conn.rollback()
        conn.close()
        pytest.skip(f"items table unavailable: {exc}")
    # The worst case the review names: force a UTC session regardless of the server/cluster
    # default, so a passing test here cannot be an accident of local Postgres configuration.
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'UTC'")
    yield conn
    conn.rollback()  # never commits -- the inserted row never persists.
    conn.close()


def _insert_boundary_item(pg_conn) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO items (url, domain, level, security_status, dedup_of, published_at) "
            "VALUES (%(url)s, %(domain)s, 'yellow', 'clean', NULL, %(published_at)s)",
            {
                "url": "https://example.test/f40-boundary-item",
                "domain": _TEST_DOMAIN,
                "published_at": _BOUNDARY_UTC,
            },
        )


class TestMonthlyWatchlistChangesJerusalemBoundary:
    """Same boundary case for `eoa.report.monthly.watchlist_changes` (F40 evidence:
    `monthly.py:345`, `entities.created_at::date` was unanchored)."""

    def test_entity_created_00_30_jerusalem_lands_in_september(self, pg_conn) -> None:
        from eoa.report import monthly

        @contextmanager
        def _fake_connection(timeout: float | None = None):
            yield pg_conn

        with pg_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO items (url, entities_mentioned) VALUES (%s, %s) RETURNING id",
                ("https://example.test/f40-monthly-boundary-item", ["F40 Test Entity"]),
            )
            cur.execute(
                "INSERT INTO entities (name, kind, created_at) VALUES (%s, 'company', %s)",
                ("F40 Test Entity", _BOUNDARY_UTC),
            )

        orig_connection = monthly.connection
        monthly.connection = _fake_connection
        try:
            rows = monthly.watchlist_changes(dt.date(2026, 9, 1), dt.date(2026, 9, 30))
        finally:
            monthly.connection = orig_connection

        assert any(r["name"] == "F40 Test Entity" for r in rows), (
            f"expected the 00:30-Jerusalem boundary entity in the September window, got {rows!r}"
        )


class TestWeeklyYellowDomainSummaryJerusalemBoundary:
    def test_00_30_jerusalem_item_lands_in_september_week_under_utc_session(self, pg_conn) -> None:
        from eoa.report import weekly

        @contextmanager
        def _fake_connection(timeout: float | None = None):
            yield pg_conn

        _insert_boundary_item(pg_conn)

        # Patch this test only, via a local import + monkeypatch-free swap: call the module
        # function with its own `connection` temporarily rebound to the shared, rolled-back
        # transaction (mirrors test_patents_heatmap_postgres.py's approach).
        orig_connection = weekly.connection
        weekly.connection = _fake_connection
        try:
            rows = weekly.collect_yellow_domain_summary(dt.date(2026, 9, 1), dt.date(2026, 9, 7))
        finally:
            weekly.connection = orig_connection

        matching = [r for r in rows if r["domain"] == _TEST_DOMAIN]
        assert matching == [{"domain": _TEST_DOMAIN, "n": 1}], (
            f"expected the 00:30-Jerusalem boundary item in the Sep 1-7 week bucket, got {rows!r}"
        )

    def test_same_item_is_absent_from_the_august_week_it_would_wrongly_land_in_under_utc(
        self, pg_conn
    ) -> None:
        from eoa.report import weekly

        @contextmanager
        def _fake_connection(timeout: float | None = None):
            yield pg_conn

        _insert_boundary_item(pg_conn)
        orig_connection = weekly.connection
        weekly.connection = _fake_connection
        try:
            rows = weekly.collect_yellow_domain_summary(dt.date(2026, 8, 25), dt.date(2026, 8, 31))
        finally:
            weekly.connection = orig_connection
        assert not [r for r in rows if r["domain"] == _TEST_DOMAIN]

    def test_old_unanchored_query_misbuckets_the_same_fixture_under_a_utc_session(self, pg_conn) -> None:
        """Discriminator: the pre-fix query -- still present here for comparison only -- finds
        NOTHING in the correct (September) week under a forced UTC session, proving the fixed
        query above is not accidentally equivalent to it."""
        _insert_boundary_item(pg_conn)
        with pg_conn.cursor() as cur:
            cur.execute(
                _OLD_BUGGY_QUERY,
                {"domain": _TEST_DOMAIN, "start": dt.date(2026, 9, 1), "end": dt.date(2026, 9, 7)},
            )
            rows = cur.fetchall()
        assert rows == [], (
            "the old unanchored query was expected to MISS the boundary item under a UTC "
            f"session (proving the fix matters), but found {rows!r}"
        )


class TestGeographyCollectByCountryJerusalemBoundary:
    """SOL-REVIEW2-2026-09-24 F40 remaining gap: `eoa.report.geography.collect_by_country`
    (`geography.py:268`) cast `COALESCE(i.published_at, i.created_at)::date` with no `AT TIME
    ZONE` anchor -- same boundary bug as the weekly/monthly cases above, now fixed to anchor on
    Asia/Jerusalem before the date cast."""

    _MARKER_TITLE = "F40 geography boundary marker -- do not match on title elsewhere"
    # A fake, not-a-real-alias ISO-2 code so `normalize_country` buckets this row alone -- the
    # live DB has hundreds of real 'IL'/'US'/... items, and `collect_by_country` caps
    # `top_items` per country at 3 (sorted by score DESC), so a same-country insert with no score
    # could silently sort outside that cap and never appear at all (score is NULL here on
    # purpose, to test the date/timezone boundary in isolation from scoring).
    _MARKER_GEOGRAPHY = "zz"

    def _insert_geo_boundary_item(self, pg_conn) -> None:
        with pg_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO items (url, title, geography, published_at) "
                "VALUES (%(url)s, %(title)s, %(geography)s, %(published_at)s)",
                {
                    "url": "https://example.test/f40-geography-boundary-item",
                    "title": self._MARKER_TITLE,
                    "geography": self._MARKER_GEOGRAPHY,
                    "published_at": _BOUNDARY_UTC,
                },
            )

    def test_00_30_jerusalem_item_lands_in_september_window_under_utc_session(self, pg_conn) -> None:
        from eoa.report import geography

        @contextmanager
        def _fake_connection(timeout: float | None = None):
            yield pg_conn

        self._insert_geo_boundary_item(pg_conn)

        orig_connection = geography.connection
        geography.connection = _fake_connection
        try:
            data = geography.collect_by_country(dt.date(2026, 9, 1), dt.date(2026, 9, 7))
        finally:
            geography.connection = orig_connection

        matching_titles = {
            item["title"]
            for entry in data["countries"]
            for item in entry["top_items"]
            if item["title"] == self._MARKER_TITLE
        }
        assert matching_titles == {self._MARKER_TITLE}, (
            f"expected the 00:30-Jerusalem boundary item in the Sep 1-7 window, got {data!r}"
        )

    def test_same_item_is_absent_from_the_august_window_it_would_wrongly_land_in_under_utc(
        self, pg_conn
    ) -> None:
        from eoa.report import geography

        @contextmanager
        def _fake_connection(timeout: float | None = None):
            yield pg_conn

        self._insert_geo_boundary_item(pg_conn)
        orig_connection = geography.connection
        geography.connection = _fake_connection
        try:
            data = geography.collect_by_country(dt.date(2026, 8, 25), dt.date(2026, 8, 31))
        finally:
            geography.connection = orig_connection

        matching_titles = {
            item["title"]
            for entry in data["countries"]
            for item in entry["top_items"]
            if item["title"] == self._MARKER_TITLE
        }
        assert matching_titles == set(), (
            f"the boundary item must NOT appear in the August window, got {data!r}"
        )
