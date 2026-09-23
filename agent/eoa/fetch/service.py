"""The ingest service: RSS/HTML fetch -> sanitize -> store, for every configured source.

Run as a one-shot call (`run_ingest(...)`) or as a standing loop:

    python -m eoa.fetch.service

which fetches once on start, then re-runs every
`config.yaml`'s `schedule.daytime_rss_poll_minutes`. Meant to run inside the
`fetcher` container (the `egress`-network service), per `docs/CONVENTIONS.md`.

A single failing source never aborts the run: every source is wrapped in its
own try/except, failures are logged, counted in `IngestStats`, and bump that
source's `sources.fail_count` in the DB.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
import structlog

from eoa.errors import DeadlineExceeded, FetchError, LeaseLost
from eoa.execution import checkpoint

log = structlog.get_logger(__name__)


async def _guarded_fetch_page(url: str, *, client: httpx.AsyncClient | None = None):
    """Q2-13 (2026-09-06): every ingestion fetch -- feed URLs, listing pages and article links that
    come out of untrusted RSS/HTML content -- goes through the same SSRF guard as deep-search reads:
    the initial URL is validated (public IP, http(s), sane port) and every redirect hop is
    re-validated before it is requested, with the connection pinned to the validated IPs.

    `client`, when given (SOL-AUDIT-2026-09-24 efficiency item: "reuse an async HTTP client during
    one ingest run"), is passed straight through to `fetch_page` instead of letting it open and
    close a brand-new `httpx.AsyncClient` (and its own TCP/TLS handshake) for every single page --
    see `run_ingest`, which opens one client for the whole run and threads it down through every
    `_ingest_*_source` helper to here.
    """
    from eoa.fetch.html import fetch_page
    from eoa.fetch.remote import assert_public_http_url

    initial_ips = assert_public_http_url(url)
    return await fetch_page(
        url, client=client, validate_redirect=assert_public_http_url, pin_ips=initial_ips
    )


_CONCURRENCY = 6
_PER_DOMAIN_MIN_INTERVAL_SECONDS = 1.0
_RAW_TEXT_MAX_CHARS = 200_000


@dataclass
class IngestStats:
    """Outcome of one `run_ingest()` call."""

    sources_attempted: int = 0
    sources_failed: int = 0
    entries_seen: int = 0
    items_inserted: int = 0
    items_skipped: int = 0
    errors: list[str] = field(default_factory=list)


class _DomainThrottle:
    """Per-domain politeness gate: at most one request/second/domain, across all concurrent tasks."""

    def __init__(self, min_interval_seconds: float = _PER_DOMAIN_MIN_INTERVAL_SECONDS) -> None:
        self._min_interval = min_interval_seconds
        self._last_request_monotonic: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def wait(self, url: str) -> None:
        domain = urlsplit(url).netloc
        async with self._locks[domain]:
            now = time.monotonic()
            last = self._last_request_monotonic.get(domain, 0.0)
            remaining = self._min_interval - (now - last)
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._last_request_monotonic[domain] = time.monotonic()


def _bump_fail_count(source_name: str) -> None:
    """Best-effort `sources.fail_count += 1`, keyed by name -- used only when this run has no
    `sources.id` to bump by (see :func:`_touch_source_fetched`'s own fallback below, which is the
    normal path since every `run_ingest` caller does have `source_db_id`)."""
    try:
        from eoa.db import connection

        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE sources SET fail_count = fail_count + 1 WHERE name = %(name)s",
                {"name": source_name},
            )
    except (DeadlineExceeded, LeaseLost):
        raise
    except Exception as exc:
        log.debug("fetch.fail_count_bump_skipped", source=source_name, error=repr(exc))


def _touch_source_fetched(source_db_id: int | None, source_name: str, *, ok: bool) -> None:
    """D9 round-1 fix (docs/qa/loop/round_1_fixes.md, ``sources_recently_fetched``): record that
    this source was just attempted -- called once per source per :func:`_ingest_one_source` call,
    success or failure, so ``last_fetched_at`` (and ``fail_count``/``last_ok_at``) actually reflect
    ingestion activity instead of sitting at ``NULL``/0 forever (round-0: 0/60 sources fetched
    within 7 days). Falls back to the old name-keyed :func:`_bump_fail_count` (fail_count only, no
    ``last_fetched_at``) on the rare path where a source has no resolved DB id yet."""
    if source_db_id is None:
        if not ok:
            _bump_fail_count(source_name)
        return
    try:
        from eoa.memory.relational import touch_source_fetched

        touch_source_fetched(source_db_id, ok)
    except (DeadlineExceeded, LeaseLost):
        raise
    except Exception as exc:
        log.debug("fetch.source_touch_failed", source_id=source_db_id, error=repr(exc))


def _strip_tags_fast(html_text: str) -> str:
    """Bare visible-text extraction for `items.raw_text` (no sanitization beyond tag stripping)."""
    from lxml import html as lxml_html

    try:
        tree = lxml_html.fromstring(html_text)
        return tree.text_content()
    except (DeadlineExceeded, LeaseLost):
        raise
    except Exception:
        return html_text


def _extract_links(
    html_text: str, base_url: str, list_selector: str | None, link_selector: str | None
) -> list[str]:
    """Resolve article links out of an `html`-kind source's listing page, per its CSS selector hints."""
    if not list_selector:
        return []

    from lxml import html as lxml_html

    try:
        tree = lxml_html.fromstring(html_text)
    except (DeadlineExceeded, LeaseLost):
        raise
    except Exception as exc:
        log.warning("fetch.html_source_parse_failed", url=base_url, error=repr(exc))
        return []

    try:
        nodes = tree.cssselect(list_selector)
    except (DeadlineExceeded, LeaseLost):
        raise
    except Exception as exc:
        log.warning("fetch.bad_list_selector", url=base_url, selector=list_selector, error=repr(exc))
        return []

    links: list[str] = []
    for node in nodes:
        href = None
        if link_selector and link_selector != "self":
            targets = node.cssselect(link_selector)
            href = targets[0].get("href") if targets else None
        else:
            href = node.get("href")
        if href:
            links.append(urljoin(base_url, href))

    seen: set[str] = set()
    deduped: list[str] = []
    for link in links:
        if link not in seen and not _is_boilerplate_url(link):
            seen.add(link)
            deduped.append(link)
    return deduped


_BOILERPLATE_PATH_MARKERS = (
    "/privacy",
    "/terms",
    "/cookie",
    "/legal",
    "/accessibility",
    "/sitemap",
    "/newsletter",
    "/subscribe",
    "/login",
    "/signin",
    "/register",
    "/about-us",
    "/contact",
)


def _is_boilerplate_url(url: str) -> bool:
    """Q4-9 r2: site boilerplate pages (privacy policy, terms of use, cookie notice, login...) that a
    listing-page selector can sweep up are never articles -- skip them before fetching."""
    path = url.split("?", 1)[0].lower()
    return any(marker in path for marker in _BOILERPLATE_PATH_MARKERS)


#: F36 (SOL-AUDIT-2026-09-24): common tracking-parameter noise that turns one article into
#: several distinct rows (a shared post URL with a different `utm_source` per channel, a click-
#: tracker's `fbclid`/`gclid`, ...) -- stripped when computing an item's `canonical_url` identity.
#: Never mutates the observed `url` itself, which is still stored verbatim as provenance.
_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAM_NAMES = frozenset(
    {"fbclid", "gclid", "gclsrc", "mc_cid", "mc_eid", "igshid", "ref", "ref_src", "spm", "yclid", "msclkid"}
)


def _normalize_url_identity(url: str) -> str:
    """F36: normalize a URL (typically a fetch's `final_url`, after redirects) into the identity
    used for cross-alias dedup -- strips tracking query params and any fragment, lowercases the
    scheme/host, and drops a trailing path slash. Two observed URLs that both resolve/normalize to
    the same value are the same article for `relational.insert_item`'s `canonical_url` upsert."""
    parts = urlsplit(url)
    kept_params = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(_TRACKING_PARAM_PREFIXES) and key.lower() not in _TRACKING_PARAM_NAMES
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(kept_params), ""))


