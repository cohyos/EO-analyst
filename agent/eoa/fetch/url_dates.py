"""URL-derived dates and "is this actually an article" heuristics.

R4 (SOL-REVIEW3-2026-09-24 carryover, 2026-09-28): five `kind: search` sources added
2026-09-27 (army_technology_search, naval_technology_search, airforce_technology_search,
defense_update_search, unmanned_systems_technology_search) surface hits with no
provider-supplied date, so `eoa.fetch.service.search_hit_published_at` had nothing to fall
back on and stored them with `published_at IS NULL` -- treated as today's news by report
recency (`COALESCE(published_at, created_at)`; a Feb-2026 Fortem/World-Cup story led the
2026-09-28 daily report). Several of these sites embed a real publication date directly in
the URL path (WordPress-style `/YYYY/MM/DD/slug/` or `/YYYY/MM/slug/`, or a compact
`YYYYMMDD_slug` filename). `date_from_url` recovers that date conservatively -- only from
clearly date-shaped path segments, sanity-bounded to a plausible year range -- and never
guesses from an arbitrary run of digits (an article/post id) that happens to look date-like.

The same sources also surfaced index/category/pagination pages as if they were articles
(`/news/`, `/2026/09`, `.../latest-news/`). `is_probable_article_url` is the companion check
for that -- applied ONLY to `kind: search` hits (`eoa.fetch.service._ingest_search_source`);
an RSS or sitemap entry is already known to be an article by construction (the feed/sitemap
itself enumerates articles), so it is never run through this filter.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from urllib.parse import urlparse

#: A URL date before this year is almost certainly a misparsed number (a product model year,
#: a phone number fragment, ...), not a real publication date.
_MIN_YEAR = 2000

# WordPress-style dated permalinks: /YYYY/MM/DD/... or /YYYY/MM/... (lookahead so the
# trailing separator/end-of-path isn't consumed and can't be double-counted by a later regex).
_PATH_YMD_RE = re.compile(r"/(\d{4})/(\d{2})/(\d{2})(?=/|$|[-_.])")
_PATH_YM_RE = re.compile(r"/(\d{4})/(\d{2})(?=/|$|[-_.])")
# Compact filename dates: 20260220_slug, 20260220-slug.html -- never preceded/followed by
# another digit, so this can't fire in the middle of a longer numeric id.
_COMPACT_YMD_RE = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")
# ISO-ish dashed dates embedded in a slug: some-slug-2026-09-20 or 2026-09-20-some-slug.
_DASHED_YMD_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


def _valid_date(y: int, m: int, d: int, *, max_year: int) -> date | None:
    if not (_MIN_YEAR <= y <= max_year):
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def date_from_url(url: str, *, now: datetime | None = None) -> date | None:
    """Best-effort publication date recovered from `url`'s path.

    Recognises, in order of specificity: `/YYYY/MM/DD/`, a dashed `YYYY-MM-DD` slug token, a
    compact `YYYYMMDD` filename token, then falls back to a bare `/YYYY/MM/` (day defaults to
    the 1st). The year is sanity-bounded to `_MIN_YEAR..now.year+1`; `date()` itself rejects an
    impossible month/day. Returns `None` when nothing date-shaped is found in the path -- this
    never guesses a date from an arbitrary number (an article id, a LinkedIn post id, a model
    number, ...) that isn't in one of these specific shapes.
    """
    if not url:
        return None
    try:
        path = urlparse(url).path
    except ValueError:
        return None
    if not path:
        return None
    max_year = (now or datetime.now(UTC)).year + 1

    for rx, has_day in ((_PATH_YMD_RE, True), (_DASHED_YMD_RE, True), (_COMPACT_YMD_RE, True), (_PATH_YM_RE, False)):
        m = rx.search(path)
        if not m:
            continue
        day = int(m.group(3)) if has_day else 1
        got = _valid_date(int(m.group(1)), int(m.group(2)), day, max_year=max_year)
        if got is not None:
            return got
    return None


#: Last-path-segment values that mean "this is a listing page", not an article, no matter what
#: precedes them.
_LISTING_LAST_SEGMENTS = {
    "news",
    "latest-news",
    "articles",
    "article",
    "press-releases",
    "press-release",
    "blog",
    "insights",
    "resources",
    "publications",
}
#: First-path-segment values that mean "everything under here is a listing", e.g. /tag/foo,
#: /category/bar, /author/baz.
_LISTING_FIRST_SEGMENTS = {
    "category",
    "categories",
    "tag",
    "tags",
    "topic",
    "topics",
    "author",
    "authors",
    "page",
}
_ARTICLE_EXT_RE = re.compile(r"\.(html?|php|aspx?|jsp)$", re.IGNORECASE)
_BARE_YEAR_RE = re.compile(r"^\d{4}$")
_BARE_MONTH_RE = re.compile(r"^\d{2}$")
#: A long digit run (a CMS post id, a LinkedIn activity id, ...) is a strong "this is a real
#: article, not a listing" signal on its own -- but a bare 4-digit year is not, hence >=5.
_LONG_DIGIT_RUN_RE = re.compile(r"(?<!\d)\d{5,}(?!\d)")


def is_probable_article_url(url: str) -> bool:
    """Conservative "is this a real article page, not an index/category/tag/pagination
    listing" check for a `kind: search` hit's URL.

    Accepted when the last path segment looks like a genuine article slug: at least three
    hyphen/underscore-separated words, OR a long (>=5 digit) numeric id, OR a URL-embedded date
    (`date_from_url`) together with at least two slug words. Rejected: an empty path, a bare
    `/news/`-style section root, a `/category/...`/`/tag/...`/`/page/N` listing, or a bare
    `/YYYY` or `/YYYY/MM` archive root with nothing after it.
    """
    if not url:
        return False
    try:
        path = urlparse(url).path
    except ValueError:
        return False
    segments = [s for s in path.split("/") if s]
    if not segments:
        return False
    if segments[0].lower() in _LISTING_FIRST_SEGMENTS:
        return False
    if any(s.lower() == "page" for s in segments[:-1]):
        return False  # .../page/2/... pagination, regardless of what follows

    last = _ARTICLE_EXT_RE.sub("", segments[-1])
    if last.lower() in _LISTING_LAST_SEGMENTS:
        return False
    if _BARE_YEAR_RE.fullmatch(last):
        return False  # bare /YYYY archive root
    if len(segments) >= 2 and _BARE_YEAR_RE.fullmatch(segments[-2]) and _BARE_MONTH_RE.fullmatch(last):
        return False  # bare /YYYY/MM archive root

    words = [w for w in re.split(r"[-_]", last) if w]
    slug_words = [w for w in words if not w.isdigit()]
    if len(slug_words) >= 3:
        return True
    if _LONG_DIGIT_RUN_RE.search(last):
        return True
    return len(slug_words) >= 2 and date_from_url(url) is not None
