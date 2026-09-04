"""RSS/Atom feed parsing.

Uses `feedparser`, which is deliberately tolerant of malformed XML (it sets
`bozo=1` and keeps going rather than raising) — we log the bozo flag but never
let a malformed feed abort ingestion of the entries it *did* manage to parse.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import feedparser
import structlog
from pydantic import BaseModel

log = structlog.get_logger(__name__)


class FeedEntry(BaseModel):
    """One parsed `<item>`/`<entry>` from an RSS 2.0 or Atom feed."""

    url: str
    title: str | None = None
    published_at: datetime | None = None
    summary: str | None = None
    lang: str | None = None


def _struct_time_to_datetime(value: time.struct_time | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(time.mktime(value), tz=UTC)
    except (OverflowError, ValueError):
        return None


def _entry_published_at(entry: feedparser.FeedParserDict) -> datetime | None:
    # Prefer `published`, fall back to `updated` — matches RSS `pubDate` vs
    # Atom `updated`, and covers feeds (some blogs) that only set one.
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        dt = _struct_time_to_datetime(parsed)
        if dt is not None:
            return dt
    return None


def _entry_url(entry: feedparser.FeedParserDict) -> str | None:
    link = entry.get("link")
    if link:
        return link
    links = entry.get("links") or []
    for candidate in links:
        href = candidate.get("href")
        if href:
            return href
    return (
        entry.get("id")
        if isinstance(entry.get("id"), str) and entry.get("id", "").startswith("http")
        else None
    )


def _entry_summary(entry: feedparser.FeedParserDict) -> str | None:
    if entry.get("summary"):
        return entry["summary"]
    content = entry.get("content")
    if content:
        try:
            return content[0].get("value")
        except (IndexError, AttributeError, TypeError):
            return None
    return None


def parse_feed(
    raw: str | bytes,
    *,
    since_days: int | None = None,
    now: datetime | None = None,
) -> list[FeedEntry]:
    """Parse an RSS/Atom document into `FeedEntry` rows.

    Robust to malformed feeds: `feedparser` never raises on bad XML, it just
    sets `parsed.bozo`. We log that (once, at parse-feed level) and still
    return whatever entries were recovered.

    `since_days`, when given, drops entries whose `published_at` is older
    than `now - since_days`. Entries with no discoverable date are always
    kept (we'd rather over-fetch than silently lose undated content).
    """
    parsed = feedparser.parse(raw)

    if getattr(parsed, "bozo", 0):
        log.warning(
            "fetch.rss_feed_malformed",
            bozo_exception=repr(getattr(parsed, "bozo_exception", None)),
        )

    feed_lang = None
    feed_meta = getattr(parsed, "feed", None)
    if feed_meta is not None:
        feed_lang = feed_meta.get("language")
        if feed_lang:
            feed_lang = feed_lang.split("-")[0].lower()

    cutoff: datetime | None = None
    if since_days is not None:
        reference = now or datetime.now(tz=UTC)
        cutoff = reference - timedelta(days=since_days)

    entries: list[FeedEntry] = []
    for raw_entry in getattr(parsed, "entries", []) or []:
        url = _entry_url(raw_entry)
        if not url:
            log.debug("fetch.rss_entry_no_url", title=raw_entry.get("title"))
            continue

        published_at = _entry_published_at(raw_entry)
        if cutoff is not None and published_at is not None and published_at < cutoff:
            continue

        entry_lang = raw_entry.get("language") or feed_lang

        entries.append(
            FeedEntry(
                url=url,
                title=raw_entry.get("title"),
                published_at=published_at,
                summary=_entry_summary(raw_entry),
                lang=entry_lang,
            )
        )

    return entries
