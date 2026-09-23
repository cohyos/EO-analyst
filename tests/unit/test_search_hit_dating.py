"""Dating `kind: search` hits (2026-09-23).

LinkedIn search results are not time-ordered: 196 of 303 stored LinkedIn "posts" were from
2017-2025 and, stored without `published_at`, entered the day's reports as news (a 2022 Iron Beam
post in tech report 261). Hits are now dated from the LinkedIn post id (``id >> 22`` = epoch ms) or
the provider's own `published` field, and hits older than ``SEARCH_HIT_MAX_AGE_DAYS`` are skipped.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from eoa.fetch import service
from eoa.fetch.service import SEARCH_HIT_MAX_AGE_DAYS, search_hit_published_at


def _linkedin_id(when: datetime) -> int:
    return int(when.timestamp() * 1000) << 22


class TestSearchHitPublishedAt:
    def test_linkedin_activity_id_decodes_to_post_time(self) -> None:
        got = search_hit_published_at(
            "https://www.linkedin.com/posts/rafael_x-activity-6920393245042614272-WDxU"
        )
        assert got is not None and got.date().isoformat() == "2022-04-14"

    def test_ugcpost_and_share_ids_decode_too(self) -> None:
        when = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
        for kind in ("ugcPost", "share"):
            got = search_hit_published_at(f"https://www.linkedin.com/feed/update/urn:li:{kind}:{_linkedin_id(when)}")
            assert got is not None and abs((got - when).total_seconds()) < 1

    def test_provider_published_field_is_used_when_no_post_id(self) -> None:
        got = search_hit_published_at("https://example.com/a", "2026-09-20T10:00:00Z")
        assert got == datetime(2026, 9, 20, 10, 0, tzinfo=UTC)

    def test_undatable_hit_returns_none(self) -> None:
        assert search_hit_published_at("https://example.com/a") is None
        assert search_hit_published_at("https://example.com/a", "not a date") is None


class TestStaleHitsAreSkipped:
    def test_only_recent_and_undated_hits_are_stored(self, monkeypatch) -> None:
        now = datetime.now(UTC)
        recent = f"https://www.linkedin.com/posts/x-activity-{_linkedin_id(now - timedelta(days=2))}-a"
        stale = f"https://www.linkedin.com/posts/x-activity-{_linkedin_id(now - timedelta(days=SEARCH_HIT_MAX_AGE_DAYS + 5))}-b"
        undated = "https://example.com/news/c"
        hits = [
            SimpleNamespace(url=u, title="t", snippet="s", engine="ddgs", published=None)
            for u in (recent, stale, undated)
        ]
        monkeypatch.setattr(
            "eoa.search.provider.search",
            lambda *a, **k: SimpleNamespace(error=None, hits=hits),
        )
        stored: list[tuple[str, datetime | None]] = []
        monkeypatch.setattr(
            service,
            "_store_search_hit",
            lambda *, source_db_id, hit, stats, published_at=None: stored.append((hit.url, published_at)),
        )
        source = SimpleNamespace(id="x_linkedin", queries=["q"], engine_lang="en", max_results=10)
        stats = service.IngestStats()
        asyncio.run(service._ingest_search_source(source, source_db_id=1, stats=stats))

        urls = [u for u, _ in stored]
        assert recent in urls and undated in urls and stale not in urls
        assert dict(stored)[recent] is not None
        assert stats.items_skipped == 1