def _url_already_seen(url: str) -> bool:
    """F19: best-effort existence probe for ``items.url`` used by :func:`_store_item` to tell a
    genuine new-row insert from ``relational.insert_item``'s ``ON CONFLICT (url) DO UPDATE``
    refresh of an already-seen URL apart.

    ``insert_item`` always ``RETURNING id`` — on conflict it just bumps ``fetched_at`` and still
    returns the (pre-existing) row's id — so the old ``if item_id: stats.items_inserted += 1`` in
    ``_store_item`` counted every successful upsert as a fresh insert, whether or not a row was
    actually created (e.g. 115 reported vs. 61 real new rows over 26h). Checking first, here,
    keeps that shared upsert helper (also used by ``eoa.tenders.scan``) untouched. Race note: two
    concurrent fetches of the very same URL within one ``run_ingest`` call could both see "not
    seen" and both count as inserted even though only one row is actually new — acceptably rare
    given the per-domain throttle and that duplicate URLs normally come from a single domain.
    Any DB error here is treated as "not seen" (degrades to the old over-counting behaviour rather
    than blocking ingestion — docs/CONVENTIONS.md rule 9)."""
    from eoa.db import connection

    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM items WHERE url = %(url)s", {"url": url})
            return cur.fetchone() is not None
    except (DeadlineExceeded, LeaseLost):
        raise
    except Exception as exc:
        log.debug("fetch.url_seen_check_failed", url=url, error=repr(exc))
        return False


