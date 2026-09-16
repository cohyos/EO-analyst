"""Round 4 search hardening (docs/MODULES.md "Round 4 search", 2026-09-06 evening incident):
per-query cache (`eoa.search.cache`), per-provider circuit breaker (`eoa.search.circuit`),
`eoa.search.provider.search()`'s cache/circuit/rotation wiring, and the per-run query budget
(`eoa.search.budget`).

No live network: `ddgs.DDGS` and `eoa.search.searxng_client.search` are both faked, matching the
pattern in `test_search_provider.py`.
"""

from __future__ import annotations

import time
from typing import ClassVar

import ddgs as ddgs_pkg
import pytest
from ddgs.exceptions import DDGSException

from eoa.search import budget, cache, circuit, provider


class FakeDDGS:
    """Stand-in for ddgs.DDGS (text-search only — round 4 tests don't exercise news)."""

    text_results: ClassVar[list[dict]] = []
    text_raises: Exception | None = None
    text_calls: ClassVar[list[dict]] = []

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
        return []


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Fresh fake ddgs, a throwaway cache dir per test, and cleared circuits — round 4's cache and
    circuit registries are process-wide by design, so tests must reset them explicitly."""
    FakeDDGS.text_results = []
    FakeDDGS.text_raises = None
    FakeDDGS.text_calls = []
    monkeypatch.setattr(ddgs_pkg, "DDGS", FakeDDGS)
    monkeypatch.setattr(provider, "_ddgs_limiter", None)
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "search_cache")
    monkeypatch.delenv("EOA_SEARCH_NO_CACHE", raising=False)
    # 2026-09-16 fix: preset the cached searxng reachability preflight to "reachable" so this
    # file's pre-existing fallback tests (which mock `searxng_client.search` directly) are
    # unaffected; the probe itself is covered by test_search_provider.py's
    # TestSearxngReachabilityPreflight.
    monkeypatch.setattr(provider, "_searxng_reachable", True)
    circuit.reset_all()
    yield
    circuit.reset_all()


def _ok_result(url="https://example.com/1", title="t", body="b"):
    return {"title": title, "href": url, "body": body}


class TestCacheHitMissTTL:
    def test_second_identical_call_is_a_cache_hit_no_network(self):
        FakeDDGS.text_results = [_ok_result()]
        r1 = provider.search("Rheinmetall Skyranger", "en", max_results=5, engines=["google"])
        assert r1.error is None
        assert len(FakeDDGS.text_calls) == 1

        r2 = provider.search("Rheinmetall Skyranger", "en", max_results=5, engines=["google"])
        assert r2.error is None
        assert r2.hits[0].url == "https://example.com/1"
        assert len(FakeDDGS.text_calls) == 1, "second call should have hit the cache, not ddgs"

    def test_cache_key_normalizes_whitespace_and_case(self):
        FakeDDGS.text_results = [_ok_result()]
        provider.search("Rheinmetall   Skyranger", "en", engines=["google"])
        provider.search("  rheinmetall skyranger  ", "EN", engines=["google"])
        assert len(FakeDDGS.text_calls) == 1

    def test_different_query_is_a_separate_cache_entry(self):
        FakeDDGS.text_results = [_ok_result()]
        provider.search("query one", "en", engines=["google"])
        provider.search("query two", "en", engines=["google"])
        assert len(FakeDDGS.text_calls) == 2

    def test_different_lang_is_a_separate_cache_entry(self):
        FakeDDGS.text_results = [_ok_result()]
        provider.search("same query", "en", engines=["google"])
        provider.search("same query", "he", engines=["google"])
        assert len(FakeDDGS.text_calls) == 2

    def test_expired_entry_is_a_miss(self, monkeypatch):
        s = provider.settings()
        monkeypatch.setattr(s.search, "cache_ttl_hours", 24.0)
        FakeDDGS.text_results = [_ok_result()]
        provider.search("stale query", "en", engines=["google"])
        assert len(FakeDDGS.text_calls) == 1

        # Simulate 25h passing by back-dating the cached file's created_at.
        key = cache.cache_key("ddgs", "stale query", "en", categories="general", engines=["google"])
        path = cache._path_for(key)
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        data["created_at"] = time.time() - 25 * 3600
        path.write_text(json.dumps(data), encoding="utf-8")

        provider.search("stale query", "en", engines=["google"])
        assert len(FakeDDGS.text_calls) == 2, "an expired entry must not serve a hit"

    def test_zero_ttl_disables_caching(self, monkeypatch):
        s = provider.settings()
        monkeypatch.setattr(s.search, "cache_ttl_hours", 0.0)
        FakeDDGS.text_results = [_ok_result()]
        provider.search("no ttl", "en", engines=["google"])
        provider.search("no ttl", "en", engines=["google"])
        assert len(FakeDDGS.text_calls) == 2

    def test_no_cache_env_var_bypasses_cache_entirely(self, monkeypatch):
        monkeypatch.setenv("EOA_SEARCH_NO_CACHE", "1")
        FakeDDGS.text_results = [_ok_result()]
        provider.search("bypass me", "en", engines=["google"])
        provider.search("bypass me", "en", engines=["google"])
        assert len(FakeDDGS.text_calls) == 2

    def test_failed_response_is_never_cached(self):
        # A genuine provider failure (rate limit), not DDGS's "No results found." zero-hit
        # signal -- since 4a61253 `_ddgs_search` deliberately treats the latter as a successful
        # empty response (see the "must not trip the circuit breaker" comment there), so it is
        # not a failure and would not exercise this test's contract.
        FakeDDGS.text_raises = DDGSException("Ratelimit: 202 Accepted")
        r1 = provider.search("will fail", "en", engines=["google"])
        assert r1.error is not None
        FakeDDGS.text_raises = None
        FakeDDGS.text_results = [_ok_result()]
        r2 = provider.search("will fail", "en", engines=["google"])
        assert r2.error is None
        assert len(FakeDDGS.text_calls) == 2, "an errored response must not have been cached"

    def test_no_results_is_a_successful_empty_response(self):
        # DDGS raises its base exception for zero hits; the provider maps that to hits=[] with no
        # error (a legitimate answer for a niche query), so it neither counts as a failure nor
        # opens the circuit breaker.
        FakeDDGS.text_raises = DDGSException("No results found.")
        r = provider.search("niche query", "en", engines=["google"])
        assert r.error is None
        assert r.hits == []
        assert circuit.get_circuit("ddgs").state == "closed"


class TestCircuitBreaker:
    def test_closed_by_default(self):
        c = circuit.get_circuit("ddgs")
        assert c.state == "closed"
        assert c.allow() is True

    def test_opens_after_threshold_consecutive_failures(self, monkeypatch):
        s = provider.settings()
        monkeypatch.setattr(s.search, "circuit_fail_threshold", 3)
        c = circuit.ProviderCircuit("t1", fail_threshold=3, base_cooldown_s=60, max_cooldown_s=1800)
        c.record_failure("timeout")
        c.record_failure("timeout")
        assert c.state == "closed"
        c.record_failure("timeout")
        assert c.state == "open"
        assert c.cooldown_s == 60

    def test_open_circuit_skips_instantly(self):
        c = circuit.ProviderCircuit("t2", fail_threshold=1, base_cooldown_s=60, max_cooldown_s=1800)
        c.record_failure("captcha sorry page")
        assert c.state == "open"
        assert c.allow() is False

    def test_allow_true_again_once_cooldown_elapses(self):
        c = circuit.ProviderCircuit("t3", fail_threshold=1, base_cooldown_s=10, max_cooldown_s=1800)
        c.record_failure("429")
        start = time.monotonic()
        assert c.allow(now=start) is False
        assert c.allow(now=start + 5) is False
        assert c.allow(now=start + 10.1) is True

    def test_success_closes_and_resets_backoff(self):
        c = circuit.ProviderCircuit("t4", fail_threshold=1, base_cooldown_s=10, max_cooldown_s=1800)
        c.record_failure("timeout")
        assert c.state == "open"
        c.record_success()
        assert c.state == "closed"
        assert c.consecutive_failures == 0
        assert c.open_count == 0

    def test_cooldown_doubles_on_repeat_failure_capped_at_max(self):
        c = circuit.ProviderCircuit("t5", fail_threshold=1, base_cooldown_s=60, max_cooldown_s=150)
        c.record_failure("timeout")
        assert c.cooldown_s == 60
        c.record_failure("timeout again after retry")
        assert c.cooldown_s == 120
        c.record_failure("timeout a third time")
        assert c.cooldown_s == 150, "capped at max_cooldown_s, not 240"

    def test_get_circuit_is_a_process_wide_singleton_per_name(self):
        a = circuit.get_circuit("ddgs")
        b = circuit.get_circuit("ddgs")
        assert a is b
        assert circuit.get_circuit("searxng") is not a

    def test_state_change_logged_once_not_per_skipped_query(self, monkeypatch):
        events = []
        c = circuit.ProviderCircuit("t6", fail_threshold=1, base_cooldown_s=60, max_cooldown_s=1800)
        monkeypatch.setattr(circuit.log, "warning", lambda event, **kw: events.append(event))
        c.record_failure("timeout")
        for _ in range(5):
            c.allow()  # repeatedly probing a still-open circuit must not log anything
        assert events == ["search_circuit_opened"]


class TestRotation:
    def test_ddgs_failure_falls_back_to_searxng_same_call(self, monkeypatch):
        from eoa.search import searxng_client

        FakeDDGS.text_raises = DDGSException("timed out")
        monkeypatch.setattr(
            searxng_client,
            "search",
            lambda q, lang="en", **kw: searxng_client.SearchResponse(
                q,
                lang,
                [searxng_client.SearchHit(url="https://sx.com/1", title="x", snippet="y", engine="google")],
            ),
        )
        resp = provider.search("counter-UAS optical tracking", "en", engines=["google"])
        assert resp.error is None
        assert resp.hits[0].url == "https://sx.com/1"
        # ddgs took exactly one consecutive failure (below default threshold of 3), not yet open.
        assert circuit.get_circuit("ddgs").consecutive_failures == 1
        assert circuit.get_circuit("ddgs").state == "closed"

    def test_open_ddgs_circuit_skips_straight_to_searxng(self, monkeypatch):
        from eoa.search import searxng_client

        ddgs_circuit = circuit.get_circuit("ddgs")
        ddgs_circuit.record_failure("t")
        ddgs_circuit.record_failure("t")
        ddgs_circuit.record_failure("t")
        assert ddgs_circuit.state == "open"

        monkeypatch.setattr(
            searxng_client,
            "search",
            lambda q, lang="en", **kw: searxng_client.SearchResponse(
                q,
                lang,
                [searxng_client.SearchHit(url="https://sx.com/2", title="x", snippet="y", engine="google")],
            ),
        )
        resp = provider.search("open circuit query", "en", engines=["google"])
        assert resp.hits[0].url == "https://sx.com/2"
        assert FakeDDGS.text_calls == [], "ddgs must not have been called while its circuit is open"

    def test_both_providers_down_degrades_to_honest_unavailable(self, monkeypatch):
        from eoa.search import searxng_client

        FakeDDGS.text_raises = DDGSException("timed out")
        monkeypatch.setattr(
            searxng_client,
            "search",
            lambda q, lang="en", **kw: searxng_client.SearchResponse(q, lang, error="connection refused"),
        )
        resp = provider.search("everything is down", "en", engines=["google"])
        assert resp.hits == []
        assert resp.error is not None
        assert "search unavailable" in resp.error

    def test_both_circuits_open_makes_zero_network_attempts(self, monkeypatch):
        from eoa.search import searxng_client

        circuit.get_circuit("ddgs").record_failure("t")
        circuit.get_circuit("ddgs").record_failure("t")
        circuit.get_circuit("ddgs").record_failure("t")
        circuit.get_circuit("searxng").record_failure("t")
        circuit.get_circuit("searxng").record_failure("t")
        circuit.get_circuit("searxng").record_failure("t")

        called = {"searxng": False}

        def boom(*a, **kw):
            called["searxng"] = True
            raise AssertionError("must not be called while circuit-open")

        monkeypatch.setattr(searxng_client, "search", boom)
        resp = provider.search("nothing should fire", "en", engines=["google"])
        assert FakeDDGS.text_calls == []
        assert called["searxng"] is False
        assert resp.error is not None
        assert "circuit-open" in resp.error

    def test_explicit_searxng_provider_never_rotates_to_ddgs(self, monkeypatch):
        from eoa.search import searxng_client

        s = provider.settings()
        monkeypatch.setattr(s.search, "provider", "searxng")
        monkeypatch.setattr(
            searxng_client,
            "search",
            lambda q, lang="en", **kw: searxng_client.SearchResponse(q, lang, error="down"),
        )
        resp = provider.search("stay on searxng", "en", engines=["google"])
        assert resp.error == "down"
        assert FakeDDGS.text_calls == [], (
            "provider=searxng must stay exclusive, matching pre-round-4 behavior"
        )


class TestPerRunQueryBudget:
    def test_consume_up_to_limit_then_exhausted(self, monkeypatch):
        s = provider.settings()
        monkeypatch.setattr(s.search, "max_queries_per_stage", 3)
        budget.reset("test_stage_a")
        assert budget.try_consume("test_stage_a") is True
        assert budget.try_consume("test_stage_a") is True
        assert budget.try_consume("test_stage_a") is True
        assert budget.try_consume("test_stage_a") is False
        assert budget.exhausted("test_stage_a") is True
        assert budget.remaining("test_stage_a") == 0

    def test_reset_opens_a_fresh_window(self, monkeypatch):
        s = provider.settings()
        monkeypatch.setattr(s.search, "max_queries_per_stage", 1)
        budget.reset("test_stage_b")
        assert budget.try_consume("test_stage_b") is True
        assert budget.try_consume("test_stage_b") is False
        budget.reset("test_stage_b")
        assert budget.try_consume("test_stage_b") is True

    def test_non_positive_limit_means_unlimited(self, monkeypatch):
        s = provider.settings()
        monkeypatch.setattr(s.search, "max_queries_per_stage", 0)
        budget.reset("test_stage_c")
        for _ in range(50):
            assert budget.try_consume("test_stage_c") is True
        assert budget.exhausted("test_stage_c") is False

    def test_stages_are_independent(self, monkeypatch):
        s = provider.settings()
        monkeypatch.setattr(s.search, "max_queries_per_stage", 1)
        budget.reset("test_stage_tenders")
        budget.reset("test_stage_patents")
        assert budget.try_consume("test_stage_tenders") is True
        assert budget.try_consume("test_stage_tenders") is False
        assert budget.try_consume("test_stage_patents") is True, "a different stage's budget is untouched"
