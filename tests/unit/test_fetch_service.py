"""Unit tests for eoa.fetch.service's IngestStats counting (F19, docs/REVIEW_2026-09-05.md).

No DB, no network: `eoa.memory.relational.insert_item` and `eoa.fetch.service._url_already_seen`
are monkeypatched; `_store_item` otherwise runs its real (pure-python) sanitize pipeline over a
small literal HTML snippet.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_fetch_service.py -q``
"""

from __future__ import annotations

import pytest

from eoa.fetch import service

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
        monkeypatch.setattr(service, "_url_already_seen", lambda url: False)
        monkeypatch.setattr("eoa.memory.relational.insert_item", lambda **kw: 101)

        stats = service.IngestStats()
        service._store_item(
            source_db_id=1, url="https://example.com/new", html_text=_SAMPLE_HTML, stats=stats
        )

        assert stats.items_inserted == 1
        assert stats.items_skipped == 0

    def test_conflict_refresh_of_seen_url_counts_as_skipped_not_inserted(self, monkeypatch):
        """The regression this fixes: `relational.insert_item`'s `ON CONFLICT (url) DO UPDATE`
        always `RETURNING id` -- even for a URL already in the DB, just refreshed `fetched_at` --
        so the id it returns is truthy either way. Before F19, `_store_item` counted every such
        refresh as a fresh `items_inserted`, which is how one run reported 115 while only 61 rows
        were actually new."""
        monkeypatch.setattr(service, "_url_already_seen", lambda url: True)
        monkeypatch.setattr("eoa.memory.relational.insert_item", lambda **kw: 202)  # pre-existing id

        stats = service.IngestStats()
        service._store_item(
            source_db_id=1, url="https://example.com/seen", html_text=_SAMPLE_HTML, stats=stats
        )

        assert stats.items_inserted == 0
        assert stats.items_skipped == 1

    def test_insert_failure_counts_as_skipped_regardless_of_seen(self, monkeypatch):
        monkeypatch.setattr(service, "_url_already_seen", lambda url: False)

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
        monkeypatch.setattr(service, "_url_already_seen", lambda url: "seen" in url)
        monkeypatch.setattr("eoa.memory.relational.insert_item", lambda **kw: 1)

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

        async def _fake_rss(source, *, source_db_id, since_days, throttle, stats):
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

        async def _fake_rss(source, *, source_db_id, since_days, throttle, stats):
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

        async def _fake_rss(source, *, source_db_id, since_days, throttle, stats):
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

        async def _fake_sitemap(source, *, source_db_id, since_days, throttle, stats):
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

        async def _fake_guarded_fetch_page(url):
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

        async def _fake_fetch_and_store(url, *, throttle, source_db_id, stats, fallback_title=None, fallback_published_at=None):
            fetched_urls.append(url)

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

        def _fake_store_search_hit(*, source_db_id, hit, stats):
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
        monkeypatch.setattr(service, "_url_already_seen", lambda url: False)
        insert_calls = []
        status_calls = []
        monkeypatch.setattr(
            "eoa.memory.relational.insert_item",
            lambda **kw: (insert_calls.append(kw), 55)[1],
        )
        monkeypatch.setattr(
            "eoa.memory.relational.update_item_fields",
            lambda item_id, **kw: status_calls.append((item_id, kw)),
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
        assert status_calls == [(55, {"content_status": "stub"})]
        assert stats.items_inserted == 1
        assert stats.items_skipped == 0

    def test_already_seen_hit_counts_as_skipped_not_inserted(self, monkeypatch) -> None:
        monkeypatch.setattr(service, "_url_already_seen", lambda url: True)
        monkeypatch.setattr("eoa.memory.relational.insert_item", lambda **kw: 55)
        status_calls = []
        monkeypatch.setattr(
            "eoa.memory.relational.update_item_fields",
            lambda item_id, **kw: status_calls.append((item_id, kw)),
        )

        stats = service.IngestStats()
        service._store_search_hit(
            source_db_id=7, hit=_FakeSearchHit("https://linkedin.com/posts/seen"), stats=stats
        )

        assert stats.items_inserted == 0
        assert stats.items_skipped == 1
        assert status_calls == []  # never re-flagged content_status on a mere refresh


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