def _store_item(
    *,
    source_db_id: int | None,
    url: str,
    html_text: str,
    stats: IngestStats,
    fallback_title: str | None = None,
    fallback_published_at: datetime | None = None,
    http_status: int | None = None,
    final_url: str | None = None,
) -> None:
    from eoa.fetch.sanitize import (
        BLOCKED_ITEM_TITLE_HE,
        choose_title,
        detect_block_page,
        extract_clean_text,
        text_hash,
    )
    from eoa.memory import relational

    clean = extract_clean_text(html_text, url)
    raw_text = _strip_tags_fast(html_text)[:_RAW_TEXT_MAX_CHARS]

    # Use explicit title fallback chain
    title = choose_title(
        clean_title=clean.title,
        fallback_title=fallback_title,
        html=html_text,
        clean_text=clean.text,
        url=url,
    )
    published_at = clean.published_at or fallback_published_at

    # Q4-1: a Cloudflare/WAF/anti-bot challenge page fetched instead of the real article must
    # never be stored as if it were real content -- neither its title/body (a human- or
    # LLM-facing lie about what was actually retrieved) nor a `security_status='clean'` that
    # would let it flow into classify/analyze/RAG. Detected here, before `insert_item`, using the
    # already-decoded HTML/text and the HTTP status this fetch got.
    is_blocked = detect_block_page(html_text, clean.text, http_status)
    stored_title = BLOCKED_ITEM_TITLE_HE if is_blocked else title
    stored_clean_text = None if is_blocked else clean.text
    stored_lang = None if is_blocked else clean.lang

    # F36: identify this row by its normalized final URL (after redirects, tracking params
    # stripped) so a redirect alias or tracking-variant of an already-stored article upserts that
    # same row -- see `_normalize_url_identity` and `insert_item`'s own docstring. `url` itself
    # (the as-observed/as-fetched address) is still stored verbatim, unchanged.
    canonical_url = _normalize_url_identity(final_url or url)
    if canonical_url == url:
        canonical_url = None  # nothing to add over the plain `ON CONFLICT (url)` path

    try:
        # F09/efficiency item: a single quality-aware upsert replaces the old
        # pre-insert-`_url_already_seen`-probe-then-plain-upsert pair -- `content_status`/
        # `security_status` here let `insert_item` refresh blocked/stub content on a later good
        # fetch (never the reverse) and `.inserted` tells a genuine new row apart from a same-
        # URL/same-canonical-URL refresh without the extra round trip the old probe cost.
        result = relational.insert_item(
            source_id=source_db_id,
            url=url,
            canonical_url=canonical_url,
            title=stored_title,
            lang=stored_lang,
            published_at=published_at,
            raw_text=raw_text,
            clean_text=stored_clean_text,
            text_hash=text_hash(clean.text),
            content_status="stub" if is_blocked else "full",
            security_status="blocked" if is_blocked else "clean",
        )
    except (DeadlineExceeded, LeaseLost):
        raise
    except Exception as exc:
        log.warning("fetch.item_store_failed", url=url, error=repr(exc))
        stats.items_skipped += 1
        return

    if is_blocked and result.inserted:
        log.info("fetch.block_page_detected", url=url, item_id=int(result), status=http_status)

    if result.inserted:
        stats.items_inserted += 1
    else:
        stats.items_skipped += 1


