"""Dating `kind: search` hits (2026-09-23; extended R4 SOL-REVIEW3-2026-09-24 carryover,
2026-09-28).

LinkedIn search results are not time-ordered: 196 of 303 stored LinkedIn "posts" were from
2017-2025 and, stored without `published_at`, entered the day's reports as news (a 2022 Iron Beam
post in tech report 261). Hits are now dated from the LinkedIn post id (``id >> 22`` = epoch ms),
the provider's own `published` field, or (R4) a date recovered from the hit's own URL path
(`eoa.fetch.url_dates.date_from_url`); hits older than ``SEARCH_HIT_MAX_AGE_DAYS`` are skipped.

R4: a handful of `*_technology_search`/`defense_update_search` sources added 2026-09-27 supply
neither a post id nor a provider date, and their `site:` queries carry no engine-side recency
guarantee by default -- an undated hit is now DROPPED unless the source was searched with a
`search_timelimit` (ddgs `timelimit`), in which case the engine itself vouches for recency and the
hit is kept with `published_at` left NULL. This is a behaviour change from the pre-R4 contract,
where every undated hit was kept regardless.
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

    def test_url_date_is_used_when_no_post_id_and_no_provider_published(self) -> None:
        """R4: the third fallback rung -- a date recovered straight from the URL path (the
        motivating case: unmannedsystemstechnology.com's `/YYYY/MM/.../` article paths, whose
        `site:` search hits carry neither a post id nor a provider `published` field)."""
        got = search_hit_published_at("https://www.unmannedsystemstechnology.com/2026/02/fortem-world-cup/")
        assert got == datetime(2026, 2, 1, tzinfo=UTC)

    def test_bad_provider_published_string_still_falls_back_to_url_date(self) -> None:
        """An unparseable `published` string must not short-circuit past the URL-date fallback --
        the pre-R4 code returned `None` immediately on a `ValueError` here."""
        got = search_hit_published_at("https://defense-update.com/20260220_cuas-report.html", "not a date")
        assert got == datetime(2026, 2, 20, tzinfo=UTC)


class TestStaleHitsAreSkipped:
    def test_recent_dated_hit_kept_stale_dated_hit_dropped(self, monkeypatch) -> None:
        now = datetime.now(UTC)
        # Real-looking article slugs (>=3 hyphen-separated words) so `is_probable_article_url`
        # isn't itself what's under test here -- see TestArticleUrlFilterInIngest below for that.
        recent = f"https://www.linkedin.com/posts/example-co-wins-contract-activity-{_linkedin_id(now - timedelta(days=2))}-a"
        stale = f"https://www.linkedin.com/posts/example-co-wins-contract-activity-{_linkedin_id(now - timedelta(days=SEARCH_HIT_MAX_AGE_DAYS + 5))}-b"
        hits = [
            SimpleNamespace(url=u, title="t", snippet="s", engine="ddgs", published=None)
            for u in (recent, stale)
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
        assert recent in urls and stale not in urls
        assert dict(stored)[recent] is not None
        assert stats.items_skipped == 1

    def test_undated_hit_is_dropped_by_default(self, monkeypatch) -> None:
        """R4 behaviour change: an undated hit used to be kept unconditionally; it is now dropped
        unless the source was searched with a `search_timelimit` (see TestSearchTimelimitAndArticleFilter
        in test_fetch_service.py for the "kept when timelimit is set" half)."""
        undated = "https://example.com/reports/quarterly-defense-market-outlook"
        hits = [SimpleNamespace(url=undated, title="t", snippet="s", engine="ddgs", published=None)]
        monkeypatch.setattr(
            "eoa.search.provider.search",
            lambda *a, **k: SimpleNamespace(error=None, hits=hits),
        )
        stored: list[str] = []
        monkeypatch.setattr(
            service,
            "_store_search_hit",
            lambda *, source_db_id, hit, stats, published_at=None: stored.append(hit.url),
        )
        source = SimpleNamespace(
            id="x_no_timelimit", queries=["q"], engine_lang="en", max_results=10, search_timelimit=None
        )
        stats = service.IngestStats()
        asyncio.run(service._ingest_search_source(source, source_db_id=1, stats=stats))

        assert stored == []
        assert stats.items_skipped == 1
