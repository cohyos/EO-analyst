"""Unit tests for the title fallback chain (eoa.fetch.sanitize.choose_title).

Tests each rung of the chain independently, with no DB/network access.
"""

from __future__ import annotations

from eoa.fetch.sanitize import choose_title


class TestChooseTitleFallbackChain:
    """Test rung by rung of the title fallback chain."""

    def test_html_title_takes_precedence_over_clean_title(self) -> None:
        """Rung 2: a raw <title> tag outranks clean_title (Q4-9: trafilatura's own title guess
        is exactly what produced the Globes lead-paragraph and Leonardo "Financial highlights"
        bugs, so a structured HTML tag is preferred whenever the page actually has one)."""
        title = choose_title(
            clean_title="Clean Article Title",
            fallback_title="RSS Title",
            html="<title>HTML Title</title>",
            clean_text="First line of body",
            url="https://example.com/articles/some-slug",
        )
        assert title == "HTML Title"

    def test_clean_title_used_when_no_html_tags_present(self) -> None:
        """Rung 4: clean_title is still used as a fallback when the page has no og:title,
        <title>, or <h1> at all."""
        title = choose_title(
            clean_title="Clean Article Title",
            fallback_title="RSS Title",
            html="<html><body>No structured title here</body></html>",
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

    def test_chain_order_verification_og_title_preferred_over_clean_and_rss(self) -> None:
        """Verify og:title is preferred even when clean_title/RSS/other HTML tags exist."""
        title = choose_title(
            clean_title="Clean Title",
            fallback_title="RSS Title",
            html='<meta property="og:title" content="OG Title" /><title>HTML Title</title>',
            clean_text="First line of body",
            url="https://example.com/test",
        )
        # og:title should win
        assert title == "OG Title"

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

    def test_q4_9_globes_lead_paragraph_rejected_in_favor_of_og_title(self) -> None:
        """Q4-9 real bug: trafilatura's clean_title picked the article's lead paragraph as the
        "title" (item 67 et al.); the page's real og:title/<title> were correct the whole time."""
        html = (
            '<html><head><meta property="og:title" content="German defense exports to Israel '
            'soar" /><title>German defense exports to Israel soar - Globes</title></head>'
            "<body>...</body></html>"
        )
        lead_paragraph = (
            "Germany approved defense exports to Israel worth nearly €800 million (about "
            '$930 million) in the first half of 2026, "Der Spiegel" reported today, citing '
            "government data obtained under freedom of information requests."
        )
        title = choose_title(
            clean_title=lead_paragraph,
            fallback_title=None,
            html=html,
            clean_text=lead_paragraph,
            url="https://en.globes.co.il/en/article-german-defense-exports-to-israel-soar-1001554312",
        )
        assert title == "German defense exports to Israel soar"

    def test_q4_9_leonardo_sidebar_widget_rejected_as_generic(self) -> None:
        """Q4-9 real bug: 12 different Leonardo press-release items all got clean_title
        "Financial highlights" (a sidebar widget heading reused on every page); the real
        <title>/og:title carried the actual per-article headline."""
        html = (
            '<html><head><title>LEONARDO IS EXPANDING IN THE US WITH THE ACQUISITION OF RAFT'
            "</title></head><body>...</body></html>"
        )
        title = choose_title(
            clean_title="Financial highlights",
            fallback_title=None,
            html=html,
            clean_text="Leonardo today announced the acquisition of RAFT...",
            url="https://www.leonardo.com/en/press-release-detail/-/detail/28-07-2026-leonardo-is-expanding-in-the-us-with-the-acquisition-of-raft",
        )
        assert title == "LEONARDO IS EXPANDING IN THE US WITH THE ACQUISITION OF RAFT"

    def test_generic_clean_title_falls_through_to_rss(self) -> None:
        """A generic clean_title (no HTML tags at all) is rejected, falling through to RSS."""
        title = choose_title(
            clean_title="Financial highlights",
            fallback_title="Leonardo acquires RAFT to expand US cyber footprint",
            html="<html><body>no structured tags</body></html>",
            clean_text="Leonardo today announced the acquisition of RAFT...",
            url="https://www.leonardo.com/en/press-release-detail/-/detail/28-07-2026",
        )
        assert title == "Leonardo acquires RAFT to expand US cyber footprint"

    def test_oversized_og_title_rejected(self) -> None:
        """An og:title over 200 chars (a lead paragraph mistakenly used as og:title) is rejected."""
        long_lead = "A" * 210
        title = choose_title(
            clean_title=None,
            fallback_title="Short RSS Title",
            html=f'<meta property="og:title" content="{long_lead}" />',
            clean_text="body",
            url="https://example.com/test",
        )
        assert title == "Short RSS Title"

    def test_h1_used_when_no_meta_or_title_tag(self) -> None:
        """Rung 3: <h1> is used when og:title/<title> are both absent."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<html><body><h1>Headline From H1</h1><p>body</p></body></html>",
            clean_text="body text",
            url="https://example.com/test",
        )
        assert title == "Headline From H1"

    def test_title_tag_site_suffix_stripped(self) -> None:
        """<title> tag's trailing " - Site Name" suffix is stripped."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<title>Real Headline Text - Globes</title>",
            clean_text="body",
            url="https://example.com/test",
        )
        assert title == "Real Headline Text"

    def test_title_tag_pipe_suffix_stripped(self) -> None:
        """<title> tag's trailing " | Site Name" suffix is stripped."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<title>Real Headline Text | Defense News</title>",
            clean_text="body",
            url="https://example.com/test",
        )
        assert title == "Real Headline Text"

    def test_title_tag_separator_in_long_headline_not_stripped(self) -> None:
        """A " - " that's part of a long headline (not a short site-name suffix) is preserved."""
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<title>Company A - Company B sign landmark defense cooperation agreement</title>",
            clean_text="body",
            url="https://example.com/test",
        )
        assert title == "Company A - Company B sign landmark defense cooperation agreement"

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