async def _fetch_and_store(
    url: str,
    *,
    throttle: _DomainThrottle,
    source_db_id: int | None,
    stats: IngestStats,
    fallback_title: str | None = None,
    fallback_published_at: datetime | None = None,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Returns whether the fetch itself succeeded (F11: callers use this to tell "every article
    fetch for this source failed" apart from "some/all succeeded", instead of the previous
    always-``None`` return that let a source with a 100%-failing fetch step still get recorded as
    successfully ingested)."""
    await throttle.wait(url)
    try:
        page = await _guarded_fetch_page(url, client=client)
    except (DeadlineExceeded, LeaseLost):
        raise
    except Exception as exc:
        log.warning("fetch.article_fetch_failed", url=url, error=repr(exc))
        return False

    _store_item(
        source_db_id=source_db_id,
        url=url,
        html_text=page.html,
        stats=stats,
        fallback_title=fallback_title,
        fallback_published_at=fallback_published_at,
        http_status=page.status,
        final_url=page.final_url,
    )
    return True


def _matches_keywords(entry, keywords_any: list[str]) -> bool:
    """A12: does an RSS entry's title/summary contain at least one of `keywords_any`
    (case-insensitive substring)? Always True when the source sets no `keywords_any` (the
    default -- every other source is unaffected)."""
    if not keywords_any:
        return True
    haystack = f"{entry.title or ''} {entry.summary or ''}".casefold()
    return any(kw.casefold() in haystack for kw in keywords_any)


async def _ingest_rss_source(
    source,
    *,
    source_db_id: int | None,
    since_days: int,
    throttle: _DomainThrottle,
    stats: IngestStats,
    client: httpx.AsyncClient | None = None,
) -> None:
    checkpoint()
    from eoa.fetch.rss import parse_feed

    await throttle.wait(source.url)
    feed_page = await _guarded_fetch_page(source.url, client=client)
    entries = parse_feed(feed_page.html, since_days=since_days)
    stats.entries_seen += len(entries)

    keywords_any = getattr(source, "keywords_any", None) or []
    if keywords_any:
        entries = [e for e in entries if _matches_keywords(e, keywords_any)]

    # F11: if every article this source's feed pointed at failed to fetch, the source itself did
    # not succeed -- raising here (only when at least one was attempted) routes through
    # `_ingest_one_source`'s `except FetchError` clause, which counts the source as failed and
    # skips `_touch_source_fetched(ok=True)` instead of recording a clean run with zero real
    # content. A source whose feed legitimately had nothing new (`entries` empty) is unaffected.
    attempted = 0
    succeeded = 0
    for entry in entries:
        checkpoint()
        attempted += 1
        if await _fetch_and_store(
            entry.url,
            throttle=throttle,
            source_db_id=source_db_id,
            stats=stats,
            fallback_title=entry.title,
            fallback_published_at=entry.published_at,
            client=client,
        ):
            succeeded += 1
    if attempted and not succeeded:
        raise FetchError(f"all {attempted} article fetch(es) failed for source {source.id}")


async def _ingest_html_source(
    source,
    *,
    source_db_id: int | None,
    throttle: _DomainThrottle,
    stats: IngestStats,
    client: httpx.AsyncClient | None = None,
) -> None:
    checkpoint()
    await throttle.wait(source.url)
    listing_page = await _guarded_fetch_page(source.url, client=client)
    links = _extract_links(listing_page.html, source.url, source.list_selector, source.link_selector)
    stats.entries_seen += len(links)

    # F11: same "don't report a source as OK when every article fetch failed" guard as
    # `_ingest_rss_source` -- see its comment.
    attempted = 0
    succeeded = 0
    for link in links:
        checkpoint()
        attempted += 1
        if await _fetch_and_store(
            link, throttle=throttle, source_db_id=source_db_id, stats=stats, client=client
        ):
            succeeded += 1
    if attempted and not succeeded:
        raise FetchError(f"all {attempted} article fetch(es) failed for source {source.id}")


async def _ingest_sitemap_source(
    source,
    *,
    source_db_id: int | None,
    since_days: int,
    throttle: _DomainThrottle,
    stats: IngestStats,
    client: httpx.AsyncClient | None = None,
) -> None:
    """`kind: sitemap` (Task B item 1, 2026-09-16): parse the source's sitemap XML
    (`eoa.fetch.sitemap.parse_sitemap`) and feed the resulting article URLs into the same
    fetch-and-store path `kind: html` sources use (`_fetch_and_store`) -- see
    `agent/eoa/fetch/sitemap.py`'s module docstring for why this is a dedicated parser rather than
    reusing `_extract_links`. `source.path_prefix`, when set, restricts which `<loc>` URLs count
    (most vendor sitemaps mix press/news URLs with product/careers/legal pages)."""
    checkpoint()
    from eoa.fetch.sitemap import parse_sitemap

    await throttle.wait(source.url)
    sitemap_page = await _guarded_fetch_page(source.url, client=client)
    entries = parse_sitemap(
        sitemap_page.html, path_prefix=getattr(source, "path_prefix", None), since_days=since_days
    )
    stats.entries_seen += len(entries)

    # F11: same "don't report a source as OK when every article fetch failed" guard as
    # `_ingest_rss_source` -- see its comment.
    attempted = 0
    succeeded = 0
    for entry in entries:
        checkpoint()
        attempted += 1
        if await _fetch_and_store(
            entry.url,
            throttle=throttle,
            source_db_id=source_db_id,
            stats=stats,
            fallback_title=entry.title,
            fallback_published_at=entry.published_at,
            client=client,
        ):
            succeeded += 1
    if attempted and not succeeded:
        raise FetchError(f"all {attempted} article fetch(es) failed for source {source.id}")


#: LinkedIn post ids (activity / ugcPost / share) are Snowflake-style: the top bits are the post's
#: creation time in epoch milliseconds (``id >> 22``).
_LINKEDIN_POST_ID_RE = re.compile(r"(?:activity|ugcPost|share)[-:](\d{18,20})")

#: How far back a `kind: search` hit may be dated and still be stored. Search results are not
#: time-ordered: on 2026-09-23 196 of 303 stored LinkedIn "posts" were from 2017-2025, and with no
#: `published_at` they entered today's reports as news (a 2022 Iron Beam post in tech report 261).
SEARCH_HIT_MAX_AGE_DAYS = 30


def search_hit_published_at(url: str, published: str | None = None) -> datetime | None:
    """Best-effort publication time of a search hit: the LinkedIn post id in the URL, else the
    provider's own `published` field (ddgs news results carry one). ``None`` when neither is
    available -- the caller keeps such a hit (it cannot be judged stale)."""
    m = _LINKEDIN_POST_ID_RE.search(url or "")
    if m:
        try:
            return datetime.fromtimestamp((int(m.group(1)) >> 22) / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            pass
    if published:
        try:
            parsed = datetime.fromisoformat(published.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _store_search_hit(*, source_db_id: int | None, hit, stats: IngestStats, published_at: datetime | None = None) -> None:
    """Task B item 2 (2026-09-16): store one `eoa.search.provider.SearchHit` directly from its
    title+snippet -- no full-page fetch (LinkedIn/X company-post pages are not fetchable without
    login, and there is no API access to another company's own posts either; the search result
    snippet IS the content).

    Marked `content_status='stub'` -- NOT the task brief's literal `'snippet'`, which does not
    exist: `items.content_status` has a DB CHECK constraint of exactly `'full'/'partial'/'stub'`
    (confirmed live 2026-09-16; a `'snippet'` write violates it and is silently dropped by this
    function's own best-effort except-block, which is how this was actually caught -- every write
    logged `fetch.search_hit_content_status_failed` and left `content_status` NULL). `'stub'` is
    the existing value for exactly this shape of content (too short for a full analysis pass --
    see `eoa.fetch.content_quality.assess`, which `eoa.pipeline.analyze._content_status_precheck`
    calls on every item at analyze time and would itself compute `'stub'` for a search snippet's
    length anyway, overwriting whatever this function set here); reusing it avoids both a second
    schema migration and inventing a status value the rest of the pipeline never checks for."""
    from eoa.memory import relational

    try:
        # F09/efficiency item: content_status='stub'/security_status='clean' passed directly into
        # the quality-aware upsert -- no separate pre-probe or post-insert `update_item_fields`
        # call needed (see `_store_item`'s equivalent comment). A stub snippet never overwrites an
        # already-stored fuller fetch of the same URL (lower quality rank); it also never
        # downgrades `security_status` since a search hit's own status is always 'clean'.
        result = relational.insert_item(
            source_id=source_db_id,
            url=hit.url,
            title=hit.title or None,
            clean_text=hit.snippet or None,
            lang=None,
            published_at=published_at,
            content_status="stub",
            security_status="clean",
        )
    except (DeadlineExceeded, LeaseLost):
        raise
    except Exception as exc:
        log.warning("fetch.search_hit_store_failed", url=hit.url, error=repr(exc))
        stats.items_skipped += 1
        return

    if result.inserted:
        stats.items_inserted += 1
    else:
        stats.items_skipped += 1


async def _ingest_search_source(source, *, source_db_id: int | None, stats: IngestStats) -> None:
    """`kind: search` (Task B item 2, 2026-09-16): run each of `source.queries` through
    `eoa.search.provider.search` (the ddgs-backed provider, same one `config/tenders.yaml`'s own
    `kind: search` sources use via `eoa.tenders.scan._fetch_search` -- see that module for the
    sibling implementation) and store each hit directly from its search-result snippet via
    `_store_search_hit`. Built for LinkedIn/X company-post monitoring
    (`site:linkedin.com/posts "<company>"`-style queries, see `config/sources.yaml`'s
    `*_linkedin_search` entries) but generic over any `kind: search` source's `queries` list.
    A failed/errored query is logged and skipped -- never aborts the source's remaining queries."""
    checkpoint()
    from eoa.search.provider import search as run_search_query

    for query in source.queries:
        checkpoint()
        resp = run_search_query(query, source.engine_lang, max_results=source.max_results)
        if resp.error:
            log.warning(
                "fetch.search_source_query_failed", source_id=source.id, query=query[:80], error=resp.error
            )
            continue
        stats.entries_seen += len(resp.hits)
        cutoff = datetime.now(UTC) - timedelta(days=SEARCH_HIT_MAX_AGE_DAYS)
        for hit in resp.hits:
            checkpoint()
            published_at = search_hit_published_at(hit.url, getattr(hit, "published", None))
            if published_at is not None and published_at < cutoff:
                stats.items_skipped += 1
                log.debug("fetch.search_hit_stale", source_id=source.id, url=hit.url, published_at=str(published_at))
                continue
            _store_search_hit(source_db_id=source_db_id, hit=hit, stats=stats, published_at=published_at)


#: F05 (SOL-AUDIT-2026-09-24): a lookback margin/ceiling on top of "how long since this source's
#: last successful fetch" -- a brief outage doesn't need to land exactly on the boundary to be
#: caught, but a source that has never succeeded (or has been down a very long time) doesn't
#: demand months of backfill on every run.
_LOOKBACK_MARGIN_DAYS = 1
_LOOKBACK_MAX_DAYS = 21

#: F35 (SOL-AUDIT-2026-09-24): a `schedule: weekly` source is "due" only once this long has passed
#: since its last successful fetch (or it has never succeeded) -- keeps it off the ordinary
#: 2-hour daytime poll (`main.py`'s `daytime_poll` cron job) except when it's actually due. The
#: nightly "ingest" pipeline stage runs through this exact same `run_ingest` selection, so a due
#: weekly source is picked up there too -- "the nightly run still fetches all due-or-daily
#: sources" -- with no separate bypass needed.
_WEEKLY_SOURCE_DUE_INTERVAL_DAYS = 7


def _sources_last_success_map(source_db_ids: list[int]) -> dict[int, datetime | None]:
    """Best-effort batch read of `COALESCE(last_ok_at, last_fetched_at)` for every id in
    `source_db_ids`, in one round trip -- backs both F05 (per-source lookback) and F35 (per-source
    due-by-schedule selection). `last_ok_at` (migration 0019) may not exist on a DB behind HEAD;
    any query failure (including that one) degrades to "unknown last success" for every id, same
    best-effort convention as the rest of this module (docs/CONVENTIONS.md rule 9)."""
    if not source_db_ids:
        return {}
    from eoa.db import connection

    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, COALESCE(last_ok_at, last_fetched_at) AS last_success "
                "FROM sources WHERE id = ANY(%(ids)s)",
                {"ids": list(source_db_ids)},
            )
            # The pool hands out dict rows (`row_factory=dict_row` in eoa.db) -- index by name. The
            # first version indexed `row[0]`, raised KeyError(0) on every run and silently
            # disabled F05/F35 (found in the full-suite run on 2026-09-24).
            return {row["id"]: row["last_success"] for row in cur.fetchall()}
    except (DeadlineExceeded, LeaseLost):
        raise
    except Exception as exc:
        # warning, not debug: a failure here quietly widens/narrows every source's lookback.
        log.warning("fetch.source_last_success_lookup_failed", error=repr(exc))
        return {}


