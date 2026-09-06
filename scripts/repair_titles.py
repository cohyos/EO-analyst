#!/usr/bin/env python
"""Repair empty/null titles in the items table, plus (Q4-9) mistitled items whose extractor
grabbed the wrong element on the page.

Two independent modes:

* Default (no flags): the original null/empty-title repair -- recompute from stored raw_text/
  clean_text/URL (no network) and UPDATE.
* `--requality`: Q4-9 (docs/qa/findings_Q4_r1.md) -- items whose *non-empty* title is wrong
  because `extract_clean_text`'s old title-extraction priority picked the wrong element (Globes:
  the article's own lead paragraph; Leonardo: a "Financial highlights" sidebar widget reused on
  12 different press releases). Since the DB never kept the original HTML (`raw_text` is already
  tag-stripped), fixing these requires a live re-fetch of each flagged URL, then re-running
  `eoa.fetch.sanitize.choose_title` with today's priority (og:title / <title> / <h1> first --
  see that module's Q4-9 comment). Flags a title as a repair candidate when it is either an exact
  known-bad phrase / over 200 chars, OR repeated 3+ times across different items from the same
  source (the per-domain frequency signal `choose_title` itself can't compute, having no DB
  access) -- Leonardo's "Financial highlights" is caught by both; a one-off long lead paragraph
  is caught by the length check alone.

Usage:
    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \
    PYTHONPATH=agent python scripts/repair_titles.py                    # null-title repair (writes)
    PYTHONPATH=agent python scripts/repair_titles.py --requality        # Q4-9 dry-run (default)
    PYTHONPATH=agent python scripts/repair_titles.py --requality --apply

Note: Requires `raw_text` column (visible text via lxml, pre-sanitization) and
`clean_text` column (post-sanitization) to already exist in items table.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from urllib.parse import urlparse

# Ensure agent/ is in path
sys.path.insert(0, "agent")

import structlog

log = structlog.get_logger(__name__)


def _url_path_as_title(url: str) -> str | None:
    """Extract the last path segment from URL as a title candidate."""
    if not url:
        return None

    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if path:
        segments = path.split("/")
        last_segment = segments[-1]
        if last_segment:
            return last_segment
    return None


def _normalize_title(text: str | None) -> str | None:
    """Normalize whitespace and strip, returning None if empty."""
    if not text:
        return None

    import re

    # Strip surrounding whitespace
    cleaned = text.strip()
    # Collapse internal whitespace
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned if cleaned else None


def _extract_title_from_html_text(html_like_text: str) -> str | None:
    """Try to extract title from raw_text if it still contains HTML tags."""
    import re

    # Try og:title first
    og_match = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html_like_text, re.IGNORECASE)
    if og_match:
        return og_match.group(1)

    og_match = re.search(r"<meta\s+property='og:title'\s+content='([^']+)'", html_like_text, re.IGNORECASE)
    if og_match:
        return og_match.group(1)

    # Try <title> tag
    title_match = re.search(r"<title\s*>([^<]+)<\s*/\s*title\s*>", html_like_text, re.IGNORECASE)
    if title_match:
        return title_match.group(1)

    return None


def _choose_title_from_item(raw_text: str | None, clean_text: str | None, url: str) -> str:
    """Reconstruct title from raw/clean text and URL (no HTML available in DB).

    Fallback chain (no HTML since we're repairing from DB):
    1. First line of clean_text (≤ 120 chars)
    2. HTML extraction from raw_text (og:title or <title> tag if present)
    3. First line of raw_text (≤ 120 chars)
    4. URL path segment
    5. "Untitled"
    """
    # Try clean_text first
    if clean_text:
        for line in clean_text.split("\n"):
            normalized = _normalize_title(line)
            if normalized:
                return normalized[:120]

    # Try HTML extraction from raw_text (which may still contain HTML)
    if raw_text:
        html_title = _extract_title_from_html_text(raw_text)
        if html_title:
            normalized = _normalize_title(html_title)
            if normalized:
                return normalized[:120]

    # Fall back to first line of raw_text
    if raw_text:
        for line in raw_text.split("\n"):
            normalized = _normalize_title(line)
            if normalized:
                return normalized[:120]

    # Fall back to URL
    url_title = _url_path_as_title(url)
    if url_title:
        return url_title

    return "Untitled"


def run_repair() -> tuple[int, int, int]:
    """Scan items for empty/null titles, recompute and update.

    Returns (total_rows_with_empty_title, repaired, failed).
    """
    from eoa.db import connection

    repaired = 0
    failed = 0
    total_empty = 0

    try:
        with connection() as conn, conn.cursor() as cur:
            # Find items with null or empty/whitespace-only title
            cur.execute(
                """
                SELECT id, url, raw_text, clean_text
                FROM items
                WHERE title IS NULL OR title ~ '^[[:space:]]*$'
                ORDER BY created_at DESC
                """
            )
            rows = cur.fetchall()
            total_empty = len(rows)

            if total_empty == 0:
                log.info("repair.no_empty_titles_found")
                return 0, 0, 0

            log.info("repair.empty_titles_found", count=total_empty)

            for row in rows:
                # psycopg3 returns dict_row, access by column name not index
                item_id = row["id"]
                url = row["url"]
                raw_text = row["raw_text"]
                clean_text = row["clean_text"]

                try:
                    new_title = _choose_title_from_item(raw_text, clean_text, url)

                    # Update the item
                    cur.execute(
                        "UPDATE items SET title = %s WHERE id = %s",
                        (new_title, item_id),
                    )
                    repaired += 1
                    log.debug(
                        "repair.title_updated",
                        item_id=item_id,
                        url=url,
                        new_title=new_title[:60],
                    )

                except Exception as exc:
                    failed += 1
                    log.warning(
                        "repair.title_update_failed",
                        item_id=item_id,
                        url=url,
                        error=repr(exc),
                    )

            # Commit the transaction
            conn.commit()

    except Exception as exc:
        log.error("repair.database_connection_failed", error=repr(exc))
        return total_empty, repaired, failed

    log.info("repair.complete", total=total_empty, repaired=repaired, failed=failed)
    return total_empty, repaired, failed


# --------------------------------------------------------------------------
# Q4-9: re-quality repair for wrong-but-non-empty titles (Globes lead paragraph,
# Leonardo "Financial highlights" sidebar widget) -- requires a live re-fetch.
# --------------------------------------------------------------------------

_REQUALITY_MIN_REPEAT_COUNT = 3  # a title reused this many+ times across one source's items


@dataclass
class RequalityStats:
    candidates: int = 0
    refetched: int = 0
    changed: int = 0
    unchanged: int = 0
    fetch_failed: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    examples: list[dict] = field(default_factory=list)


def _requality_candidates(cur) -> list[dict]:
    """Rows worth re-fetching: a known-bad/oversized title, or a title repeated 3+ times across
    different items from the same source (a per-domain "boilerplate widget" signature)."""
    from eoa.fetch.sanitize import _GENERIC_TITLE_PHRASES  # reuse the same literal list

    cur.execute(
        """
        SELECT i.id, i.url, i.title, i.security_status,
               COALESCE(s.name, 'unknown') AS source_name,
               count(*) OVER (PARTITION BY i.source_id, i.title) AS title_repeat_count
        FROM items i
        LEFT JOIN sources s ON s.id = i.source_id
        WHERE i.title IS NOT NULL AND i.title !~ '^[[:space:]]*$'
          AND i.security_status <> 'blocked'
        """
    )
    rows = cur.fetchall()
    candidates = []
    for row in rows:
        title = row["title"]
        is_known_bad = len(title) > 200 or title.strip().lower() in _GENERIC_TITLE_PHRASES
        is_repeated = row["title_repeat_count"] >= _REQUALITY_MIN_REPEAT_COUNT
        # Globes real bug (item 67 et al.): the lead paragraph got picked as the title, then
        # truncated to exactly 120 chars by this very script's original null-title repair (or the
        # old choose_title's rung-4 "first line of text" cap, same 120-char limit) -- an *organic*
        # headline essentially never lands on that exact boundary, so length == 120 on the nose is
        # a strong, specific signal of a truncated-body-text title rather than a real one.
        is_truncated_at_cap = len(title) == 120
        if is_known_bad or is_repeated or is_truncated_at_cap:
            candidates.append(row)
    return candidates


async def _refetch_and_choose_title(url: str) -> str | None:
    """Live re-fetch `url` and run today's `choose_title` chain on it. Returns ``None`` on any
    fetch failure (dead link, now-blocked, robots disallow, ...) -- never raises, so one bad URL
    doesn't abort the batch."""
    from eoa.fetch.html import fetch_page
    from eoa.fetch.sanitize import choose_title, detect_block_page, extract_clean_text

    try:
        page = await fetch_page(url)
    except Exception as exc:
        log.debug("requality.fetch_failed", url=url, error=repr(exc))
        return None

    clean = extract_clean_text(page.html, url)
    if detect_block_page(page.html, clean.text, page.status):
        log.debug("requality.now_blocked", url=url)
        return None

    return choose_title(
        clean_title=clean.title,
        fallback_title=None,  # the original RSS entry title (if any) isn't retained on the row
        html=page.html,
        clean_text=clean.text,
        url=url,
    )


async def run_requality_repair(*, apply: bool, limit: int | None = None) -> RequalityStats:
    from eoa.db import connection

    stats = RequalityStats()

    with connection() as conn, conn.cursor() as cur:
        rows = _requality_candidates(cur)
        if limit:
            rows = rows[:limit]
        stats.candidates = len(rows)

        for row in rows:
            new_title = await _refetch_and_choose_title(row["url"])
            if new_title is None:
                stats.fetch_failed += 1
                continue
            stats.refetched += 1

            if new_title == row["title"]:
                stats.unchanged += 1
                continue

            stats.changed += 1
            stats.by_source[row["source_name"]] = stats.by_source.get(row["source_name"], 0) + 1
            if len(stats.examples) < 20:
                stats.examples.append(
                    {
                        "id": row["id"],
                        "url": row["url"],
                        "source": row["source_name"],
                        "old_title": row["title"][:100],
                        "new_title": new_title[:100],
                    }
                )

            if apply:
                cur.execute("UPDATE items SET title = %s WHERE id = %s", (new_title, row["id"]))

        if apply:
            conn.commit()
        else:
            conn.rollback()

    return stats


def _print_requality_report(stats: RequalityStats, *, apply: bool) -> None:
    print(f"\n{'=' * 64}")
    print(f"Q4-9 title re-quality repair ({'APPLY' if apply else 'DRY-RUN'})")
    print(f"{'=' * 64}")
    print(f"  Candidates (bad/oversized/repeated title): {stats.candidates}")
    print(f"  Successfully re-fetched:                   {stats.refetched}")
    print(f"  Fetch failed (skipped, left unchanged):    {stats.fetch_failed}")
    print(f"  Title changed:                             {stats.changed}")
    print(f"  Title unchanged (re-fetch agreed):         {stats.unchanged}")
    print("  Changed, by source:")
    for name, count in sorted(stats.by_source.items(), key=lambda kv: -kv[1]):
        print(f"    {name:<35} {count}")
    if stats.examples:
        print("  Examples (up to 20):")
        for ex in stats.examples[:5]:
            print(f"    id={ex['id']:<6} source={ex['source']}")
            print(f"      old: {ex['old_title']!r}")
            print(f"      new: {ex['new_title']!r}")
    print(f"{'=' * 64}\n")


# --------------------------------------------------------------------------
# Q5-13 (docs/qa/findings_Q5_r2.md): titles left with a raw, undecoded HTML entity (e.g.
# "Israel&#39;s Aero Sentinel") -- from before eoa.fetch.sanitize.choose_title ran html.unescape
# on every candidate. Pure text transform on the already-stored title; unlike --requality, this
# needs no live re-fetch.
# --------------------------------------------------------------------------

# Matches a named entity (&amp;, &#39;, &nbsp;, ...), a decimal numeric reference (&#39;), or a
# hex numeric reference (&#x27;) -- the three forms `html.unescape` decodes.
_ENTITY_SQL_PATTERN = r"&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z]+);"


@dataclass
class UnescapeStats:
    candidates: int = 0
    changed: int = 0
    examples: list[dict] = field(default_factory=list)


def run_unescape_repair(*, apply: bool, limit: int | None = None) -> UnescapeStats:
    """Scan `items.title` for a raw HTML entity, `html.unescape` it, and (with `--apply`) write
    the decoded title back. Dry-run by default, same convention as `--requality`."""
    import html as html_lib
    import re

    from eoa.db import connection

    stats = UnescapeStats()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, title FROM items WHERE title IS NOT NULL AND title ~ %s ORDER BY id",
            (_ENTITY_SQL_PATTERN,),
        )
        rows = cur.fetchall()
        if limit:
            rows = rows[:limit]
        stats.candidates = len(rows)

        for row in rows:
            old_title = row["title"]
            new_title = html_lib.unescape(old_title)
            new_title = re.sub(r"\s+", " ", new_title).strip()
            if new_title == old_title:
                continue

            stats.changed += 1
            if len(stats.examples) < 20:
                stats.examples.append(
                    {"id": row["id"], "old_title": old_title[:100], "new_title": new_title[:100]}
                )
            if apply:
                cur.execute("UPDATE items SET title = %s WHERE id = %s", (new_title, row["id"]))

        if apply:
            conn.commit()
        else:
            conn.rollback()

    return stats


