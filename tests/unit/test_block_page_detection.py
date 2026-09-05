"""Unit tests for eoa.fetch.sanitize.detect_block_page (Q4-1, docs/qa/findings_Q4_r1.md).

No DB/network access.
"""

from __future__ import annotations

from eoa.fetch.sanitize import BLOCKED_ITEM_TITLE_HE, detect_block_page


class TestDetectBlockPage:
    def test_cloudflare_security_service_phrase_detected_regardless_of_status(self) -> None:
        """Real Safran repro: a 403 whose body is the Cloudflare challenge page."""
        text = (
            "This website is using a security service to protect itself from online attacks. "
            "The action you just performed triggered the security solution."
        )
        assert detect_block_page(html=f"<html><body>{text}</body></html>", text=text, status=403) is True

    def test_cloudflare_js_challenge_returns_200_still_detected(self) -> None:
        """A JS interstitial commonly answers 200, not 403/429/503 -- phrase match alone must catch it."""
        text = "Just a moment... Please wait while we check your browser."
        assert detect_block_page(html="<html></html>", text=text, status=200) is True

    def test_war_gov_access_denied_detected(self) -> None:
        """Real repro: war.gov mod_security block page."""
        text = 'Access Denied\n\nYou don\'t have permission to access "..." on this server.'
        assert detect_block_page(html=None, text=text, status=403) is True

    def test_tiny_body_with_block_status_detected(self) -> None:
        assert detect_block_page(html="", text="Forbidden", status=403) is True

    def test_real_article_not_flagged(self) -> None:
        text = (
            "Israel's defense ministry announced a new procurement agreement today, covering "
            "several electro-optical sensor systems for border surveillance applications over "
            "the next three years, according to officials familiar with the matter." * 3
        )
        assert detect_block_page(html=f"<html><body>{text}</body></html>", text=text, status=200) is False

    def test_real_403_article_with_substantial_text_not_flagged(self) -> None:
        """A genuine 403 page that still carries a lot of real text (not a tiny challenge body)
        and no known phrase should not be treated as a block page."""
        text = "Some unusual access-control page with lots of unrelated legitimate text. " * 20
        assert detect_block_page(html="", text=text, status=403) is False

    def test_none_inputs_do_not_crash(self) -> None:
        assert detect_block_page(html=None, text=None, status=None) is False


def test_blocked_item_title_is_hebrew_neutral_note() -> None:
    assert BLOCKED_ITEM_TITLE_HE == "הפריט אינו נגיש: האתר חוסם גישה אוטומטית"
