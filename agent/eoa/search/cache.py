"""Per-query search-result cache (round 4, docs/qa/loop, 2026-09-06 evening incident).

One file per normalised ``(provider, query, lang, categories, time_range, engines)`` key under
``runtime/cache/search/`` (created on first use; never inside the repo — matches the
`runtime/models`, `runtime/logs` convention in `eoa.config.REPO_ROOT`). A hit within
``search.cache_ttl_hours`` skips the network entirely. Only successful responses (``error is
None`` — including a legitimate zero-hit search) are cached; a transient failure is never cached,
so the next call retries the network (subject to the circuit breaker in `eoa.search.circuit`).

Set ``EOA_SEARCH_NO_CACHE=1`` to bypass the cache completely (neither read nor write) — the
escape hatch for a one-off "I know the cache is stale" run.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from eoa.config import REPO_ROOT, settings

if TYPE_CHECKING:
    from eoa.search.provider import SearchResponse

log = structlog.get_logger(__name__)

CACHE_DIR = Path(os.environ.get("EOA_SEARCH_CACHE_DIR", REPO_ROOT / "runtime" / "cache" / "search"))


def _no_cache() -> bool:
    return os.environ.get("EOA_SEARCH_NO_CACHE", "").strip() in {"1", "true", "yes"}


def cache_key(
    provider: str,
    query: str,
    lang: str,
    *,
    categories: str = "general",
    time_range: str | None = None,
    engines: list[str] | None = None,
) -> str:
    """Normalise the call signature into a stable hash usable as a cache filename stem."""
    normalized_query = " ".join(query.strip().lower().split())
    normalized_engines = tuple(sorted(e.strip().lower() for e in engines)) if engines else ()
    raw = json.dumps(
        [provider, normalized_query, lang.strip().lower(), categories, time_range or "", normalized_engines],
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _path_for(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def get(key: str) -> SearchResponse | None:
    """Return a cached, still-fresh ``SearchResponse``, or ``None`` on miss/expiry/disabled."""
    if _no_cache():
        return None
    path = _path_for(key)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.debug("search_cache_read_failed", key=key, error=str(exc)[:160])
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        log.debug("search_cache_corrupt", key=key)
        return None

    ttl_hours = settings().search.cache_ttl_hours
    age_s = time.time() - float(data.get("created_at", 0))
    if ttl_hours <= 0 or age_s > ttl_hours * 3600:
        # ttl_hours <= 0 means caching is effectively disabled for reads (every entry is treated
        # as already expired) -- distinct from EOA_SEARCH_NO_CACHE, which also skips writing.
        return None

    from eoa.search.provider import SearchHit, SearchResponse

    hits = [SearchHit(**h) for h in data.get("hits", [])]
    return SearchResponse(query=data["query"], lang=data["lang"], hits=hits, error=None)


def put(key: str, response: SearchResponse) -> None:
    """Persist a successful response. No-op for a response carrying an error, or when disabled."""
    if _no_cache() or response.error is not None:
        return
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "query": response.query,
            "lang": response.lang,
            "created_at": time.time(),
            "hits": [asdict(h) for h in response.hits],
        }
        tmp = _path_for(key).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_path_for(key))
    except OSError as exc:
        log.debug("search_cache_write_failed", key=key, error=str(exc)[:160])
