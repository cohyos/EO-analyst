"""Search provider abstraction (migration step 1b, docs/PLAN_WINDOWS_NATIVE.md).

SearXNG is a Linux/Docker-only metasearch container; the native-Windows build has no
container runtime for it. This module is the single entry point callers use
(``eoa.search.provider.search`` / ``.ping``) and dispatches on ``settings().search.provider``:

- ``"ddgs"`` (default): a pure-Python backend built on the `ddgs` PyPI package
  (formerly ``duckduckgo_search``). No external service required.
- ``"searxng"``: the legacy backend, unchanged, in ``eoa.search.searxng_client``
  (imported lazily so a native install with no SearXNG container never touches httpx
  for it and never needs the container reachable at import time).

The dataclasses (`SearchHit`, `SearchResponse`) and the `search()` signature are kept
identical to the pre-migration ``searxng_client`` module so callers only need an import
change (see docs/MODULES.md, search/ section, 2026-09-05 note).

Round 4 (docs/MODULES.md "Round 4 search", 2026-09-06 evening incident): ``search()`` now also
(1) checks a 24h-default per-query result cache (``eoa.search.cache``) before touching the
network, (2) tracks a per-provider circuit breaker (``eoa.search.circuit``) so a provider stuck
timing out or captcha-blocked is skipped instantly instead of paying its timeout every query, and
(3) when the configured provider is ``"ddgs"`` (the default), automatically rotates to
``"searxng"`` if ddgs fails or its circuit is open (and vice versa is intentionally *not* done: an
explicit ``search.provider: searxng`` is treated as the operator's exclusive choice, unchanged
from the pre-round-4 behaviour). When every reachable provider fails or is circuit-open, ``search``
degrades to an ``error``-carrying empty response immediately — no additional network attempts, no
long waits. The per-run query budget (``eoa.search.budget``) is deliberately *not* enforced inside
this function — it is caller-side policy; see that module's docstring.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import structlog

from eoa.config import settings
from eoa.search import cache as _cache
from eoa.search import circuit as _circuit

log = structlog.get_logger(__name__)

# ISO 639-1 -> ddgs "region" (country-language) code. Mirrors searxng_client.LANG_MAP's intent,
# just in the region-code shape ddgs/DuckDuckGo expects.
LANG_REGION = {
    "he": "il-he",
    "en": "us-en",
    "ru": "ru-ru",
    "zh": "cn-zh",
    "fr": "fr-fr",
    "de": "de-de",
}

# Text-search backends ddgs 9.x actually resolves (verified against the installed package's
# engine registry, `ddgs.engines.ENGINES["text"]`, 2026-09-05: bing and yandex exist as engine
# classes but ship with `disabled = True` and never register). Anything outside this set is
# dropped before being handed to ddgs so an unsupported/renamed engine degrades to "auto"
# instead of silently searching nothing.
DDGS_TEXT_BACKENDS = {
    "brave",
    "duckduckgo",
    "google",
    "grokipedia",
    "mojeek",
    "startpage",
    "wikipedia",
    "yahoo",
}

# News-search backends ddgs 9.x resolves (`ddgs.engines.ENGINES["news"]`). Note there is no
# "google" news engine at all in this package.
DDGS_NEWS_BACKENDS = {"bing", "duckduckgo", "yahoo"}


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str
    engine: str
    score: float = 0.0
    published: str | None = None


@dataclass
class SearchResponse:
    query: str
    lang: str
    hits: list[SearchHit] = field(default_factory=list)
    error: str | None = None


class _RateLimiter:
    """Same per-minute sliding-window limiter as ``searxng_client._RateLimiter``.

    Duplicated rather than imported: the ddgs backend must not depend on (or lazily import)
    the searxng module at all, and the two limiters guard independent quotas (ddgs has no
    per-minute setting of its own in config, so it reuses ``searxng.rate_limit_per_minute``
    as the one configured "external search calls per minute" budget — see the note in
    ``_ddgs_limiter_instance`` below).
    """

    def __init__(self, per_minute: int) -> None:
        self.per_minute = max(per_minute, 1)
        self._stamps: list[float] = []
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._stamps = [t for t in self._stamps if now - t < 60]
            if len(self._stamps) >= self.per_minute:
                sleep_for = 60 - (now - self._stamps[0]) + 0.1
                log.debug("ddgs_rate_limit_sleep", seconds=round(sleep_for, 1))
                time.sleep(max(sleep_for, 0))
            self._stamps.append(time.monotonic())


_ddgs_limiter: _RateLimiter | None = None


def _ddgs_limiter_instance() -> _RateLimiter:
    global _ddgs_limiter
    if _ddgs_limiter is None:
        # config/config.yaml's `search.ddgs` block has no rate-limit field of its own (see
        # docs/MODULES.md note): `searxng.rate_limit_per_minute` is kept as the one configured
        # "external metasearch calls per minute" budget and reused for whichever backend is active.
        _ddgs_limiter = _RateLimiter(settings().searxng.rate_limit_per_minute)
    return _ddgs_limiter


def _split_backends(engines: list[str]) -> tuple[list[str], set[str]]:
    """Split a configured engine list (searxng-flavoured, e.g. ``["google", "bing news"]``)
    into (ddgs text backends, ddgs news backends).

    Unrecognised text engines (e.g. a stale "baidu"/"yandex" left over from the SearXNG
    config) are dropped with a debug log; ddgs itself also falls back to "auto" if the
    resulting backend list resolves to nothing, so this never hard-fails.
    """
    text_backends: list[str] = []
    news_backends: set[str] = set()
    for raw in engines:
        name = raw.strip().lower()
        if "news" in name:
            base = name.replace("news", "").strip()
            news_backends.add(base if base in DDGS_NEWS_BACKENDS else "duckduckgo")
            continue
        if name in DDGS_TEXT_BACKENDS:
            text_backends.append(name)
        else:
            log.debug("ddgs_backend_unsupported", engine=name)
    return text_backends, news_backends


def _ddgs_search(
    query: str,
    lang: str,
    *,
    max_results: int,
    time_range: str | None,
    engines: list[str] | None,
) -> SearchResponse:
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    cfg = settings().search.ddgs
    region = LANG_REGION.get(lang, "us-en")
    if engines is not None:
        # explicit override from the caller (or a test): use exactly what was asked for, like
        # searxng_client.search's `engines` kwarg did.
        text_backends, news_backends = _split_backends(engines)
    else:
        configured = settings().searxng.engines_by_lang.get(lang) or settings().searxng.engines or []
        text_backends, news_backends = _split_backends(configured)
        # The legacy per-language SearXNG lists were tuned for SearXNG's engine set and often map
        # down to a single ddgs text backend (e.g. "he" -> just "google" once "bing" is dropped as
        # disabled). A lone scraping backend is exactly the one that gets rate-limited/blocked most
        # often; ddgs queries every backend in `backend=` concurrently and merges+dedupes the
        # results (see ddgs.ddgs.DDGS._search_sync), so folding in the configured ddgs default set
        # costs nothing on a good day and is the difference between 0 and 8 hits on a bad one.
        for b in cfg.backends:
            name = b.strip().lower()
            if name in DDGS_TEXT_BACKENDS and name not in text_backends:
                text_backends.append(name)
    if not text_backends and not news_backends:
        text_backends = [b.strip().lower() for b in cfg.backends if b.strip().lower() in DDGS_TEXT_BACKENDS]

    _ddgs_limiter_instance().wait()
    hits: list[SearchHit] = []
    seen: set[str] = set()
    saw_error: str | None = None
    try:
        with DDGS(timeout=cfg.timeout_s) as ddgs:
            if text_backends:
                try:
                    raw = ddgs.text(
                        query,
                        region=region,
                        safesearch="off",
                        timelimit=time_range,
                        max_results=max_results,
                        backend=",".join(text_backends),
                    )
                except DDGSException as exc:
                    saw_error = str(exc)[:200]
                    raw = []
                for r in raw:
                    url = r.get("href") or r.get("url") or ""
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    hits.append(
                        SearchHit(
                            url=url,
                            title=r.get("title") or "",
                            snippet=(r.get("body") or "")[:400],
                            engine="ddgs",
                        )
                    )
            if news_backends:
                try:
                    raw_news = ddgs.news(
                        query,
                        region=region,
                        safesearch="off",
                        timelimit=time_range,
                        max_results=max_results,
                        backend=",".join(sorted(news_backends)),
                    )
                except DDGSException as exc:
                    saw_error = saw_error or str(exc)[:200]
                    raw_news = []
                for r in raw_news:
                    url = r.get("url") or ""
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    hits.append(
                        SearchHit(
                            url=url,
                            title=r.get("title") or "",
                            snippet=(r.get("body") or "")[:400],
                            engine="ddgs-news",
                            published=r.get("date"),
                        )
                    )
    except Exception as exc:
        # ddgs raises on rate limits and even on a plain "no results" (DDGSException("No results
        # found.")) — match searxng_client's contract: never raise into the ReAct loop, return an
        # empty (or partial) SearchResponse with `error` set instead.
        log.warning("ddgs_failed", query=query[:80], lang=lang, error=str(exc)[:160])
        return SearchResponse(query, lang, hits, error=str(exc)[:200])

    if not hits and saw_error:
        log.warning("ddgs_partial_failure", query=query[:80], lang=lang, error=saw_error)
        return SearchResponse(query, lang, error=saw_error)
    log.info("ddgs_ok", query=query[:80], lang=lang, hits=len(hits))
    return SearchResponse(query, lang, hits[:max_results])


def _call_searxng(
    query: str,
    lang: str,
    *,
    categories: str,
    max_results: int,
    time_range: str | None,
    engines: list[str] | None,
) -> SearchResponse:
    from eoa.search.searxng_client import search as searxng_search

    return searxng_search(
        query,
        lang,
        categories=categories,
        max_results=max_results,
        time_range=time_range,
        engines=engines,
    )


def search(
    query: str,
    lang: str = "en",
    *,
    categories: str = "general",
    max_results: int = 10,
    time_range: str | None = None,
    engines: list[str] | None = None,
) -> SearchResponse:
    """Run one query against the configured backend (``settings().search.provider``).

    Signature matches the pre-migration ``eoa.search.searxng_client.search`` exactly so
    existing callers only change their import. Never raises; returns ``error`` on failure
    so the ReAct loop (``eoa.search.deep_search``) can continue.

    Round 4: a fresh cache hit short-circuits straight back here (see module docstring); a miss
    falls through to the provider(s), each guarded by its own circuit breaker.
    """
    provider = settings().search.provider
    key = _cache.cache_key(
        provider, query, lang, categories=categories, time_range=time_range, engines=engines
    )
    cached = _cache.get(key)
    if cached is not None:
        log.debug("search_cache_hit", query=query[:80], lang=lang, provider=provider)
        return SearchResponse(cached.query, cached.lang, cached.hits[:max_results], error=None)

    if provider == "searxng":
        # Explicit operator choice: no automatic rotation to ddgs (unchanged pre-round-4 contract).
        resp = _call_searxng(
            query,
            lang,
            categories=categories,
            max_results=max_results,
            time_range=time_range,
            engines=engines,
        )
        if resp.error is None:
            _cache.put(key, resp)
        return resp

    # provider == "ddgs" (default): try ddgs first, then fall back to searxng. Each provider is
    # skipped instantly (no network attempt) while its circuit is open.
    attempted: list[str] = []
    ddgs_circuit = _circuit.get_circuit("ddgs")
    if ddgs_circuit.allow():
        attempted.append("ddgs")
        resp = _ddgs_search(query, lang, max_results=max_results, time_range=time_range, engines=engines)
        if resp.error is None:
            ddgs_circuit.record_success()
            _cache.put(key, resp)
            return resp
        ddgs_circuit.record_failure(resp.error or "")
    else:
        log.debug("search_circuit_skip", provider="ddgs", query=query[:80])

    searxng_circuit = _circuit.get_circuit("searxng")
    if searxng_circuit.allow():
        attempted.append("searxng")
        resp = _call_searxng(
            query,
            lang,
            categories=categories,
            max_results=max_results,
            time_range=time_range,
            engines=engines,
        )
        if resp.error is None:
            searxng_circuit.record_success()
            _cache.put(key, resp)
            return resp
        searxng_circuit.record_failure(resp.error or "")
    else:
        log.debug("search_circuit_skip", provider="searxng", query=query[:80])

    reason = "all attempted providers failed" if attempted else "all providers circuit-open"
    log.warning("search_unavailable", query=query[:80], lang=lang, attempted=attempted, reason=reason)
    return SearchResponse(query, lang, error=f"search unavailable: {reason}")


def ping() -> bool:
    """True if the configured search backend is reachable/usable.

    ddgs has no persistent service to health-check; a lightweight query stands in for it.
    """
    provider = settings().search.provider
    if provider == "searxng":
        from eoa.search.searxng_client import ping as searxng_ping

        return searxng_ping()
    try:
        from ddgs import DDGS

        with DDGS(timeout=5) as ddgs:
            return bool(ddgs.text("test", max_results=1))
    except Exception as exc:
        log.debug("ddgs_ping_failed", error=str(exc)[:160])
        return False