def _lookback_days(
    last_success: datetime | None, default_since_days: int, *, now: datetime | None = None
) -> int:
    """F05: a source not successfully fetched in longer than `default_since_days` gets a wider
    lookback for this run -- elapsed-since-last-success plus a margin, bounded to
    `_LOOKBACK_MAX_DAYS` so a source down (or never working) for months doesn't demand an
    unbounded backfill. A source with no known last-success (new, or the lookup failed/degraded)
    keeps the caller's plain `default_since_days`."""
    if last_success is None:
        return default_since_days
    reference = now or datetime.now(UTC)
    if last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=UTC)
    elapsed_days = max(0, (reference - last_success).days)
    return min(max(default_since_days, elapsed_days + _LOOKBACK_MARGIN_DAYS), _LOOKBACK_MAX_DAYS)


def _source_is_due(schedule: str, last_success: datetime | None, *, now: datetime | None = None) -> bool:
    """F35: `schedule: daily` sources are due on every call (unchanged cadence). `schedule: weekly`
    sources are due only when never successfully fetched, or when at least
    `_WEEKLY_SOURCE_DUE_INTERVAL_DAYS` have passed since the last successful fetch."""
    if schedule != "weekly":
        return True
    if last_success is None:
        return True
    reference = now or datetime.now(UTC)
    if last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=UTC)
    return (reference - last_success) >= timedelta(days=_WEEKLY_SOURCE_DUE_INTERVAL_DAYS)


