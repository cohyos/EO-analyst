"""Sitemap (`kind: sitemap`, Task B item 1, 2026-09-16) parsing.

Several `*_press` sources in `config/sources.yaml` (anduril, rafael, iai, thales, rheinmetall,
saab, controp) are `verified: false` for `kind: html` because their listing page renders
client-side or sits behind a WAF challenge -- yet several of them publish a plain, static
`sitemap.xml` (optionally using the Google News sitemap extension) that lists every dated article
URL without any JS/bot-gate. This module parses that XML directly (never the HTML listing page) so
`eoa.fetch.service._ingest_sitemap_source` can feed the resulting URLs into the same
article-fetch-and-store path `kind: html` sources already use.

Deliberately NOT reusing `eoa.fetch.service._extract_links`/`lxml.html.fromstring`: that helper
only reads an element's `href` ATTRIBUTE (built for `<a href>` listing pages), while a sitemap's
URL sits in the TEXT CONTENT of a `<loc>` element -- see
`docs/qa/content_review/CR-platform-opportunity.md` section 6 for the live finding that motivated
a dedicated parser rather than trying to bend the HTML-listing helper to this shape.
`lxml.html.fromstring` also chokes on a `str` containing an `<?xml ... encoding=...?>` declaration
(lxml requires bytes for that) -- this module always parses with `lxml.etree` against bytes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from lxml import etree
from pydantic import BaseModel

log = structlog.get_logger(__name__)

# Sitemap protocol + Google News sitemap extension namespaces (both optional on any given
# <url> node -- a plain sitemap.xml has neither prefix, just bare <loc>/<lastmod>).
_NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}


class SitemapEntry(BaseModel):
    """One parsed `<url>` entry from a sitemap (plain or Google-News-extended)."""

    url: str
    title: str | None = None
    published_at: datetime | None = None


def _local(tag: str) -> str:
    """Strip a `{namespace}tag` qualifier down to the bare local tag name -- lets this module
    match `loc`/`lastmod`/`publication_date`/`title` regardless of whether the document declared
    the sitemap/news namespaces with the expected prefixes, a default namespace, or (rare, but
    seen on hand-rolled feeds) no namespace at all."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    try:
        # `lastmod`/`news:publication_date` are ISO 8601 (W3C datetime); Python's fromisoformat
        # accepts "+00:00" but not a bare trailing "Z" before 3.11's relaxation -- normalize it.
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def parse_sitemap(
    raw: str | bytes,
    *,
    path_prefix: str | None = None,
    since_days: int | None = None,
    now: datetime | None = None,
) -> list[SitemapEntry]:
    """Parse a sitemap XML document (plain `<urlset>` or Google-News-extended) into
    `SitemapEntry` rows.

    `path_prefix`, when given (e.g. ``"/news/"``), drops any `<loc>` whose URL path does not start
    with it -- most vendor sitemaps mix press/news URLs with product pages, careers, etc.
    `since_days`, when given, drops entries whose `lastmod`/`news:publication_date` is older than
    `now - since_days`; an entry with no discoverable date is always kept (same "over-fetch rather
    than silently lose undated content" convention as `eoa.fetch.rss.parse_feed`).

    A `<sitemapindex>` (a sitemap-of-sitemaps) is logged and returns an empty list -- following
    the index chain is out of scope for this pre-check; a source whose only sitemap is an index
    stays `verified: false`, documented in `config/sources.yaml`'s notes.

    Never raises: malformed/empty XML returns an empty list (same fail-open convention as every
    other fetch-parsing helper in this package -- a single bad sitemap must never abort the run).
    """
    body = raw.encode("utf-8") if isinstance(raw, str) else raw
    try:
        root = etree.fromstring(body)
    except etree.XMLSyntaxError as exc:
        log.warning("fetch.sitemap_parse_failed", error=repr(exc))
        return []

    root_tag = _local(root.tag)
    if root_tag == "sitemapindex":
        log.info("fetch.sitemap_is_index_unsupported", child_count=len(root))
        return []
    if root_tag != "urlset":
        log.warning("fetch.sitemap_unexpected_root", root_tag=root_tag)
        return []

    cutoff: datetime | None = None
    if since_days is not None:
        reference = now or datetime.now(tz=UTC)
        cutoff = reference - timedelta(days=since_days)

    entries: list[SitemapEntry] = []
    for url_node in root:
        if _local(url_node.tag) != "url":
            continue
        loc: str | None = None
        lastmod_raw: str | None = None
        news_title: str | None = None
        news_pubdate_raw: str | None = None
        for child in url_node.iter():
            tag = _local(child.tag)
            text = (child.text or "").strip() or None
            if tag == "loc" and loc is None:
                loc = text
            elif tag == "lastmod" and lastmod_raw is None:
                lastmod_raw = text
            elif tag == "title" and news_title is None:
                news_title = text
            elif tag == "publication_date" and news_pubdate_raw is None:
                news_pubdate_raw = text

        if not loc:
            continue
        if path_prefix:
            from urllib.parse import urlsplit

            if not urlsplit(loc).path.startswith(path_prefix):
                continue

        published_at = _parse_datetime(news_pubdate_raw) or _parse_datetime(lastmod_raw)
        if cutoff is not None and published_at is not None and published_at < cutoff:
            continue

        entries.append(SitemapEntry(url=loc, title=news_title, published_at=published_at))

    return entries
