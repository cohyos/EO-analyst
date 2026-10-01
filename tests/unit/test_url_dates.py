"""Unit tests for `eoa.fetch.url_dates`: URL-embedded dates and the "is this an article, not a
listing page" heuristic (R4, SOL-REVIEW3-2026-09-24 carryover, 2026-09-28).

No DB/network access -- pure string/regex logic against real URLs from the incident report
(docs/qa/content_review/SOL-REVIEW3-2026-09-24.md): the Fortem/2026-World-Cup story that led the
2026-09-28 daily report, the defense-update.com compact-date filename, the L3Harris newsroom
2024/2025 items, and the index/listing pages that were stored as if they were articles.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from eoa.fetch.url_dates import (
    date_from_url,
    is_probable_article_url,
    latest_year_mentioned,
    undated_text_published_at,
)


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


class TestArchiveStyleUrls:
    """R4 (2026-10-01): an August-2013 FBO notice mirrored on ns1.ld.com led the 2026-10-01 tech
    report as current news because nothing recognised the archive-style path."""

    FBO_URL = "https://ns1.ld.com/archive/2013/08-August/30-Aug-2013/FBO-03166032.htm"

    def test_fbo_archive_mirror_url_yields_the_exact_day(self) -> None:
        assert date_from_url(self.FBO_URL) == date(2013, 8, 30)

    def test_year_and_month_folder_alone_gives_first_of_month(self) -> None:
        assert date_from_url("https://ns1.ld.com/archive/2013/08-August/FBO-03166032.htm") == date(2013, 8, 1)

    def test_dd_mon_yyyy_segment_alone(self) -> None:
        assert date_from_url("https://example.org/bulletins/5-Mar-2011/notice.html") == date(2011, 3, 5)

    def test_full_month_name_and_sept_abbreviation(self) -> None:
        assert date_from_url("https://example.org/x/12-September-2012/n.htm") == date(2012, 9, 12)
        assert date_from_url("https://example.org/x/12-Sept-2012/n.htm") == date(2012, 9, 12)

    def test_non_month_word_is_not_a_date(self) -> None:
        assert date_from_url("https://example.org/x/12-abc-2020/n.htm") is None
        assert date_from_url("https://example.org/x/12-augustine-2020/n.htm") is None

    def test_dd_mon_yyyy_glued_to_other_word_chars_is_not_a_date(self) -> None:
        assert date_from_url("https://example.org/x/v30-Aug-2013/n.htm") is None
        assert date_from_url("https://example.org/x/30-Aug-20134/n.htm") is None

    def test_dd_mon_yyyy_inside_a_story_slug_is_not_a_publication_date(self) -> None:
        # A story ABOUT a past date (dvidshub "...-11-sep-2001") was published long after it.
        assert (
            date_from_url("https://www.dvidshub.net/news/574166/mi-reservist-saves-new-yorkers-11-sep-2001") is None
        )

    def test_impossible_day_in_dd_mon_yyyy_is_rejected(self) -> None:
        assert date_from_url("https://example.org/x/31-Feb-2013/n.htm") is None

    def test_future_dd_mon_yyyy_beyond_sanity_bound_is_rejected(self) -> None:
        assert date_from_url("https://example.org/x/30-Aug-2099/n.htm") is None

    def test_existing_shapes_keep_precedence_when_no_named_month(self) -> None:
        assert date_from_url("https://example.com/2026/02/17/some-article-slug/") == date(2026, 2, 17)


class TestLatestYearMentioned:
    def test_picks_the_latest_year(self) -> None:
        assert latest_year_mentioned("Contract awarded in 2008, upgraded in 2012, retired 2010.") == 2012

    def test_none_when_no_year(self) -> None:
        assert latest_year_mentioned("No dates in this text at all.") is None
        assert latest_year_mentioned("") is None
        assert latest_year_mentioned(None) is None

    def test_years_outside_1990_to_current_plus_one_are_not_years(self) -> None:
        now = datetime(2026, 10, 1)
        assert latest_year_mentioned("Built in 1985.", now=now) is None
        assert latest_year_mentioned("Fiscal 2027 budget and 2008 award.", now=now) == 2027

    def test_far_future_target_year_makes_text_unjudgeable(self) -> None:
        # "by 2030" is a forward-looking target: the old 2008 mention must not age the text.
        assert latest_year_mentioned("Program since 2008, goal by 2030.", now=datetime(2026, 10, 1)) is None

    def test_model_numbers_prices_and_decimals_are_not_years(self) -> None:
        now = datetime(2026, 10, 1)
        assert latest_year_mentioned("The F-2020 and AN/ALQ-2024 pods", now=now) is None
        assert latest_year_mentioned("costs $2020 or 1.2024 units or 2019.5 kg", now=now) is None
        assert latest_year_mentioned("a 2018% rise", now=now) is None

    def test_year_in_parentheses_and_with_punctuation_counts(self) -> None:
        assert latest_year_mentioned("(2008) and 2009.", now=datetime(2026, 10, 1)) == 2009


class TestUndatedTextPublishedAt:
    NOW = datetime(2026, 10, 1, tzinfo=UTC)

    def test_did_like_text_with_only_2008_is_dated_jan_1_2008(self) -> None:
        text = "The ATP-SE Litening pod won the 2008 USAF competition for targeting pod orders."
        assert undated_text_published_at("ATP-SE, Litening strikes as USAF splits orders", text, now=self.NOW) == (
            datetime(2008, 1, 1, tzinfo=UTC)
        )

    def test_text_mentioning_2026_is_left_undated(self) -> None:
        assert undated_text_published_at("Pod orders", "Awarded in 2008 and extended in 2026.", now=self.NOW) is None

    def test_previous_year_is_too_recent_to_age(self) -> None:
        # current_year - 2 is the cut-off: 2025 is left alone, 2024 is aged.
        assert undated_text_published_at("t", "Mentions 2025 only.", now=self.NOW) is None
        assert undated_text_published_at("t", "Mentions 2024 only.", now=self.NOW) == datetime(
            2024, 1, 1, tzinfo=UTC
        )

    def test_year_only_in_title_counts(self) -> None:
        assert undated_text_published_at("FBO notice 2013", "No year here.", now=self.NOW) == datetime(
            2013, 1, 1, tzinfo=UTC
        )

    def test_no_year_leaves_undated(self) -> None:
        assert undated_text_published_at("t", "nothing", now=self.NOW) is None
        assert undated_text_published_at(None, None, now=self.NOW) is None