async def _ingest_one_source(
    source,
    *,
    source_db_id: int | None,
    since_days: int,
    throttle: _DomainThrottle,
    stats: IngestStats,
    client: httpx.AsyncClient | None = None,
) -> None:
    checkpoint()
    stats.sources_attempted += 1
    try:
        if source.kind == "rss":
            await _ingest_rss_source(
                source,
                source_db_id=source_db_id,
                since_days=since_days,
                throttle=throttle,
                stats=stats,
                client=client,
            )
        elif source.kind == "sitemap":
            await _ingest_sitemap_source(
                source,
                source_db_id=source_db_id,
                since_days=since_days,
                throttle=throttle,
                stats=stats,
                client=client,
            )
        elif source.kind == "search":
            await _ingest_search_source(source, source_db_id=source_db_id, stats=stats)
        else:
            await _ingest_html_source(
                source, source_db_id=source_db_id, throttle=throttle, stats=stats, client=client
            )
    except FetchError as exc:
        stats.sources_failed += 1
        stats.errors.append(f"{source.id}: {exc}")
        log.warning("fetch.source_failed", source_id=source.id, error=str(exc))
        _touch_source_fetched(source_db_id, source.name, ok=False)
        return
    except (DeadlineExceeded, LeaseLost):
        raise
    except Exception as exc:
        stats.sources_failed += 1
        stats.errors.append(f"{source.id}: {exc!r}")
        log.warning("fetch.source_failed_unexpected", source_id=source.id, error=repr(exc))
        _touch_source_fetched(source_db_id, source.name, ok=False)
        return
    _touch_source_fetched(source_db_id, source.name, ok=True)


