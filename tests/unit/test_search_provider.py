"""Tests for eoa.search.provider — the ddgs/searxng dispatch layer (migration step 1b).

The ddgs client itself is mocked throughout (no network calls); a live smoke test against
the real ddgs package and a real SearXNG container is documented separately, not run here.
"""

from __future__ import annotations

from typing import ClassVar

import ddgs as ddgs_pkg
import pytest
from ddgs.exceptions import DDGSException, RatelimitException

from eoa.config import settings
from eoa.search import provider


class FakeDDGS:
    """Stand-in for ddgs.DDGS: records calls, returns canned results per category."""

    text_results: ClassVar[list[dict]] = []
    news_results: ClassVar[list[dict]] = []
    text_raises: Exception | None = None
    news_raises: Exception | None = None
    text_calls: ClassVar[list[dict]] = []
    news_calls: ClassVar[list[dict]] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> FakeDDGS:
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def text(self, query, **kwargs):
        FakeDDGS.text_calls.append({"query": query, **kwargs})
        if FakeDDGS.text_raises:
            raise FakeDDGS.text_raises
        return FakeDDGS.text_results

    def news(self, query, **kwargs):
        FakeDDGS.news_calls.append({"query": query, **kwargs})
        if FakeDDGS.news_raises:
            raise FakeDDGS.news_raises
        return FakeDDGS.news_results


@pytest.fixture(autouse=True)
def _reset_fake_ddgs(monkeypatch):
    """Patch ddgs.DDGS everywhere it's looked up and reset canned state between tests."""
    FakeDDGS.text_results = []
    FakeDDGS.news_results = []
    FakeDDGS.text_raises = None
    FakeDDGS.news_raises = None
    FakeDDGS.text_calls = []
    FakeDDGS.news_calls = []
    monkeypatch.setattr(ddgs_pkg, "DDGS", FakeDDGS)
    # keep the rate limiter from accumulating stamps across tests / real config values
    monkeypatch.setattr(provider, "_ddgs_limiter", None)
    yield


@pytest.fixture()
def force_provider(monkeypatch):
    """Force settings().search.provider without touching the on-disk config.yaml."""

    def _set(name: str):
        s = settings()
        monkeypatch.setattr(s.search, "provider", name)
        return s

    return _set


class TestSplitBackends:
    """_split_backends maps a searxng-flavoured engine list to ddgs backends."""

    def test_plain_text_engines_pass_through(self):
        text, news = provider._split_backends(["google", "bing"])
        # "bing" is a disabled text engine in ddgs 9.x and is dropped; google is kept.
        assert text == ["google"]
        assert news == set()

    def test_news_suffix_extracted(self):
        text, news = provider._split_backends(["google", "bing news", "google news"])
        assert text == ["google"]
        # "bing news" maps directly; "google news" has no ddgs equivalent -> falls back to duckduckgo.
        assert news == {"bing", "duckduckgo"}

    def test_unsupported_engine_dropped(self):
        text, news = provider._split_backends(["baidu", "duckduckgo"])
        assert text == ["duckduckgo"]
        assert news == set()

    def test_empty_list(self):
        text, news = provider._split_backends([])
        assert text == []
        assert news == set()


class TestDdgsSearchDispatch:
    """search() with the default provider (ddgs) delegates to the ddgs package."""

    def test_default_provider_is_ddgs(self):
        assert settings().search.provider == "ddgs"

    def test_text_hits_mapped_to_search_hit(self):
        FakeDDGS.text_results = [
            {"title": "Rheinmetall Skyranger deal", "href": "https://example.com/1", "body": "snippet one"},
            {"title": "Second hit", "href": "https://example.com/2", "body": "snippet two"},
        ]
        resp = provider.search("Rheinmetall Skyranger", "en", max_results=8, engines=["google"])
        assert resp.error is None
        assert resp.query == "Rheinmetall Skyranger"
        assert resp.lang == "en"
        assert len(resp.hits) == 2
        assert resp.hits[0].url == "https://example.com/1"
        assert resp.hits[0].title == "Rheinmetall Skyranger deal"
        assert resp.hits[0].snippet == "snippet one"
        assert resp.hits[0].engine == "ddgs"

    def test_region_mapping_for_hebrew(self):
        FakeDDGS.text_results = [{"title": "t", "href": "https://x.com", "body": "b"}]
        provider.search("אלביט מערכות חוזה", "he", max_results=5, engines=["google"])
        assert FakeDDGS.text_calls[-1]["region"] == "il-he"

    def test_region_mapping_defaults_to_us_en_for_unknown_lang(self):
        FakeDDGS.text_results = []
        FakeDDGS.text_raises = DDGSException("No results found.")
        provider.search("q", "xx", max_results=5, engines=["google"])
        assert FakeDDGS.text_calls[-1]["region"] == "us-en"

    def test_max_results_forwarded(self):
        FakeDDGS.text_results = [{"title": "t", "href": "https://x.com", "body": "b"}]
        provider.search("q", "en", max_results=3, engines=["google"])
        assert FakeDDGS.text_calls[-1]["max_results"] == 3

    def test_dedupes_by_url(self):
        FakeDDGS.text_results = [
            {"title": "a", "href": "https://dup.com", "body": "1"},
            {"title": "b", "href": "https://dup.com", "body": "2"},
        ]
        resp = provider.search("q", "en", engines=["google"])
        assert len(resp.hits) == 1

    def test_news_engine_triggers_news_call(self):
        FakeDDGS.text_results = [{"title": "t", "href": "https://x.com", "body": "b"}]
        FakeDDGS.news_results = [
            {"title": "News hit", "url": "https://news.com/1", "body": "news body", "date": "2026-09-01"}
        ]
        resp = provider.search("q", "en", engines=["google", "bing news"])
        urls = {h.url for h in resp.hits}
        assert "https://x.com" in urls
        assert "https://news.com/1" in urls
        news_hit = next(h for h in resp.hits if h.url == "https://news.com/1")
        assert news_hit.engine == "ddgs-news"
        assert news_hit.published == "2026-09-01"
        assert FakeDDGS.news_calls, "news() should have been called"
        assert FakeDDGS.news_calls[-1]["backend"] == "bing"

    def test_no_news_call_when_no_news_engine_configured(self):
        FakeDDGS.text_results = [{"title": "t", "href": "https://x.com", "body": "b"}]
        provider.search("q", "en", engines=["google"])
        assert FakeDDGS.news_calls == []


