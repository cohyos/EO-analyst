"""SearXNG metasearch client (JSON API) with a simple per-minute rate limit."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import httpx
import structlog

from eoa.config import settings

log = structlog.get_logger(__name__)

LANG_MAP = {
    "he": "he-IL",
    "en": "en-US",
    "ru": "ru-RU",
    "zh": "zh-CN",
    "fr": "fr-FR",
    "de": "de-DE",
    "ar": "ar-EG",
    "ko": "ko-KR",
    "tr": "tr-TR",
}


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
                log.debug("searxng_rate_limit_sleep", seconds=round(sleep_for, 1))
                time.sleep(max(sleep_for, 0))
            self._stamps.append(time.monotonic())


_limiter: _RateLimiter | None = None


def _limiter_instance() -> _RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = _RateLimiter(settings().searxng.rate_limit_per_minute)
    return _limiter


def search(
    query: str,
    lang: str = "en",
    *,
    categories: str = "general",
    max_results: int = 10,
    time_range: str | None = None,
    engines: list[str] | None = None,
) -> SearchResponse:
    """Run one query. Never raises; returns ``error`` on failure so the ReAct loop can continue."""
    s = settings().searxng
    _limiter_instance().wait()
    params: dict[str, str] = {
        "q": query,
        "format": "json",
        "language": LANG_MAP.get(lang, lang),
        "categories": categories,
        "safesearch": "0",
    }
    if time_range:
        params["time_range"] = time_range
    chosen = engines or s.engines_by_lang.get(lang) or s.engines
    if chosen:
        params["engines"] = ",".join(chosen)
    try:
        r = httpx.get(
            f"{settings().searxng_url.rstrip('/')}/search",
            params=params,
            timeout=25,
            headers={"Accept": "application/json", "User-Agent": settings().fetch.user_agent},
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        log.warning("searxng_failed", query=query[:80], lang=lang, error=str(exc)[:160])
        return SearchResponse(query, lang, error=str(exc)[:200])
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for res in data.get("results", []):
        url = res.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        hits.append(
            SearchHit(
                url=url,
                title=res.get("title") or "",
                snippet=(res.get("content") or "")[:400],
                engine=res.get("engine") or "",
                score=float(res.get("score") or 0),
                published=res.get("publishedDate"),
            )
        )
        if len(hits) >= max_results:
            break
    log.info("searxng_ok", query=query[:80], lang=lang, hits=len(hits))
    return SearchResponse(query, lang, hits)


def ping() -> bool:
    """True if SearXNG answers its healthz endpoint."""
    try:
        return httpx.get(f"{settings().searxng_url.rstrip('/')}/healthz", timeout=3).status_code == 200
    except Exception:
        return False