def _print_unescape_report(stats: UnescapeStats, *, apply: bool) -> None:
    print(f"\n{'=' * 64}")
    print(f"Q5-13 title HTML-entity unescape repair ({'APPLY' if apply else 'DRY-RUN'})")
    print(f"{'=' * 64}")
    print(f"  Candidates (title contains a raw HTML entity): {stats.candidates}")
    print(f"  Titles changed:                                {stats.changed}")
    if stats.examples:
        print("  Examples (up to 20):")
        for ex in stats.examples[:10]:
            print(f"    id={ex['id']}")
            print(f"      old: {ex['old_title']!r}")
            print(f"      new: {ex['new_title']!r}")
    print(f"{'=' * 64}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--requality",
        action="store_true",
        help="Run the Q4-9 wrong-title re-fetch repair instead of the null-title repair.",
    )
    parser.add_argument(
        "--unescape",
        action="store_true",
        help="Run the Q5-13 HTML-entity unescape repair (no re-fetch) instead of the null-title repair.",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write updates (default: dry-run). --requality/--unescape only."
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Cap candidates scanned. --requality/--unescape only."
    )
    args = parser.parse_args()

    if args.requality:
        stats = asyncio.run(run_requality_repair(apply=args.apply, limit=args.limit))
        _print_requality_report(stats, apply=args.apply)
        return 0

    if args.unescape:
        unescape_stats = run_unescape_repair(apply=args.apply, limit=args.limit)
        _print_unescape_report(unescape_stats, apply=args.apply)
        return 0

    total, repaired, failed = run_repair()
    print(
        f"\n{'=' * 60}"
        f"\nTitle Repair Summary:"
        f"\n  Total with empty title:  {total}"
        f"\n  Successfully repaired:   {repaired}"
        f"\n  Failed repairs:          {failed}"
        f"\n{'=' * 60}\n"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
