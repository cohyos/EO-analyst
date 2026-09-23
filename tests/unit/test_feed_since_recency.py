"""F16 (docs/qa/content_review/SOL-AUDIT-2026-09-24.md): the Morning "items ingested" KPI cards'
`/feed?since=24h` deep link (and the matching `level=red`/`level=orange` variants) must key off an
immutable ingestion timestamp (`created_at`), not `fetched_at` -- `fetched_at` is bumped on every
re-fetch of an already-known URL (`relational.py` `insert_item`'s `ON CONFLICT ... DO UPDATE SET
fetched_at = ...`), which used to let an old, undated item that got re-crawled today resurface as
if it were newly ingested, in both the KPI count and the feed page it deep-links to.

`services._fetchone`/`_fetchall` are monkeypatched at the module level -- no live DB (same policy
as `tests/unit/test_api_smoke.py`).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_feed_since_recency.py -q``
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from eoa.api import services


class TestListItemsSinceFilterUsesCreatedAt:
    def test_since_filter_keys_off_created_at_not_fetched_at(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen_queries: list[tuple[str, Any]] = []

        def fetchone(query: str, params: Any = None) -> Any:
            seen_queries.append((query, params))
            return {"n": 0}

        def fetchall(query: str, params: Any = None) -> Any:
            seen_queries.append((query, params))
            return []

        monkeypatch.setattr(services, "_fetchone", fetchone)
        monkeypatch.setattr(services, "_fetchall", fetchall)
        monkeypatch.setattr(services, "_attach_corroboration", lambda cards: None)
        monkeypatch.setattr(services, "_attach_story_info", lambda cards: None)

        cutoff = (dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=24)).isoformat()
        services.list_items(since=cutoff, page=1, page_size=10)

        count_query, count_params = seen_queries[0]
        assert "i.created_at >= %(since)s" in count_query
        assert "i.fetched_at" not in count_query
        assert count_params["since"] == cutoff

    def test_old_undated_item_refetched_today_is_excluded_by_created_at(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An item with an old `created_at` but a `fetched_at` bumped by today's re-crawl (the F16
        repro) must NOT satisfy the `since` filter -- only `created_at` decides."""
        window_start = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=24)

        def fetchone(query: str, params: Any = None) -> Any:
            assert "i.created_at >= %(since)s" in query
            old_created_at = window_start - dt.timedelta(days=10)
            matches = old_created_at >= params["since"] if isinstance(params["since"], dt.datetime) else False
            return {"n": 1 if matches else 0}

        monkeypatch.setattr(services, "_fetchone", fetchone)
        monkeypatch.setattr(services, "_fetchall", lambda *_a, **_kw: [])
        monkeypatch.setattr(services, "_attach_corroboration", lambda cards: None)
        monkeypatch.setattr(services, "_attach_story_info", lambda cards: None)

        total, _items = services.list_items(since=window_start, page=1, page_size=10)
        assert total == 0


class TestNightSummaryItemCountUsesCreatedAt:
    def test_items_ingested_query_keys_off_created_at_not_fetched_at(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen_items_queries: list[str] = []

        def fetchone(query: str, params: Any = None) -> Any:
            if "FROM jobs WHERE kind = 'daily_run'" in query:
                return None
            if "FROM items" in query:
                seen_items_queries.append(query)
                return {"n": 7}
            if "FROM jobs WHERE kind = 'deep_search'" in query:
                return {"n": 0}
            if "FROM run_log WHERE event" in query:
                return {"n": 0}
            if "FROM tenders WHERE status" in query:
                return {"n": 0}
            if "FROM tender_forecasts" in query:
                return {"n": 0}
            raise AssertionError(f"unexpected query: {query}")

        monkeypatch.setattr(services, "_fetchone", fetchone)

        summary = services._night_summary()
        assert summary is not None
        assert summary["items_ingested"] == 7
        assert seen_items_queries, "expected at least one items-count query"
        for q in seen_items_queries:
            assert "WHERE created_at BETWEEN" in q
            assert "fetched_at" not in q
