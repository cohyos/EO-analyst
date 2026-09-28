"""Unit tests for `eoa.fetch.url_dates`: URL-embedded dates and the "is this an article, not a
listing page" heuristic (R4, SOL-REVIEW3-2026-09-24 carryover, 2026-09-28).

No DB/network access -- pure string/regex logic against real URLs from the incident report
(docs/qa/content_review/SOL-REVIEW3-2026-09-24.md): the Fortem/2026-World-Cup story that led the
2026-09-28 daily report, the defense-update.com compact-date filename, the L3Harris newsroom
2024/2025 items, and the index/listing pages that were stored as if they were articles.
"""

from __future__ import annotations

from datetime import date, datetime

from eoa.fetch.url_dates import date_from_url, is_probable_article_url


class TestDateFromUrl:
    def test_path_year_month_day(self) -> None:
        assert date_from_url("https://example.com/2026/02/17/some-article-slug/") == date(2026, 2, 17)

    def test_path_year_month_only_defaults_to_first_of_month(self) -> None:
        # The Fortem/2026-World-Cup story (2026-09-28 daily report incident): the site's search
        # hit carried no date at all, only this /YYYY/MM/ path.
        assert date_from_url("https://www.unmannedsystemstechnology.com/2026/02/fortem-world-cup/") == date(
            2026, 2, 1
        )

    def test_l3harris_newsroom_editorial_2024_09(self) -> None:
        assert date_from_url(
            "https://www.l3harris.com/newsroom/editorial/2024/09/some-2024-story-slug"
        ) == date(2024, 9, 1)

    def test_l3harris_newsroom_editorial_2025_12(self) -> None:
        assert date_from_url(
            "https://www.l3harris.com/newsroom/editorial/2025/12/some-2025-story-slug"
        ) == date(2025, 12, 1)

    def test_compact_yyyymmdd_filename(self) -> None:
        assert date_from_url("https://defense-update.com/20260220_cuas-report.html") == date(2026, 2, 20)

    def test_dashed_yyyy_mm_dd_in_slug(self) -> None:
        assert date_from_url("https://example.com/news/some-article-2026-09-20-slug") == date(2026, 9, 20)

    def test_no_date_shape_in_path_returns_none(self) -> None:
        assert date_from_url("https://www.army-technology.com/news/anduril-lattice-us-army-uas/") is None

    def test_bare_year_alone_is_not_a_date(self) -> None:
        # A bare /YYYY (no month) never matches -- date_from_url requires at least year+month.
        assert date_from_url("https://defense-update.com/2026") is None

    def test_long_article_id_is_never_mistaken_for_a_date(self) -> None:
        # A 19-digit LinkedIn activity id must never be parsed as a date -- it isn't in any of
        # the recognised path/dashed/compact date shapes.
        assert date_from_url(
            "https://www.linkedin.com/posts/elbitsystems_protect-disrupt-collect-built-on-the-"
            "activity-7508395473372778496-RSHr"
        ) is None

    def test_year_outside_sane_bound_is_rejected(self) -> None:
        assert date_from_url("https://example.com/1999/02/17/old-slug/") is None
        assert date_from_url("https://example.com/2099/02/17/future-slug/") is None

    def test_impossible_month_day_is_rejected(self) -> None:
        assert date_from_url("https://example.com/2026/13/40/bad-date-slug/") is None

    def test_empty_or_missing_url_returns_none(self) -> None:
        assert date_from_url("") is None
        assert date_from_url(None) is None  # type: ignore[arg-type]

    def test_sanity_bound_is_relative_to_now(self) -> None:
        # `now` fixes "current year" for the max-year bound: 2031 is sane relative to 2030 but not
        # relative to 2026.
        assert date_from_url("https://example.com/2031/02/17/slug/", now=datetime(2030, 1, 1)) == date(
            2031, 2, 17
        )
        assert date_from_url("https://example.com/2031/02/17/slug/", now=datetime(2026, 1, 1)) is None


class TestIsProbableArticleUrl:
    """Kept: real article URLs with a genuine slug. Rejected: index/category/tag/pagination
    listing pages that the five *_search sources surfaced alongside real articles."""

    def test_army_technology_real_article_is_kept(self) -> None:
        assert is_probable_article_url("https://www.army-technology.com/news/anduril-lattice-us-army-uas/")

    def test_defense_update_compact_date_article_is_kept(self) -> None:
        assert is_probable_article_url("https://defense-update.com/20260220_cuas-report.html")

    def test_linkedin_post_with_long_activity_id_is_kept(self) -> None:
        assert is_probable_article_url(
            "https://www.linkedin.com/posts/elbitsystems_protect-disrupt-collect-built-on-the-"
            "activity-7508395473372778496-RSHr"
        )

    def test_army_technology_news_root_is_rejected(self) -> None:
        assert not is_probable_article_url("https://www.army-technology.com/news/")

    def test_naval_technology_bare_domain_root_is_rejected(self) -> None:
        assert not is_probable_article_url("https://www.naval-technology.com/")

    def test_defense_update_bare_year_root_is_rejected(self) -> None:
        assert not is_probable_article_url("https://defense-update.com/2026")

    def test_defense_update_year_month_archive_root_is_rejected(self) -> None:
        assert not is_probable_article_url("https://defense-update.com/2026/09")

    def test_latest_news_root_is_rejected(self) -> None:
        assert not is_probable_article_url("https://www.airforce-technology.com/latest-news/")

    def test_category_and_tag_listings_are_rejected(self) -> None:
        assert not is_probable_article_url("https://example.com/category/air-defense/")
        assert not is_probable_article_url("https://example.com/tag/counter-uas/")

    def test_pagination_is_rejected(self) -> None:
        assert not is_probable_article_url("https://www.army-technology.com/news/page/2/")

    def test_empty_or_missing_url_is_rejected(self) -> None:
        assert not is_probable_article_url("")
        assert not is_probable_article_url(None)  # type: ignore[arg-type]

    def test_short_non_slug_last_segment_is_rejected(self) -> None:
        # A single short word is not a real article slug and has no id/date to redeem it.
        assert not is_probable_article_url("https://example.com/news/c")
