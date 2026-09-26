"""Load `config/sources.yaml` into validated pydantic models and upsert into the DB.

Kept separate from `service.py` so `run_ingest` and anything else that just
needs the configured source list doesn't have to import DB code, and so the
loader itself is trivially unit-testable against an arbitrary YAML path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import structlog
import yaml
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)

# agent/eoa/fetch/sources_loader.py -> parents[3] is the repo root.
_REPO_ROOT = Path(os.environ.get("EOA_ROOT", Path(__file__).resolve().parents[3]))
_DEFAULT_SOURCES_PATH = _REPO_ROOT / "config" / "sources.yaml"


class Source(BaseModel):
    """One `config/sources.yaml` entry, validated."""

    id: str
    name: str
    url: str
    kind: Literal["rss", "html", "sitemap", "search"]
    lang: str
    reliability: int = Field(ge=1, le=5)
    tags: list[str] = Field(default_factory=list)
    schedule: Literal["daily", "weekly"] = "daily"
    # Q4-2/Q4-3 (docs/qa/findings_Q4_r1.md): a source with no working fetch path at all (feed
    # dead with no replacement, robots.txt blocks the only feed that exists) is disabled here
    # rather than left in the config to fail every run -- `run_ingest` filters these out before
    # upserting/fetching. Defaults to True so every pre-existing entry (no `enabled:` key) is
    # unaffected.
    enabled: bool = True
    notes: str | None = None
    verified: bool = False
    verified_at: str | None = None
    list_selector: str | None = None
    link_selector: str | None = None
    # A12 (מעקב טכנולוגי, 2026-09-06): additive. `category` is free-text metadata (e.g.
    # "science") carried through to the DB row for downstream reporting; `keywords_any`, when
    # set, restricts a broad `rss` feed (site-wide, journal TOC, etc.) to entries whose title or
    # summary contains at least one of these substrings (case-insensitive) -- applied in
    # `eoa.fetch.service._ingest_rss_source` before the article is even fetched, so an
    # off-topic issue of e.g. an IEEE journal TOC doesn't burn a fetch + LLM classify call.
    category: str | None = None
    keywords_any: list[str] = Field(default_factory=list)
    # Task B item 1 (2026-09-16): `kind: sitemap` only -- restrict which sitemap `<loc>` URLs
    # count (e.g. "/news/") since most vendor sitemaps mix press/news URLs with product, careers,
    # and legal pages. See `eoa.fetch.sitemap.parse_sitemap`.
    path_prefix: str | None = None
    # Task B item 2 (2026-09-16): `kind: search` only -- the search queries to run for this
    # source (e.g. LinkedIn/X company-post lookups via `eoa.search.provider.search`) plus display
    # metadata for the resulting items. See `eoa.fetch.service._ingest_search_source`.
    queries: list[str] = Field(default_factory=list)
    engine_lang: str = "en"
    max_results: int = 10
    # 2026-09-27 (nightly-ingest partial-ingest fix): robots.txt is enforced by default for every
    # fetch (`eoa.fetch.html.fetch_page`'s own `respect_robots` default), but a handful of official
    # APIs -- arXiv's export API is the motivating case, whose robots.txt is a blanket
    # `Disallow: /` even though its own API Terms of Use explicitly invite unattended programmatic
    # access -- have terms that permit exactly the kind of unattended fetching robots.txt exists to
    # gate. This is a per-source, explicit opt-out for THAT case only: it is threaded down to
    # `fetch_page` as an explicit keyword (`eoa.fetch.service._guarded_fetch_page`), never as a
    # global config switch, so it can never leak to a concurrently-fetched source that didn't set
    # it. Leave this `True` (the default) for anything that is not an official API whose terms
    # affirmatively allow this -- it is never an excuse to scrape a site whose robots.txt says no.
    respect_robots: bool = True
    # Pairs with `respect_robots: false` for a source whose API terms mandate a slower rate than
    # this fetcher's default 1 request/second/domain (e.g. arXiv's "no more than one request every
    # 3 seconds") -- `eoa.fetch.service._DomainThrottle` takes the MAX of the default interval and
    # every source's own configured value for a given domain, so setting this only ever widens
    # (never narrows) the gap between requests to that domain, even when another source without a
    # configured value also fetches it. `None` (the default) applies no per-source override.
    min_request_interval_s: float | None = None


def load_sources(path: str | Path | None = None) -> list[Source]:
    """Load and validate every entry in `config/sources.yaml` (or an alternate `path`)."""
    file_path = Path(path) if path is not None else _DEFAULT_SOURCES_PATH
    with file_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    entries = raw.get("sources", [])
    sources = [Source.model_validate(entry) for entry in entries]
    log.debug("fetch.sources_loaded", count=len(sources), path=str(file_path))
    return sources


def upsert_sources_to_db(sources: list[Source] | None = None) -> dict[str, int]:
    """Upsert *every* configured source into the `sources` table -- including ones with
    `enabled: false` (Q4-2/Q4-3: a dead feed / robots.txt-blocked source is never fetched, but its
    DB row must still exist and be kept in sync).

    Returns a `{source.id (yaml slug): db_row_id}` map — the DB table keys
    sources by `name`, not by our yaml slug, so callers that need to resolve
    a slug (e.g. `run_ingest(source_ids=...)`) go through this map.

    Imported lazily: unit tests for `sources_loader.load_sources()` never
    touch the DB.

    Round-3 (D9 finding 5, docs/qa/loop/round_1_judge.md): every source's DB `active` flag is set
    to `source.enabled` on every call (previously only ever `True` -- a source disabled in config
    kept `active=true` in the DB forever, since ``run_ingest`` filtered disabled sources out
    *before* calling this function at all, so `upsert_source` was simply never invoked for them).
    Also deactivates any DB row whose `name` isn't among the sources passed here at all (see
    `relational.deactivate_orphaned_sources` -- catches a renamed-in-config source's now-orphaned
    old-name row, which would otherwise sit at `active=true`/`last_fetched_at=NULL` forever).
    Callers that want this orphan/active-flag sync to see the *whole* config (including disabled
    entries) must pass the unfiltered `load_sources()` result, not a pre-filtered subset.
    """
    from eoa.memory import relational

    resolved = sources if sources is not None else load_sources()
    id_map: dict[str, int] = {}
    for source in resolved:
        db_id = relational.upsert_source(
            name=source.name,
            url=source.url,
            kind=source.kind,
            lang=source.lang,
            reliability=source.reliability,
            active=source.enabled,
        )
        id_map[source.id] = db_id
    relational.deactivate_orphaned_sources({s.name for s in resolved})
    log.info("fetch.sources_upserted", count=len(id_map))
    return id_map
