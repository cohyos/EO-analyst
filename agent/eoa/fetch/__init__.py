"""Fetch layer: RSS/HTML ingestion, sanitization, and the ingest service loop.

Runs primarily inside the `fetcher` container (the only compute service with
general internet egress, per `docs/CONVENTIONS.md` rule 13). All DB/config
access in this package is imported lazily inside functions so
`tests/unit/test_*.py` can exercise `rss.py` / `html.py` / `sanitize.py`
without a database or a fully-populated `config/*.yaml`.
"""

from __future__ import annotations

from eoa.fetch.html import FetchedPage, fetch_page
from eoa.fetch.rss import FeedEntry, parse_feed
from eoa.fetch.sanitize import CleanText, detect_lang, extract_clean_text, text_hash

__all__ = [
    "CleanText",
    "FeedEntry",
    "FetchedPage",
    "detect_lang",
    "extract_clean_text",
    "fetch_page",
    "parse_feed",
    "text_hash",
]
