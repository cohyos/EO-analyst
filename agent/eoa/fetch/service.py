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
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlsplit

import structlog

log = structlog.get_logger(__name__)


async def _guarded_fetch_page(url: str):
    """Q2-13 (2026-09-06): every ingestion fetch -- feed URLs, listing pages and article links that
    come out of untrusted RSS/HTML content -- goes through the same SSRF guard as deep-search reads:
    the initial URL is validated (public IP, http(s), sane port) and every redirect hop is
    re-validated before it is requested, with the connection pinned to the validated IPs."""
    from eoa.fetch.html import fetch_page
    from eoa.fetch.remote import assert_public_http_url

    initial_ips = assert_public_http_url(url)
    return await fetch_page(url, validate_redirect=assert_public_http_url, pin_ips=initial_ips)


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
    except Exception as exc:
        log.debug("fetch.source_touch_failed", source_id=source_db_id, error=repr(exc))


def _strip_tags_fast(html_text: str) -> str:
    """Bare visible-text extraction for `items.raw_text` (no sanitization beyond tag stripping)."""
    from lxml import html as lxml_html

    try:
        tree = lxml_html.fromstring(html_text)
        return tree.text_content()
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
    except Exception as exc:
        log.warning("fetch.html_source_parse_failed", url=base_url, error=repr(exc))
        return []

    try:
        nodes = tree.cssselect(list_selector)
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

    # F19: probe for an existing row *before* the upsert, so a same-URL refresh (conflict) can be
    # told apart from a genuine new row — see `_url_already_seen`.
    already_seen = _url_already_seen(url)

    try:
        item_id = relational.insert_item(
            source_id=source_db_id,
            url=url,
            title=stored_title,
            lang=stored_lang,
            published_at=published_at,
            raw_text=raw_text,
            clean_text=stored_clean_text,
            text_hash=text_hash(clean.text),
        )
    except Exception as exc:
        log.warning("fetch.item_store_failed", url=url, error=repr(exc))
        stats.items_skipped += 1
        return

    # Only stamp `security_status='blocked'` on a genuinely new row. `insert_item`'s
    # ON CONFLICT path never touches title/clean_text on a refetch of an already-seen URL, so
    # downgrading an existing (possibly good) row here on a merely-transient block would disagree
    # with what is actually stored -- and could clobber a previously successful fetch's status.
    if is_blocked and not already_seen:
        try:
            relational.update_item_fields(item_id, security_status="blocked")
        except Exception as exc:
            log.warning("fetch.blocked_status_update_failed", url=url, item_id=item_id, error=repr(exc))
        log.info("fetch.block_page_detected", url=url, item_id=item_id, status=http_status)

    if item_id and not already_seen:
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
) -> None:
    await throttle.wait(url)
    try:
        page = await _guarded_fetch_page(url)
    except Exception as exc:
        log.warning("fetch.article_fetch_failed", url=url, error=repr(exc))
        return

    _store_item(
        source_db_id=source_db_id,
        url=url,
        html_text=page.html,
        stats=stats,
        fallback_title=fallback_title,
        fallback_published_at=fallback_published_at,
        http_status=page.status,
    )


def _matches_keywords(entry, keywords_any: list[str]) -> bool:
    """A12: does an RSS entry's title/summary contain at least one of `keywords_any`
    (case-insensitive substring)? Always True when the source sets no `keywords_any` (the
    default -- every other source is unaffected)."""
    if not keywords_any:
        return True
    haystack = f"{entry.title or ''} {entry.summary or ''}".casefold()
    return any(kw.casefold() in haystack for kw in keywords_any)


async def _ingest_rss_source(
    source, *, source_db_id: int | None, since_days: int, throttle: _DomainThrottle, stats: IngestStats
) -> None:
    from eoa.fetch.rss import parse_feed

    await throttle.wait(source.url)
    feed_page = await _guarded_fetch_page(source.url)
    entries = parse_feed(feed_page.html, since_days=since_days)
    stats.entries_seen += len(entries)

    keywords_any = getattr(source, "keywords_any", None) or []
    if keywords_any:
        entries = [e for e in entries if _matches_keywords(e, keywords_any)]

    for entry in entries:
        await _fetch_and_store(
            entry.url,
            throttle=throttle,
            source_db_id=source_db_id,
            stats=stats,
            fallback_title=entry.title,
            fallback_published_at=entry.published_at,
        )


async def _ingest_html_source(
    source, *, source_db_id: int | None, throttle: _DomainThrottle, stats: IngestStats
) -> None:
    await throttle.wait(source.url)
    listing_page = await _guarded_fetch_page(source.url)
    links = _extract_links(listing_page.html, source.url, source.list_selector, source.link_selector)
    stats.entries_seen += len(links)

    for link in links:
        await _fetch_and_store(link, throttle=throttle, source_db_id=source_db_id, stats=stats)


async def _ingest_one_source(
    source, *, source_db_id: int | None, since_days: int, throttle: _DomainThrottle, stats: IngestStats
) -> None:
    from eoa.errors import FetchError

    stats.sources_attempted += 1
    try:
        if source.kind == "rss":
            await _ingest_rss_source(
                source, source_db_id=source_db_id, since_days=since_days, throttle=throttle, stats=stats
            )
        else:
            await _ingest_html_source(source, source_db_id=source_db_id, throttle=throttle, stats=stats)
    except FetchError as exc:
        stats.sources_failed += 1
        stats.errors.append(f"{source.id}: {exc}")
        log.warning("fetch.source_failed", source_id=source.id, error=str(exc))
        _touch_source_fetched(source_db_id, source.name, ok=False)
        return
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
    from eoa.fetch.sources_loader import load_sources, upsert_sources_to_db

    # Q4-2/Q4-3: a source config can be `enabled: false` (dead feed with no replacement, or
    # robots.txt blocks its only feed) -- excluded before it is even upserted to `sources`, so it
    # neither gets fetched nor accumulates `fail_count` for a fetch path known not to work.
    sources = [s for s in load_sources() if s.enabled]
    id_map = upsert_sources_to_db(sources)

    targets = [(id_map.get(s.id), s) for s in sources]
    if source_ids is not None:
        wanted = set(source_ids)
        targets = [(db_id, s) for db_id, s in targets if db_id in wanted]

    stats = IngestStats()
    throttle = _DomainThrottle()
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def _bounded(db_id: int | None, source) -> None:
        async with semaphore:
            await _ingest_one_source(
                source, source_db_id=db_id, since_days=since_days, throttle=throttle, stats=stats
            )

    await asyncio.gather(*(_bounded(db_id, s) for db_id, s in targets))

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
    except Exception as exc:
        log.warning("fetch.relay_start_failed", error=str(exc)[:120])
    interval_s = settings().schedule.daytime_rss_poll_minutes * 60
    while True:
        serve_fetch_jobs(stop_after=interval_s)
        log.info("fetch.service_poll_tick")
        try:
            asyncio.run(run_ingest())
        except Exception as exc:
            log.error("fetch.poll_failed", error=str(exc)[:200])


if __name__ == "__main__":
    _run_forever()