class TestDdgsErrorHandling:
    """ddgs raises on rate limits and on zero results — never propagate into the ReAct loop."""

    def test_ddgs_exception_returns_empty_response_with_error(self):
        FakeDDGS.text_raises = DDGSException("No results found.")
        resp = provider.search("nonexistent query xyz", "en")
        assert resp.hits == []
        assert resp.error is not None
        assert "No results" in resp.error

    def test_ratelimit_exception_returns_error_not_raise(self):
        FakeDDGS.text_raises = RatelimitException("rate limited")
        resp = provider.search("q", "en")
        assert resp.error is not None
        assert resp.hits == []

    def test_unexpected_exception_also_caught(self):
        FakeDDGS.text_raises = RuntimeError("boom")
        resp = provider.search("q", "en")
        assert resp.error is not None
        assert "boom" in resp.error


class TestProviderSwitch:
    """settings().search.provider selects the backend; searxng path delegates unchanged."""

    def test_provider_searxng_delegates_to_searxng_client(self, monkeypatch, force_provider):
        force_provider("searxng")
        from eoa.search import searxng_client

        called = {}

        def fake_search(query, lang="en", *, categories="general", max_results=10, time_range=None, engines=None):
            called.update(
                query=query, lang=lang, categories=categories, max_results=max_results, time_range=time_range, engines=engines
            )
            return searxng_client.SearchResponse(query, lang, [])

        monkeypatch.setattr(searxng_client, "search", fake_search)
        resp = provider.search("q", "en", max_results=7)
        assert called["query"] == "q"
        assert called["max_results"] == 7
        assert resp.query == "q"
        # ddgs backend must not have been touched
        assert FakeDDGS.text_calls == []

    def test_provider_ddgs_does_not_touch_searxng_client(self, monkeypatch):
        from eoa.search import searxng_client

        def boom(*a, **kw):
            raise AssertionError("searxng_client.search should not be called when provider=ddgs")

        monkeypatch.setattr(searxng_client, "search", boom)
        FakeDDGS.text_results = [{"title": "t", "href": "https://x.com", "body": "b"}]
        resp = provider.search("q", "en")
        assert resp.hits


class TestPing:
    def test_ping_ddgs_success(self):
        FakeDDGS.text_results = [{"title": "t", "href": "https://x.com", "body": "b"}]
        assert provider.ping() is True

    def test_ping_ddgs_failure(self):
        FakeDDGS.text_raises = DDGSException("no results")
        assert provider.ping() is False

    def test_ping_searxng_delegates(self, monkeypatch, force_provider):
        force_provider("searxng")
        from eoa.search import searxng_client

        monkeypatch.setattr(searxng_client, "ping", lambda: True)
        assert provider.ping() is True


class TestRateLimiter:
    def test_rate_limiter_does_not_sleep_under_budget(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(provider.time, "sleep", lambda s: sleeps.append(s))
        limiter = provider._RateLimiter(per_minute=5)
        for _ in range(3):
            limiter.wait()
        assert sleeps == []

    def test_rate_limiter_sleeps_over_budget(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(provider.time, "sleep", lambda s: sleeps.append(s))
        limiter = provider._RateLimiter(per_minute=2)
        for _ in range(3):
            limiter.wait()
        assert len(sleeps) == 1
