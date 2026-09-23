"""F21/F15/F40 (SOL-AUDIT-2026-09-24): SQL-text regression tests for the daily/weekly/monthly/
bd_territory report item collectors and the monthly/bd event-window queries -- same policy as
`tests/unit/test_dedup_xlang.py`: no live DB, assert on the exact SQL text sent so a future
regression (dropping `story_id`/`lang`, reintroducing a mutable `fetched_at` date fallback, or an
implicit-session-timezone `::date` cast) fails loudly here.

- F21: daily/weekly/monthly/bd_territory item collectors must select `story_id`/`lang` so
  `eoa.report.clustering.cluster_items`'s fallback grouping can use the persisted story key.
- F15: monthly's top-events-by-amount and bd_territory's procurement-events window must never fall
  back to `i.fetched_at` (mutable -- bumped on a plain re-fetch, F09).
- F40: the same date windows anchor their `::date` cast to Asia/Jerusalem explicitly rather than
  relying on the DB session's own time zone setting.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_report_collectors_sql.py -q``
"""

from __future__ import annotations

import datetime as dt

import pytest

from eoa.report import bd_territory, daily, monthly, weekly


class _FakeCursor:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows or []
        self.executed: list[tuple[str, dict | None]] = []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, query: str, params: dict | None = None) -> None:
        self.executed.append((query, params))

    def fetchall(self) -> list[dict]:
        return self._rows

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None


class _FakeConnection:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows
        self.cursors: list[_FakeCursor] = []

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def cursor(self, row_factory=None) -> _FakeCursor:
        cur = _FakeCursor(self._rows)
        self.cursors.append(cur)
        return cur

    def all_queries(self) -> str:
        return "\n".join(q for cur in self.cursors for q, _ in cur.executed)


def _patch(monkeypatch: pytest.MonkeyPatch, module, rows: list[dict] | None = None) -> _FakeConnection:
    conn = _FakeConnection(rows)
    monkeypatch.setattr(module, "connection", lambda: conn)
    return conn


# --------------------------------------------------------------------------
# F21: story_id / lang selected by every main item collector
# --------------------------------------------------------------------------


def test_daily_collect_items_selects_story_id_and_lang(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _patch(monkeypatch, daily, rows=[])
    daily.collect_items(dt.date(2026, 9, 1), dt.date(2026, 9, 1))
    sql = conn.all_queries()
    assert "i.story_id" in sql
    assert "i.lang" in sql


def test_weekly_collect_week_items_selects_story_id_and_lang(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _patch(monkeypatch, weekly, rows=[])
    weekly.collect_week_items(dt.date(2026, 9, 1), dt.date(2026, 9, 7))
    sql = conn.all_queries()
    assert "i.story_id" in sql
    assert "i.lang" in sql


def test_monthly_collect_month_items_selects_story_id_and_lang(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _patch(monkeypatch, monthly, rows=[])
    monthly.collect_month_items(dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    sql = conn.all_queries()
    assert "i.story_id" in sql
    assert "i.lang" in sql


def test_bd_territory_collect_market_items_selects_story_id_and_lang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _patch(monkeypatch, bd_territory, rows=[])
    bd_territory.collect_market_items("IL", dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    sql = conn.all_queries()
    assert "i.story_id" in sql
    assert "i.lang" in sql


# --------------------------------------------------------------------------
# F40: Jerusalem date boundaries made explicit (not the DB session's own time zone)
# --------------------------------------------------------------------------


def test_weekly_collect_week_items_anchors_date_cast_to_jerusalem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _patch(monkeypatch, weekly, rows=[])
    weekly.collect_week_items(dt.date(2026, 9, 1), dt.date(2026, 9, 7))
    assert "AT TIME ZONE 'Asia/Jerusalem'" in conn.all_queries()


def test_monthly_collect_month_items_anchors_date_cast_to_jerusalem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _patch(monkeypatch, monthly, rows=[])
    monthly.collect_month_items(dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    assert "AT TIME ZONE 'Asia/Jerusalem'" in conn.all_queries()


def test_bd_territory_collect_market_items_anchors_date_cast_to_jerusalem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _patch(monkeypatch, bd_territory, rows=[])
    bd_territory.collect_market_items("IL", dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    assert "AT TIME ZONE 'Asia/Jerusalem'" in conn.all_queries()


# --------------------------------------------------------------------------
# F15 + F40: monthly top-events-by-amount and bd_territory procurement events
# --------------------------------------------------------------------------


def test_monthly_top_events_by_amount_never_falls_back_to_fetched_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _patch(monkeypatch, monthly, rows=[])
    monthly.top_events_by_amount(dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    sql = conn.all_queries()
    assert "fetched_at" not in sql
    assert "i.created_at" in sql
    assert "AT TIME ZONE 'Asia/Jerusalem'" in sql


def test_bd_territory_platform_events_never_falls_back_to_fetched_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _patch(monkeypatch, bd_territory, rows=[])
    # `collect_platform_events` also tries to load `eoa.tenders.forecast.load_platform_payloads`
    # (best-effort, wrapped in its own try/except -- a failure there just logs a warning and
    # continues with `platforms = []`), so it needs no mocking for this SQL-text assertion.
    bd_territory.collect_platform_events("IL", dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    sql = conn.all_queries()
    assert "fetched_at" not in sql
    assert "i.created_at" in sql
    assert "AT TIME ZONE 'Asia/Jerusalem'" in sql
