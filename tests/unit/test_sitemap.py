"""Unit tests for eoa.fetch.sitemap.parse_sitemap (Task B item 1, 2026-09-16).

No DB/network: parses literal XML fixtures. Mirrors tests/unit/test_rss.py's conventions for
eoa.fetch.rss.parse_feed (the sibling `kind: rss` parser).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_sitemap.py -q``
"""

from __future__ import annotations

from datetime import UTC, datetime

from eoa.fetch.sitemap import SitemapEntry, parse_sitemap

_PLAIN_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/news/first-story</loc>
    <lastmod>2026-09-10T12:00:00+00:00</lastmod>
  </url>
  <url>
    <loc>https://example.com/careers/apply</loc>
    <lastmod>2026-09-12T12:00:00+00:00</lastmod>
  </url>
  <url>
    <loc>https://example.com/news/second-story</loc>
  </url>
</urlset>
"""

_NEWS_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url>
    <loc>https://example.com/news/fury-fit-checked</loc>
    <news:news>
      <news:publication_date>2026-09-15T08:00:00+00:00</news:publication_date>
      <news:title>Fury Fit Checked With Munitions</news:title>
    </news:news>
  </url>
</urlset>
"""

_SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap2.xml</loc></sitemap>
</sitemapindex>
"""


class TestParseSitemapPlain:
    def test_parses_all_entries_with_no_filter(self) -> None:
        entries = parse_sitemap(_PLAIN_SITEMAP)
        assert len(entries) == 3
        assert {e.url for e in entries} == {
            "https://example.com/news/first-story",
            "https://example.com/careers/apply",
            "https://example.com/news/second-story",
        }

    def test_path_prefix_filters_out_non_matching_urls(self) -> None:
        entries = parse_sitemap(_PLAIN_SITEMAP, path_prefix="/news/")
        urls = {e.url for e in entries}
        assert urls == {
            "https://example.com/news/first-story",
            "https://example.com/news/second-story",
        }

    def test_lastmod_is_parsed_into_published_at(self) -> None:
        entries = parse_sitemap(_PLAIN_SITEMAP, path_prefix="/news/first-story")
        assert len(entries) == 1
        assert entries[0].published_at == datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

    def test_entry_with_no_date_is_kept_undated(self) -> None:
        entries = parse_sitemap(_PLAIN_SITEMAP, path_prefix="/news/second-story")
        assert len(entries) == 1
        assert entries[0].published_at is None

    def test_since_days_drops_stale_dated_entries_but_keeps_undated(self) -> None:
        now = datetime(2026, 9, 16, tzinfo=UTC)
        entries = parse_sitemap(_PLAIN_SITEMAP, path_prefix="/news/", since_days=3, now=now)
        # first-story (2026-09-10) is 6 days old -> dropped; second-story (undated) -> kept.
        urls = {e.url for e in entries}
        assert urls == {"https://example.com/news/second-story"}

    def test_accepts_bytes_input(self) -> None:
        entries = parse_sitemap(_PLAIN_SITEMAP.encode("utf-8"))
        assert len(entries) == 3


class TestParseSitemapGoogleNewsExtension:
    def test_news_title_and_publication_date_extracted(self) -> None:
        entries = parse_sitemap(_NEWS_SITEMAP)
        assert entries == [
            SitemapEntry(
                url="https://example.com/news/fury-fit-checked",
                title="Fury Fit Checked With Munitions",
                published_at=datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
            )
        ]


class TestParseSitemapIndex:
    def test_sitemapindex_returns_empty_not_an_error(self) -> None:
        assert parse_sitemap(_SITEMAP_INDEX) == []


class TestParseSitemapMalformed:
    def test_garbage_xml_returns_empty_list(self) -> None:
        assert parse_sitemap("not xml at all <<>>") == []

    def test_empty_string_returns_empty_list(self) -> None:
        assert parse_sitemap("") == []

    def test_unexpected_root_element_returns_empty_list(self) -> None:
        assert parse_sitemap("<rss><channel></channel></rss>") == []

    def test_xml_declaration_with_encoding_as_str_does_not_raise(self) -> None:
        """Regression: lxml.html.fromstring raises ValueError on a `str` containing an
        `<?xml ... encoding=...?>` declaration (the exact pitfall documented in
        docs/qa/content_review/CR-platform-opportunity.md section 6, which motivated this
        dedicated lxml.etree-based parser instead of reusing the html-listing helper)."""
        entries = parse_sitemap(_PLAIN_SITEMAP)  # str, with an encoding declaration
        assert len(entries) == 3