async def run_ingest(source_ids: list[int] | None = None, since_days: int = 3) -> IngestStats:
    """Fetch, sanitize, and store items for the given DB source ids (or every configured source).

    `source_ids`, when given, are `sources.id` DB row ids (not the yaml
    slugs in `config/sources.yaml`) — every configured source is upserted
    first (to resolve slug -> DB id), then filtered down to the requested
    set.
    """
    checkpoint()
    from eoa.fetch.sources_loader import load_sources, upsert_sources_to_db

    # Round-3 (D9 finding 5, docs/qa/loop/round_1_judge.md): upsert *every* configured source,
    # enabled or not, so `sources.active` stays in sync with `config/sources.yaml` (a disabled
    # source's row gets `active=false`, and a renamed/removed source's now-orphaned old row also
    # gets deactivated -- see `upsert_sources_to_db`/`relational.deactivate_orphaned_sources`).
    # Only *then* filter down to `enabled` sources for actual fetching (Q4-2/Q4-3: a source with
    # `enabled: false` -- a dead feed with no replacement, or one robots.txt blocks entirely --
    # still isn't fetched, so it never accumulates `fail_count` for a path known not to work).
    all_sources = load_sources()
    id_map = upsert_sources_to_db(all_sources)
    sources = [s for s in all_sources if s.enabled]

    targets = [(id_map.get(s.id), s) for s in sources]
    if source_ids is not None:
        wanted = set(source_ids)
        targets = [(db_id, s) for db_id, s in targets if db_id in wanted]

    # F05/F35: one batched read of every targeted source's last successful fetch, used to (a)
    # skip a `schedule: weekly` source that isn't due yet (F35) and (b) widen this run's lookback
    # for a source that's been down longer than the plain `since_days` default (F05) -- see
    # `_source_is_due`/`_lookback_days`.
    known_db_ids = [db_id for db_id, _ in targets if db_id is not None]
    last_success_map = _sources_last_success_map(known_db_ids)

    due_targets: list[tuple[int | None, object, int]] = []
    for db_id, source in targets:
        last_success = last_success_map.get(db_id) if db_id is not None else None
        schedule = getattr(source, "schedule", None) or "daily"
        if not _source_is_due(schedule, last_success):
            log.debug("fetch.source_skipped_not_due", source_id=source.id, schedule=schedule)
            continue
        due_targets.append((db_id, source, _lookback_days(last_success, since_days)))

    stats = IngestStats()
    throttle = _DomainThrottle()
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    # Efficiency item (SOL-AUDIT-2026-09-24): one shared `httpx.AsyncClient` for the whole run --
    # `_guarded_fetch_page`/`fetch_page` otherwise open and close a brand-new client (a fresh
    # TCP/TLS handshake) per page fetch. Threaded down through every `_ingest_*_source` helper;
    # each individual fetch's own `timeout=`/byte cap/retry behavior is unaffected (those are set
    # per-request, not per-client).
    async with httpx.AsyncClient() as client:

        async def _bounded(db_id: int | None, source, effective_since_days: int) -> None:
            async with semaphore:
                await _ingest_one_source(
                    source,
                    source_db_id=db_id,
                    since_days=effective_since_days,
                    throttle=throttle,
                    stats=stats,
                    client=client,
                )

        await asyncio.gather(
            *(_bounded(db_id, s, eff_since_days) for db_id, s, eff_since_days in due_targets)
        )

    log.info(
        "fetch.ingest_complete",
        attempted=stats.sources_attempted,
        failed=stats.sources_failed,
        entries_seen=stats.entries_seen,
        items_inserted=stats.items_inserted,
        items_skipped=stats.items_skipped,
    )
    return stats


def _run_forever() -> None:
    """Fetcher container entrypoint: serve `ingest`/`fetch_url` jobs from the queue, plus a periodic
    daytime RSS poll (the nightly ingest is requested by the agent as a job)."""
    from eoa.config import settings
    from eoa.fetch.remote import serve_fetch_jobs

    log.info("fetch.service_start")
    try:
        from eoa.notify.relay import start_relay_thread

        start_relay_thread()  # public-topic mirror (only this container has egress)
    except (DeadlineExceeded, LeaseLost):
        raise
    except Exception as exc:
        log.warning("fetch.relay_start_failed", error=str(exc)[:120])
    interval_s = settings().schedule.daytime_rss_poll_minutes * 60
    while True:
        serve_fetch_jobs(stop_after=interval_s)
        log.info("fetch.service_poll_tick")
        try:
            asyncio.run(run_ingest())
        except (DeadlineExceeded, LeaseLost):
            raise
        except Exception as exc:
            log.error("fetch.poll_failed", error=str(exc)[:200])


if __name__ == "__main__":
    _run_forever()