class TestQ5_13HtmlEntityUnescape:
    """Q5-13 (docs/qa/findings_Q5_r2.md): rungs 1-3 pull a candidate straight out of raw HTML via
    regex, never through a real HTML parser, so a numeric/named entity in the source markup
    reached `items.title` completely undecoded (item 112: "Israel&#39;s Aero Sentinel")."""

    def test_numeric_entity_in_title_tag_is_unescaped(self) -> None:
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<title>Israel&#39;s Aero Sentinel</title>",
            clean_text="body",
            url="https://example.com/test",
        )
        assert title == "Israel's Aero Sentinel"

    def test_named_entity_in_og_title_is_unescaped(self) -> None:
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html='<meta property="og:title" content="Aegis &amp; Iron Dome">',
            clean_text="body",
            url="https://example.com/test",
        )
        assert title == "Aegis & Iron Dome"

    def test_hex_entity_and_nbsp_in_h1_are_unescaped_and_collapsed(self) -> None:
        title = choose_title(
            clean_title=None,
            fallback_title=None,
            html="<h1>Israel&#x27;s&nbsp;Aero Sentinel</h1>",
            clean_text="body",
            url="https://example.com/test",
        )
        assert title == "Israel's Aero Sentinel"

    def test_entity_in_fallback_rss_title_is_also_unescaped(self) -> None:
        """Every rung goes through the same `_normalize_candidate`, not just the HTML-derived
        ones -- an RSS feed's own <title> can carry the same raw entities."""
        title = choose_title(
            clean_title=None,
            fallback_title="Rafael &amp; Elbit sign cooperation deal",
            html="<html><body>no structured title</body></html>",
            clean_text="",
            url="https://example.com/test",
        )
        assert title == "Rafael & Elbit sign cooperation deal"
