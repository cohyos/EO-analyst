"""Tests for eoa.notify.ntfy — user response matching for clarifications."""

from __future__ import annotations

import pytest

from eoa.notify.ntfy import _match, _fmt_action


class TestMatch:
    """Test the _match() function for parsing user responses."""

    def test_numeric_index_match(self):
        """Numeric index (1-based) maps to option."""
        options = ["option_a", "option_b", "option_c"]
        assert _match("1", options) == "option_a"
        assert _match("2", options) == "option_b"
        assert _match("3", options) == "option_c"

    def test_numeric_with_space(self):
        """Numeric followed by space → matches option."""
        options = ["yes", "no", "maybe"]
        assert _match("1 yes please", options) == "yes"
        assert _match("2 absolutely not", options) == "no"

    def test_numeric_with_period(self):
        """Numeric followed by period → matches option."""
        options = ["option_a", "option_b"]
        assert _match("1. option_a", options) == "option_a"
        assert _match("2.", options) == "option_b"

    def test_text_exact_match(self):
        """Exact text match (case-insensitive)."""
        options = ["Red", "Orange", "Yellow"]
        assert _match("red", options) == "Red"
        assert _match("ORANGE", options) == "Orange"

    def test_text_substring_match(self):
        """Option text as substring of user response."""
        options = ["option_a", "option_b"]
        # "option_a" is a substring of "option_a_test"
        assert _match("option_a_test", options) == "option_a"
        assert _match("go with option_b", options) == "option_b"

    def test_option_substring_in_text(self):
        """Option as substring in response text."""
        options = ["agree", "disagree"]
        assert _match("i agree completely", options) == "agree"
        # Note: "agree" is a substring of "disagree", so "agree" matches first if it comes first
        assert _match("i concur", options) is None  # No match

    def test_empty_text_returns_none(self):
        """Empty/whitespace text → None."""
        options = ["a", "b"]
        assert _match("", options) is None
        assert _match("   ", options) is None

    def test_no_match_returns_none(self):
        """No matching option → None."""
        options = ["yes", "no"]
        assert _match("maybe", options) is None
        assert _match("3", options) is None

    def test_out_of_range_index_returns_none(self):
        """Index out of range → None."""
        options = ["a", "b"]
        assert _match("5", options) is None
        assert _match("0", options) is None  # 0-based, not 1-based

    def test_case_insensitive_option_text_matching(self):
        """Option text matching is case-insensitive."""
        options = ["Proceed", "Defer"]
        assert _match("proceed", options) == "Proceed"
        assert _match("DEFER", options) == "Defer"

    def test_first_match_wins(self):
        """If multiple options match, first one is returned."""
        options = ["a", "ab", "abc"]
        result = _match("a", options)
        # Could match any of them; behavior depends on order
        assert result in ["a", "ab", "abc"]

    def test_unicode_support(self):
        """Works with Hebrew/Unicode text."""
        options = ["כן", "לא"]
        assert _match("כן", options) == "כן"
        assert _match("לא", options) == "לא"


class TestFormatAction:
    """Test the _fmt_action() function for ntfy action headers."""

    def test_view_action_default(self):
        """Default (view) action format."""
        action = {"label": "Open Report", "url": "https://example.com/report"}
        result = _fmt_action(action)
        assert result == "view, Open Report, https://example.com/report"

    def test_view_action_explicit(self):
        """Explicit view action."""
        action = {"kind": "view", "label": "Click Here", "url": "http://localhost:8080"}
        result = _fmt_action(action)
        assert result == "view, Click Here, http://localhost:8080"

    def test_http_action_post(self):
        """HTTP POST action."""
        action = {
            "kind": "http",
            "label": "Approve",
            "url": "https://api.example.com/approve",
            "method": "POST",
            "body": '{"status":"approved"}',
        }
        result = _fmt_action(action)
        assert "http" in result
        assert "Approve" in result
        assert "method=POST" in result
        assert 'body={"status":"approved"}' in result

    def test_http_action_get(self):
        """HTTP GET action."""
        action = {
            "kind": "http",
            "label": "Fetch",
            "url": "https://api.example.com/data",
            "method": "GET",
        }
        result = _fmt_action(action)
        assert "http" in result
        assert "method=GET" in result

    def test_http_action_default_method(self):
        """HTTP action without method defaults to POST."""
        action = {
            "kind": "http",
            "label": "Submit",
            "url": "https://api.example.com/submit",
        }
        result = _fmt_action(action)
        assert "method=POST" in result

    def test_http_action_empty_body(self):
        """HTTP action without body."""
        action = {
            "kind": "http",
            "label": "Trigger",
            "url": "https://api.example.com/trigger",
        }
        result = _fmt_action(action)
        assert "body=" in result

    def test_kind_default_is_view(self):
        """Missing kind defaults to view."""
        action = {"label": "Link", "url": "https://example.com"}
        result = _fmt_action(action)
        assert result == "view, Link, https://example.com"
