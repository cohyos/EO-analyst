"""Unit tests for the title fallback chain (eoa.fetch.sanitize.choose_title).

Tests each rung of the chain independently, with no DB/network access.
"""

from __future__ import annotations

from eoa.fetch.sanitize import choose_title


class TestChooseTitleFallbackChain:
    """Test rung by rung of the title fallback chain."""

    def test_clean_title_takes_precedence(self) -> None:
        """Rung 1: Sanitized page title has highest priority."""
        title = choose_title(
            clean_title="Clean Article Title",
            fallback_title="RSS Title",
            html="<title>HTML Title</title>",
            clean_text="First line of body",
            url="https://example.com/articles/some-slug",
        )
        assert title == "Clean Article Title"

    def test_fallback_title_when_clean_empty_and_no_html(self) -> None:
        """Rung 3: RSS entry title used when clean_title and HTML extraction both fail."""
        title = choose_title(
            clean_title=None,
            fallback_title="RSS Entry Title",
            html="<html><body>No title tags</body></html>",
            clean_text="First line of body",
            url="https://example.com/articles/some-slug",
        )
        assert title == "RSS Entry Title"

    def test_html_title_preferred_over_rss_when_available(self) -> None:
        """Rung 2: HTML title used when clean_title fails but HTML has title."""
        title = choose_title(
            clean_title=None,
            fallback_title="Generic RSS Title",
            html="<title>Specific Article Title</title>",
            clean_text="First line of body",
            url="https://example.com/articles/some-slug",
        )
        # Article-specific HTML title is preferred over generic RSS title
        assert title == "Specific Article Title"

    def test_html_title_tag_when_fallback_empty(self) -> None:
        """Rung 3: HTML <title> tag used when earlier rungs fail."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<title>HTML Page Title</title>",
            clean_text="First line of body",
            url="https://example.com/articles/some-slug",
        )
        assert title == "HTML Page Title"

    def test_html_og_title_takes_precedence_over_title(self) -> None:
        """Rung 3: og:title meta tag has priority over <title> tag."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html='<meta property="og:title" content="Open Graph Title" /><title>HTML Title</title>',
            clean_text="First line of body",
            url="https://example.com/articles/some-slug",
        )
        assert title == "Open Graph Title"

    def test_html_og_title_with_single_quotes(self) -> None:
        """Rung 3: og:title extraction handles single quotes."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<meta property='og:title' content='Title With Quotes' />",
            clean_text="Body text",
            url="https://example.com/test",
        )
        assert title == "Title With Quotes"

    def test_html_title_tag_with_whitespace(self) -> None:
        """Rung 3: HTML title extraction strips surrounding whitespace."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<title>  \n  Title With Whitespace  \n  </title>",
            clean_text="Body",
            url="https://example.com/test",
        )
        assert title == "Title With Whitespace"

    def test_first_line_of_clean_text_when_html_empty(self) -> None:
        """Rung 4: First non-empty line of clean_text used when HTML extraction fails."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<html><body>No structured title here</body></html>",
            clean_text="First line is title candidate\nSecond line",
            url="https://example.com/articles/some-slug",
        )
        assert title == "First line is title candidate"

    def test_first_line_truncated_at_max_chars(self) -> None:
        """Rung 4: First line of text is bounded at 120 characters."""
        long_line = "A" * 150 + " more text"
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<html></html>",
            clean_text=long_line + "\nSecond line",
            url="https://example.com/test",
        )
        assert title == "A" * 120
        assert len(title) == 120

    def test_first_line_skips_blank_lines(self) -> None:
        """Rung 4: First non-empty line is selected (blank lines skipped)."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<html></html>",
            clean_text="\n\n\nActual first content line",
            url="https://example.com/test",
        )
        assert title == "Actual first content line"

    def test_url_path_as_fallback(self) -> None:
        """Rung 5: Last URL path segment used as absolute fallback."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<html></html>",
            clean_text="",
            url="https://example.com/articles/ir-targeting-pod",
        )
        assert title == "ir-targeting-pod"

    def test_url_path_ignores_trailing_slash(self) -> None:
        """Rung 5: URL path extraction handles trailing slashes."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<html></html>",
            clean_text="",
            url="https://example.com/articles/some-slug/",
        )
        assert title == "some-slug"

    def test_whitespace_collapsing_internal(self) -> None:
        """All rungs: Internal whitespace is collapsed to single spaces."""
        title = choose_title(
            clean_title="Title  with  \n  excessive   whitespace",
            fallback_title=None,
            html="",
            clean_text="",
            url="https://example.com/test",
        )
        assert title == "Title with excessive whitespace"

    def test_whitespace_stripping_surrounding(self) -> None:
        """All rungs: Surrounding whitespace is stripped."""
        title = choose_title(
            clean_title="  \n  Title Text  \n  ",
            fallback_title=None,
            html="",
            clean_text="",
            url="https://example.com/test",
        )
        assert title == "Title Text"

    def test_empty_string_never_returned(self) -> None:
        """Empty string is never returned; URL path is absolute fallback."""
        title = choose_title(
            clean_title="",
            fallback_title="",
            html="",
            clean_text="",
            url="https://example.com/",
        )
        # URL has no path segment, so "Untitled" is fallback
        assert title == "Untitled"

    def test_none_values_gracefully_skipped(self) -> None:
        """None values are skipped without error."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="",
            clean_text=None,
            url="https://example.com/my-article",
        )
        assert title == "my-article"

    def test_real_world_globes_english_feed(self) -> None:
        """Real example: Globes English feed with og:title and RSS fallback."""
        html = """
        <html>
        <head>
            <meta property="og:title" content="Defense Ministry to Increase Procurement" />
            <title>Globes - Israeli Business News</title>
        </head>
        <body>
            <article>The Ministry announced new procurement plans...</article>
        </body>
        </html>
        """
        clean_text = "The Ministry announced new procurement plans for defense systems. This represents a significant investment."

        title = choose_title(
            clean_title=None,  # trafilatura found no title
            fallback_title="Breaking News from Globes",
            html=html,
            clean_text=clean_text,
            url="https://www.globes.co.il/en/article-defense-ministry-procurement",
        )
        # og:title is found and used
        assert title == "Defense Ministry to Increase Procurement"

    def test_real_world_rss_only(self) -> None:
        """Real example: RSS entry with title, no HTML title extraction needed."""
        title = choose_title(
            clean_title=None,
            fallback_title="New IR Targeting Pod Enters Service",
            html="<html><body>Content with no title tags</body></html>",
            clean_text="The military reported a new targeting pod...",
            url="https://example.com/news/ir-pod-2026",
        )
        assert title == "New IR Targeting Pod Enters Service"

    def test_real_world_html_source_landing_page(self) -> None:
        """Real example: HTML source landing page, no RSS metadata available."""
        html = "<html><head><title>Latest Defense News</title></head><body>News listing...</body></html>"
        clean_text = (
            "Breaking: New Counter-UAS System Announced\n"
            "The company unveiled its latest detection technology...\n"
            "Available in Q4 2026."
        )

        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html=html,
            clean_text=clean_text,
            url="https://defensenews.com/articles/breaking-counter-uas",
        )
        # HTML title found via <title> tag
        assert title == "Latest Defense News"

    def test_fallback_to_text_extraction_when_html_title_fails(self) -> None:
        """When HTML has malformed/missing title, fall through to text first line."""
        html = "<html><body>Regular page content, no title tags</body></html>"
        clean_text = "Parsed article headline goes here\nBody paragraph starts..."

        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html=html,
            clean_text=clean_text,
            url="https://example.com/article",
        )
        assert title == "Parsed article headline goes here"

    def test_chain_order_verification_clean_preferred_over_rss(self) -> None:
        """Verify clean_title is preferred even when other good options exist."""
        title = choose_title(
            clean_title="Clean Title",
            fallback_title="RSS Title",
            html='<meta property="og:title" content="OG Title" /><title>HTML Title</title>',
            clean_text="First line of body",
            url="https://example.com/test",
        )
        # clean_title should win
        assert title == "Clean Title"

    def test_chain_order_verification_html_preferred_over_rss(self) -> None:
        """Verify HTML title is preferred over RSS title when available."""
        title = choose_title(
            clean_title=None,
            fallback_title="Generic RSS Title",
            html="<title>Article-Specific HTML Title</title>",
            clean_text="Body text",
            url="https://example.com/test",
        )
        # Article-specific HTML title should win over generic RSS title
        assert title == "Article-Specific HTML Title"

    def test_chain_order_verification_html_preferred_over_text(self) -> None:
        """Verify HTML title is preferred over first line of text."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<title>HTML Title</title>",
            clean_text="First line text candidate\nSecond line",
            url="https://example.com/test",
        )
        # HTML should win
        assert title == "HTML Title"

    def test_chain_order_verification_text_preferred_over_url(self) -> None:
        """Verify first line of text is preferred over URL path."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<html></html>",
            clean_text="Real article headline content",
            url="https://example.com/articles/url-slug",
        )
        # Text should win
        assert title == "Real article headline content"

    def test_empty_html_string_doesnt_crash(self) -> None:
        """Empty or malformed HTML doesn't cause exceptions."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="",
            clean_text="",
            url="https://example.com/test",
        )
        assert title == "test"

    def test_malformed_og_title_ignored(self) -> None:
        """Malformed og:title tag is safely ignored, falls through to title."""
        html = '<meta property="og:title" content= /><title>Fallback HTML Title</title>'
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html=html,
            clean_text="",
            url="https://example.com/test",
        )
        # Malformed og:title skipped, regular <title> found
        assert title == "Fallback HTML Title"

    def test_unicode_preservation_hebrew(self) -> None:
        """Hebrew characters preserved through all stages."""
        title = choose_title(
            clean_title="כותרת בעברית",
            fallback_title=None,
            html="",
            clean_text="",
            url="https://example.com/test",
        )
        assert title == "כותרת בעברית"

    def test_unicode_preservation_mixed_text(self) -> None:
        """Mixed Hebrew/English text preserved."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<title>Defense Update: התקנה חדשה</title>",
            clean_text="",
            url="https://example.com/test",
        )
        assert title == "Defense Update: התקנה חדשה"

    def test_special_characters_preserved(self) -> None:
        """Special characters (quotes, punctuation) are preserved."""
        title = choose_title(
            clean_title='Company\'s "New System" — A Breakthrough',
            fallback_title=None,
            html="",
            clean_text="",
            url="https://example.com/test",
        )
        assert title == 'Company\'s "New System" — A Breakthrough'
