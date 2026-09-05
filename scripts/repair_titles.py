#!/usr/bin/env python
"""Repair empty/null titles in the items table.

For each item with a null or empty title, recompute using the explicit title
fallback chain and UPDATE the database. Prints counts of fixed rows.

Usage:
    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \
    PYTHONPATH=agent python scripts/repair_titles.py

Note: Requires `raw_text` column (visible text via lxml, pre-sanitization) and
`clean_text` column (post-sanitization) to already exist in items table.
"""

from __future__ import annotations

import sys
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


if __name__ == "__main__":
    total, repaired, failed = run_repair()
    print(
        f"\n{'='*60}"
        f"\nTitle Repair Summary:"
        f"\n  Total with empty title:  {total}"
        f"\n  Successfully repaired:   {repaired}"
        f"\n  Failed repairs:          {failed}"
        f"\n{'='*60}\n"
    )
    sys.exit(0 if failed == 0 else 1)
