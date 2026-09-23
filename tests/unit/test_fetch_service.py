"""Unit tests for eoa.fetch.service's IngestStats counting (F19, docs/REVIEW_2026-09-05.md).

No DB, no network: `eoa.memory.relational.insert_item` and `eoa.fetch.service._url_already_seen`
are monkeypatched; `_store_item` otherwise runs its real (pure-python) sanitize pipeline over a
small literal HTML snippet.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_fetch_service.py -q``
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eoa.fetch import service
from eoa.memory.relational import ItemUpsertResult

_SAMPLE_HTML = (
    "<html><head><title>Elbit wins new EO/IR contract</title></head>"
    "<body><article><p>Elbit Systems announced a new contract for targeting pods today.</p>"
    "<p>The deal is worth a significant sum and covers several years of deliveries.</p>"
    "</article></body></html>"
)


class _FakeConnCtx:
    """Minimal ``eoa.db.connection()`` stand-in for `_url_already_seen`'s own probe query --
    only used by tests that exercise that function directly rather than via monkeypatch."""

    def __init__(self, row: dict | None, *, raises: bool = False):
        self._row = row
        self._raises = raises

    def __enter__(self):
        if self._raises:
            raise RuntimeError("db unreachable")
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, row_factory=None):
        return self

    def execute(self, sql, params=None):
        return None

    def fetchone(self):
        return self._row


# --------------------------------------------------------------------------
# _url_already_seen: best-effort existence probe
# --------------------------------------------------------------------------


class TestUrlAlreadySeen:
    def test_true_when_a_row_comes_back(self, monkeypatch):
        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConnCtx({"?column?": 1}))
        assert service._url_already_seen("https://example.com/a") is True

    def test_false_when_no_row_comes_back(self, monkeypatch):
        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConnCtx(None))
        assert service._url_already_seen("https://example.com/b") is False

    def test_false_on_db_error_best_effort(self, monkeypatch):
        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConnCtx(None, raises=True))
        assert service._url_already_seen("https://example.com/c") is False


# --------------------------------------------------------------------------
# _store_item: IngestStats accounting (F19)
# --------------------------------------------------------------------------


class TestStoreItemStats:
    def test_new_url_counts_as_inserted(self, monkeypatch):
        monkeypatch.setattr(
            "eoa.memory.relational.insert_item", lambda **kw: ItemUpsertResult(101, inserted=True)
        )

        stats = service.IngestStats()
        service._store_item(
            source_db_id=1, url="https://example.com/new", html_text=_SAMPLE_HTML, stats=stats
        )

        assert stats.items_inserted == 1
        assert stats.items_skipped == 0

    def test_conflict_refresh_of_seen_url_counts_as_skipped_not_inserted(self, monkeypatch):
        """F19/F09 (SOL-AUDIT-2026-09-24): `insert_item` itself now reports whether it created a
        new row (`ItemUpsertResult.inserted`, the Postgres `xmax = 0` upsert idiom) -- `_store_item`
        trusts that flag directly instead of the old separate pre-insert `_url_already_seen` probe
        this replaced (removes a DB round trip and its own race)."""
        monkeypatch.setattr(
            "eoa.memory.relational.insert_item", lambda **kw: ItemUpsertResult(202, inserted=False)
        )

        stats = service.IngestStats()
        service._store_item(
            source_db_id=1, url="https://example.com/seen", html_text=_SAMPLE_HTML, stats=stats
        )

        assert stats.items_inserted == 0
        assert stats.items_skipped == 1

    def test_insert_failure_counts_as_skipped(self, monkeypatch):
        def _boom(**kw):
            raise RuntimeError("db write failed")

        monkeypatch.setattr("eoa.memory.relational.insert_item", _boom)

        stats = service.IngestStats()
        service._store_item(
            source_db_id=1, url="https://example.com/fails", html_text=_SAMPLE_HTML, stats=stats
        )

        assert stats.items_inserted == 0
        assert stats.items_skipped == 1

    def test_multiple_calls_accumulate_on_shared_stats(self, monkeypatch):
        monkeypatch.setattr(
            "eoa.memory.relational.insert_item",
            lambda **kw: ItemUpsertResult(1, inserted="seen" not in kw["url"]),
        )

        stats = service.IngestStats()
        service._store_item(
            source_db_id=None, url="https://example.com/1", html_text=_SAMPLE_HTML, stats=stats
        )
        service._store_item(
            source_db_id=None, url="https://example.com/2", html_text=_SAMPLE_HTML, stats=stats
        )
        service._store_item(
            source_db_id=None, url="https://example.com/seen-3", html_text=_SAMPLE_HTML, stats=stats
        )

        assert stats.items_inserted == 2
        assert stats.items_skipped == 1

    def test_passes_quality_aware_content_and_security_status(self, monkeypatch):
        """F09: a normal (non-blocked) fetch is stored `content_status='full'`,
        `security_status='clean'` -- the quality signal `insert_item` needs to know a later good
        fetch of this same URL may safely replace a stored blocked/stub row."""
        captured = {}

        def _fake_insert(**kw):
            captured.update(kw)
            return ItemUpsertResult(9, inserted=True)

        monkeypatch.setattr("eoa.memory.relational.insert_item", _fake_insert)

        stats = service.IngestStats()
        service._store_item(
            source_db_id=1, url="https://example.com/good", html_text=_SAMPLE_HTML, stats=stats
        )

        assert captured["content_status"] == "full"
        assert captured["security_status"] == "clean"

    def test_blocked_page_stored_with_stub_and_blocked_status(self, monkeypatch):
        captured = {}

        def _fake_insert(**kw):
            captured.update(kw)
            return ItemUpsertResult(10, inserted=True)

        monkeypatch.setattr("eoa.memory.relational.insert_item", _fake_insert)

        stats = service.IngestStats()
        blocked_html = "<html><body>Attention Required! | Cloudflare</body></html>"
        service._store_item(
            source_db_id=1,
            url="https://example.com/blocked",
            html_text=blocked_html,
            stats=stats,
            http_status=403,
        )

        assert captured["security_status"] == "blocked"

    def test_canonical_url_omitted_when_same_as_url_and_no_redirect(self, monkeypatch):
        """F36: no point sending an identical `canonical_url` -- only a genuinely different
        (redirected, or tracking-param-stripped) final URL is worth the extra dedup lookup."""
        captured = {}

        def _fake_insert(**kw):
            captured.update(kw)
            return ItemUpsertResult(11, inserted=True)

        monkeypatch.setattr("eoa.memory.relational.insert_item", _fake_insert)

        stats = service.IngestStats()
        service._store_item(
            source_db_id=1,
            url="https://example.com/plain",
            html_text=_SAMPLE_HTML,
            stats=stats,
            final_url="https://example.com/plain",
        )

        assert captured["canonical_url"] is None

    def test_canonical_url_set_from_redirected_final_url(self, monkeypatch):
        captured = {}

        def _fake_insert(**kw):
            captured.update(kw)
            return ItemUpsertResult(12, inserted=True)

        monkeypatch.setattr("eoa.memory.relational.insert_item", _fake_insert)

        stats = service.IngestStats()
        service._store_item(
            source_db_id=1,
            url="https://example.com/short-link",
            html_text=_SAMPLE_HTML,
            stats=stats,
            final_url="https://example.com/real-article?utm_source=twitter",
        )

        assert captured["canonical_url"] == "https://example.com/real-article"


# --------------------------------------------------------------------------
# F36: URL identity normalization
# --------------------------------------------------------------------------


class TestNormalizeUrlIdentity:
    def test_strips_utm_and_click_tracking_params(self):
        a = service._normalize_url_identity(
            "https://example.com/a?utm_source=x&utm_medium=y&fbclid=abc"
        )
        b = service._normalize_url_identity("https://example.com/a")
        assert a == b

    def test_keeps_non_tracking_query_params(self):
        normalized = service._normalize_url_identity("https://example.com/a?id=123")
        assert "id=123" in normalized

    def test_strips_trailing_slash_and_fragment(self):
        a = service._normalize_url_identity("https://example.com/a/#section")
        b = service._normalize_url_identity("https://example.com/a")
        assert a == b

    def test_lowercases_scheme_and_host(self):
        a = service._normalize_url_identity("HTTPS://Example.COM/a")
        b = service._normalize_url_identity("https://example.com/a")
        assert a == b


# --------------------------------------------------------------------------
# D9 round-1 fix (docs/qa/loop/round_1_fixes.md, sources_recently_fetched): per-source
# last_fetched_at/fail_count bookkeeping, once per _ingest_one_source call.
# --------------------------------------------------------------------------


class _FakeSource:
    def __init__(self, kind="rss", source_id=42, name="Example Source", url="https://example.com/feed"):
        self.kind = kind
        self.id = source_id
        self.name = name
        self.url = url


class TestTouchSourceFetched:
    def test_success_calls_relational_touch_with_ok_true(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "eoa.memory.relational.touch_source_fetched", lambda sid, ok: calls.append((sid, ok))
        )
        service._touch_source_fetched(7, "Example Source", ok=True)
        assert calls == [(7, True)]

    def test_failure_calls_relational_touch_with_ok_false(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "eoa.memory.relational.touch_source_fetched", lambda sid, ok: calls.append((sid, ok))
        )
        service._touch_source_fetched(7, "Example Source", ok=False)
        assert calls == [(7, False)]

    def test_no_source_id_falls_back_to_name_keyed_bump_on_failure(self, monkeypatch):
        bumped = []
        monkeypatch.setattr(service, "_bump_fail_count", lambda name: bumped.append(name))
        service._touch_source_fetched(None, "Example Source", ok=False)
        assert bumped == ["Example Source"]

    def test_no_source_id_and_ok_is_a_noop(self, monkeypatch):
        bumped = []
        monkeypatch.setattr(service, "_bump_fail_count", lambda name: bumped.append(name))
        service._touch_source_fetched(None, "Example Source", ok=True)
        assert bumped == []

    def test_relational_error_is_swallowed(self, monkeypatch):
        def _boom(sid, ok):
            raise RuntimeError("db down")

        monkeypatch.setattr("eoa.memory.relational.touch_source_fetched", _boom)
        service._touch_source_fetched(7, "Example Source", ok=True)  # must not raise


class TestIngestOneSourceTouchesBookkeeping:
    @pytest.mark.asyncio
    async def test_success_touches_ok_true(self, monkeypatch):
        calls = []
        monkeypatch.setattr(service, "_touch_source_fetched", lambda sid, name, ok: calls.append((sid, ok)))

        async def _fake_rss(source, *, source_db_id, since_days, throttle, stats, client=None):
            return None

        monkeypatch.setattr(service, "_ingest_rss_source", _fake_rss)
        stats = service.IngestStats()
        await service._ingest_one_source(
            _FakeSource(), source_db_id=7, since_days=3, throttle=service._DomainThrottle(), stats=stats
        )
        assert calls == [(7, True)]
        assert stats.sources_failed == 0

    @pytest.mark.asyncio
    async def test_fetch_error_touches_ok_false(self, monkeypatch):
        from eoa.errors import FetchError

        calls = []
        monkeypatch.setattr(service, "_touch_source_fetched", lambda sid, name, ok: calls.append((sid, ok)))

        async def _fake_rss(source, *, source_db_id, since_days, throttle, stats, client=None):
            raise FetchError("boom")

        monkeypatch.setattr(service, "_ingest_rss_source", _fake_rss)
        stats = service.IngestStats()
        await service._ingest_one_source(
            _FakeSource(), source_db_id=7, since_days=3, throttle=service._DomainThrottle(), stats=stats
        )
        assert calls == [(7, False)]
        assert stats.sources_failed == 1

    @pytest.mark.asyncio
    async def test_unexpected_error_touches_ok_false(self, monkeypatch):
        calls = []
        monkeypatch.setattr(service, "_touch_source_fetched", lambda sid, name, ok: calls.append((sid, ok)))

        async def _fake_rss(source, *, source_db_id, since_days, throttle, stats, client=None):
            raise ValueError("unexpected")

        monkeypatch.setattr(service, "_ingest_rss_source", _fake_rss)
        stats = service.IngestStats()
        await service._ingest_one_source(
            _FakeSource(), source_db_id=7, since_days=3, throttle=service._DomainThrottle(), stats=stats
        )
        assert calls == [(7, False)]
        assert stats.sources_failed == 1


# --------------------------------------------------------------------------
# Task B item 1 (2026-09-16): kind: sitemap dispatch
# --------------------------------------------------------------------------


class _FakeSitemapPage:
    def __init__(self, html: str, status: int = 200) -> None:
        self.html = html
        self.status = status


class TestIngestSitemapDispatch:
    @pytest.mark.asyncio
    async def test_sitemap_kind_routes_to_ingest_sitemap_source(self, monkeypatch) -> None:
        calls = []

        async def _fake_sitemap(source, *, source_db_id, since_days, throttle, stats, client=None):
            calls.append((source.id, source_db_id, since_days))

        monkeypatch.setattr(service, "_ingest_sitemap_source", _fake_sitemap)
        monkeypatch.setattr(service, "_touch_source_fetched", lambda sid, name, ok: None)

        stats = service.IngestStats()
        await service._ingest_one_source(
            _FakeSource(kind="sitemap", source_id=1, name="Sitemap Co", url="https://example.com/sitemap.xml"),
            source_db_id=9,
            since_days=7,
            throttle=service._DomainThrottle(),
            stats=stats,
        )
        assert calls == [(1, 9, 7)]
        assert stats.sources_failed == 0

    @pytest.mark.asyncio
    async def test_ingest_sitemap_source_feeds_entries_into_fetch_and_store(self, monkeypatch) -> None:
        from eoa.fetch.sitemap import SitemapEntry

        async def _fake_guarded_fetch_page(url, *, client=None):
            return _FakeSitemapPage("<urlset></urlset>")

        monkeypatch.setattr(service, "_guarded_fetch_page", _fake_guarded_fetch_page)
        monkeypatch.setattr(
            "eoa.fetch.sitemap.parse_sitemap",
            lambda html, *, path_prefix, since_days: [
                SitemapEntry(url="https://example.com/news/a", title="A"),
                SitemapEntry(url="https://example.com/news/b", title="B"),
            ],
        )

        fetched_urls = []

        async def _fake_fetch_and_store(
            url, *, throttle, source_db_id, stats, fallback_title=None, fallback_published_at=None, client=None
        ):
            fetched_urls.append(url)
            return True

        monkeypatch.setattr(service, "_fetch_and_store", _fake_fetch_and_store)

        stats = service.IngestStats()
        source = _FakeSource(kind="sitemap", url="https://example.com/sitemap.xml")
        source.path_prefix = "/news/"
        await service._ingest_sitemap_source(
            source, source_db_id=1, since_days=3, throttle=service._DomainThrottle(), stats=stats
        )
        assert fetched_urls == ["https://example.com/news/a", "https://example.com/news/b"]
        assert stats.entries_seen == 2


# --------------------------------------------------------------------------
# Task B item 2 (2026-09-16): kind: search dispatch (LinkedIn/X company posts via the search
# provider) -- store directly from each hit's snippet, never a full-page fetch.
# --------------------------------------------------------------------------


class _FakeSearchHit:
    def __init__(self, url: str, title: str = "", snippet: str = "") -> None:
        self.url = url
        self.title = title
        self.snippet = snippet


class _FakeSearchResponse:
    def __init__(self, hits: list, error: str | None = None) -> None:
        self.hits = hits
        self.error = error


class TestIngestSearchDispatch:
    @pytest.mark.asyncio
    async def test_search_kind_routes_to_ingest_search_source(self, monkeypatch) -> None:
        calls = []

        async def _fake_search(source, *, source_db_id, stats):
            calls.append((source.id, source_db_id))

        monkeypatch.setattr(service, "_ingest_search_source", _fake_search)
        monkeypatch.setattr(service, "_touch_source_fetched", lambda sid, name, ok: None)

        stats = service.IngestStats()
        await service._ingest_one_source(
            _FakeSource(kind="search", source_id=2, name="LinkedIn Co", url="https://linkedin.com/x"),
            source_db_id=5,
            since_days=7,
            throttle=service._DomainThrottle(),
            stats=stats,
        )
        assert calls == [(2, 5)]

    @pytest.mark.asyncio
    async def test_ingest_search_source_stores_each_hit_via_snippet(self, monkeypatch) -> None:
        stored = []

        def _fake_store_search_hit(*, source_db_id, hit, stats, published_at=None):
            stored.append((source_db_id, hit.url))
            stats.items_inserted += 1

        def _fake_run_search(query, lang, *, max_results):
            return _FakeSearchResponse([_FakeSearchHit("https://linkedin.com/posts/a"), _FakeSearchHit("https://linkedin.com/posts/b")])

        monkeypatch.setattr(service, "_store_search_hit", _fake_store_search_hit)
        monkeypatch.setattr("eoa.search.provider.search", _fake_run_search)

        source = _FakeSource(kind="search")
        source.queries = ['site:linkedin.com/posts "Example Co"']
        source.engine_lang = "en"
        source.max_results = 10

        stats = service.IngestStats()
        await service._ingest_search_source(source, source_db_id=3, stats=stats)

        assert stored == [(3, "https://linkedin.com/posts/a"), (3, "https://linkedin.com/posts/b")]
        assert stats.entries_seen == 2

    @pytest.mark.asyncio
    async def test_ingest_search_source_skips_failed_query_without_raising(self, monkeypatch) -> None:
        stored = []
        monkeypatch.setattr(
            service, "_store_search_hit", lambda *, source_db_id, hit, stats: stored.append(hit.url)
        )
        monkeypatch.setattr(
            "eoa.search.provider.search",
            lambda query, lang, *, max_results: _FakeSearchResponse([], error="search unavailable"),
        )

        source = _FakeSource(kind="search")
        source.queries = ["a query"]
        source.engine_lang = "en"
        source.max_results = 10

        stats = service.IngestStats()
        await service._ingest_search_source(source, source_db_id=1, stats=stats)  # must not raise
        assert stored == []


class TestStoreSearchHit:
    def test_new_hit_inserted_with_snippet_content_status(self, monkeypatch) -> None:
        insert_calls = []
        monkeypatch.setattr(
            "eoa.memory.relational.insert_item",
            lambda **kw: (insert_calls.append(kw), ItemUpsertResult(55, inserted=True))[1],
        )

        stats = service.IngestStats()
        service._store_search_hit(
            source_db_id=7,
            hit=_FakeSearchHit("https://linkedin.com/posts/x", title="X", snippet="snippet text"),
            stats=stats,
        )

        assert insert_calls[0]["url"] == "https://linkedin.com/posts/x"
        assert insert_calls[0]["clean_text"] == "snippet text"
        # 'stub', not the task brief's literal 'snippet' -- items.content_status's DB CHECK
        # constraint only allows 'full'/'partial'/'stub'; see _store_search_hit's docstring.
        assert insert_calls[0]["content_status"] == "stub"
        assert insert_calls[0]["security_status"] == "clean"
        assert stats.items_inserted == 1
        assert stats.items_skipped == 0

    def test_already_seen_hit_counts_as_skipped_not_inserted(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "eoa.memory.relational.insert_item", lambda **kw: ItemUpsertResult(55, inserted=False)
        )

        stats = service.IngestStats()
        service._store_search_hit(
            source_db_id=7, hit=_FakeSearchHit("https://linkedin.com/posts/seen"), stats=stats
        )

        assert stats.items_inserted == 0
        assert stats.items_skipped == 1


# --------------------------------------------------------------------------
# F11: a source whose every article fetch fails must not be recorded as OK
# --------------------------------------------------------------------------


class TestSourceFailsWhenEveryArticleFetchFails:
    @pytest.mark.asyncio
    async def test_rss_source_raises_fetch_error_when_all_articles_fail(self, monkeypatch) -> None:
        from eoa.errors import FetchError
        from eoa.fetch.rss import FeedEntry

        async def _fake_guarded_fetch_page(url, *, client=None):
            return _FakeSitemapPage("<rss></rss>")

        monkeypatch.setattr(service, "_guarded_fetch_page", _fake_guarded_fetch_page)
        monkeypatch.setattr(
            "eoa.fetch.rss.parse_feed",
            lambda html, *, since_days: [
                FeedEntry(url="https://example.com/a"),
                FeedEntry(url="https://example.com/b"),
            ],
        )

        async def _fake_fetch_and_store(url, **kw):
            return False  # every article fetch fails

        monkeypatch.setattr(service, "_fetch_and_store", _fake_fetch_and_store)

        stats = service.IngestStats()
        source = _FakeSource(kind="rss")
        with pytest.raises(FetchError, match="all 2 article"):
            await service._ingest_rss_source(
                source, source_db_id=1, since_days=3, throttle=service._DomainThrottle(), stats=stats
            )

    @pytest.mark.asyncio
    async def test_rss_source_ok_when_at_least_one_article_succeeds(self, monkeypatch) -> None:
        from eoa.fetch.rss import FeedEntry

        async def _fake_guarded_fetch_page(url, *, client=None):
            return _FakeSitemapPage("<rss></rss>")

        monkeypatch.setattr(service, "_guarded_fetch_page", _fake_guarded_fetch_page)
        monkeypatch.setattr(
            "eoa.fetch.rss.parse_feed",
            lambda html, *, since_days: [
                FeedEntry(url="https://example.com/a"),
                FeedEntry(url="https://example.com/b"),
            ],
        )

        results = iter([False, True])

        async def _fake_fetch_and_store(url, **kw):
            return next(results)

        monkeypatch.setattr(service, "_fetch_and_store", _fake_fetch_and_store)

        stats = service.IngestStats()
        source = _FakeSource(kind="rss")
        await service._ingest_rss_source(
            source, source_db_id=1, since_days=3, throttle=service._DomainThrottle(), stats=stats
        )  # must not raise

    @pytest.mark.asyncio
    async def test_rss_source_ok_when_feed_has_no_entries(self, monkeypatch) -> None:
        """Zero entries is a legitimate outcome (nothing new), not a failure."""

        async def _fake_guarded_fetch_page(url, *, client=None):
            return _FakeSitemapPage("<rss></rss>")

        monkeypatch.setattr(service, "_guarded_fetch_page", _fake_guarded_fetch_page)
        monkeypatch.setattr("eoa.fetch.rss.parse_feed", lambda html, *, since_days: [])

        stats = service.IngestStats()
        source = _FakeSource(kind="rss")
        await service._ingest_rss_source(
            source, source_db_id=1, since_days=3, throttle=service._DomainThrottle(), stats=stats
        )  # must not raise

    @pytest.mark.asyncio
    async def test_html_source_raises_fetch_error_when_all_links_fail(self, monkeypatch) -> None:
        from eoa.errors import FetchError

        async def _fake_guarded_fetch_page(url, *, client=None):
            return _FakeSitemapPage(
                '<html><body><a class="item" href="/a">A</a><a class="item" href="/b">B</a></body></html>'
            )

        monkeypatch.setattr(service, "_guarded_fetch_page", _fake_guarded_fetch_page)

        async def _fake_fetch_and_store(url, **kw):
            return False

        monkeypatch.setattr(service, "_fetch_and_store", _fake_fetch_and_store)

        stats = service.IngestStats()
        source = _FakeSource(kind="html", url="https://example.com/news")
        source.list_selector = "a.item"
        source.link_selector = "self"
        with pytest.raises(FetchError, match="all 2 article"):
            await service._ingest_html_source(
                source, source_db_id=1, throttle=service._DomainThrottle(), stats=stats
            )

    @pytest.mark.asyncio
    async def test_sitemap_source_raises_fetch_error_when_all_articles_fail(self, monkeypatch) -> None:
        from eoa.errors import FetchError
        from eoa.fetch.sitemap import SitemapEntry

        async def _fake_guarded_fetch_page(url, *, client=None):
            return _FakeSitemapPage("<urlset></urlset>")

        monkeypatch.setattr(service, "_guarded_fetch_page", _fake_guarded_fetch_page)
        monkeypatch.setattr(
            "eoa.fetch.sitemap.parse_sitemap",
            lambda html, *, path_prefix, since_days: [SitemapEntry(url="https://example.com/news/a")],
        )

        async def _fake_fetch_and_store(url, **kw):
            return False

        monkeypatch.setattr(service, "_fetch_and_store", _fake_fetch_and_store)

        stats = service.IngestStats()
        source = _FakeSource(kind="sitemap", url="https://example.com/sitemap.xml")
        source.path_prefix = None
        with pytest.raises(FetchError, match="all 1 article"):
            await service._ingest_sitemap_source(
                source, source_db_id=1, since_days=3, throttle=service._DomainThrottle(), stats=stats
            )


# --------------------------------------------------------------------------
# F05: per-source lookback derived from last successful fetch
# --------------------------------------------------------------------------


class TestLookbackDays:
    def test_no_known_last_success_keeps_default(self):
        assert service._lookback_days(None, 3) == 3

    def test_recent_success_keeps_default(self):
        now = datetime(2026, 9, 24, tzinfo=UTC)
        last_success = now - timedelta(hours=6)
        assert service._lookback_days(last_success, 3, now=now) == 3

    def test_five_day_outage_widens_lookback_past_the_three_day_default(self):
        """The audit's own example: a 5-day outage must not permanently skip a 4-day-old entry."""
        now = datetime(2026, 9, 24, tzinfo=UTC)
        last_success = now - timedelta(days=5)
        lookback = service._lookback_days(last_success, 3, now=now)
        assert lookback >= 5
        assert lookback == 6  # elapsed (5) + margin (1)

    def test_very_long_outage_is_bounded(self):
        now = datetime(2026, 9, 24, tzinfo=UTC)
        last_success = now - timedelta(days=200)
        assert service._lookback_days(last_success, 3, now=now) == service._LOOKBACK_MAX_DAYS

    def test_naive_datetime_is_treated_as_utc(self):
        now = datetime(2026, 9, 24, tzinfo=UTC)
        naive_last_success = (now - timedelta(days=5)).replace(tzinfo=None)
        assert service._lookback_days(naive_last_success, 3, now=now) == 6


# --------------------------------------------------------------------------
# F35: schedule-aware source due-ness (weekly sources skip ordinary 2-hour polls)
# --------------------------------------------------------------------------


class TestSourceIsDue:
    def test_daily_schedule_always_due(self):
        now = datetime(2026, 9, 24, tzinfo=UTC)
        assert service._source_is_due("daily", now - timedelta(minutes=5), now=now) is True

    def test_weekly_never_fetched_is_due(self):
        assert service._source_is_due("weekly", None) is True

    def test_weekly_fetched_recently_is_not_due(self):
        """The audit's test: a weekly source is excluded from ordinary two-hour polls."""
        now = datetime(2026, 9, 24, tzinfo=UTC)
        last_success = now - timedelta(hours=2)
        assert service._source_is_due("weekly", last_success, now=now) is False

    def test_weekly_fetched_eight_days_ago_is_due(self):
        """...and runs when due."""
        now = datetime(2026, 9, 24, tzinfo=UTC)
        last_success = now - timedelta(days=8)
        assert service._source_is_due("weekly", last_success, now=now) is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
