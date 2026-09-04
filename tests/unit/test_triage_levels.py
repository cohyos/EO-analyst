"""Tests for eoa.pipeline.triage — importance scoring and categorization."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from eoa.pipeline.triage import level_for, _watchlist_hits
from eoa.config import settings


class TestLevelFor:
    """Test the level_for() score→level threshold function."""

    def test_score_8_and_above_is_red(self):
        """Score >= 8 → 'red'."""
        assert level_for(8) == "red"
        assert level_for(9) == "red"
        assert level_for(10) == "red"

    def test_score_6_7_is_orange(self):
        """Score 6-7 → 'orange'."""
        assert level_for(6) == "orange"
        assert level_for(7) == "orange"

    def test_score_4_5_is_yellow(self):
        """Score 4-5 → 'yellow'."""
        assert level_for(4) == "yellow"
        assert level_for(5) == "yellow"

    def test_score_below_4_is_archive(self):
        """Score < 4 → 'archive'."""
        assert level_for(3) == "archive"
        assert level_for(2) == "archive"
        assert level_for(0) == "archive"

    def test_boundary_cases(self):
        """Exact boundary values map correctly."""
        s = settings()
        red_threshold = s.triage.levels["red"]
        orange_threshold = s.triage.levels["orange"]
        yellow_threshold = s.triage.levels["yellow"]

        assert level_for(red_threshold) == "red"
        assert level_for(red_threshold - 1) == "orange"
        assert level_for(orange_threshold) == "orange"
        assert level_for(orange_threshold - 1) == "yellow"
        assert level_for(yellow_threshold) == "yellow"
        assert level_for(yellow_threshold - 1) == "archive"


class TestWatchlistHits:
    """Test the _watchlist_hits() function for entity matching."""

    def test_empty_entities_returns_empty_string(self):
        """No entities → 'אין'."""
        result = _watchlist_hits([])
        assert result == "אין"

    def test_none_entities_returns_empty_string(self):
        """None entities → 'אין'."""
        result = _watchlist_hits(None)
        assert result == "אין"

    def test_exact_company_match(self, monkeypatch):
        """Exact match against watchlist company name."""
        mock_settings = MagicMock()
        mock_settings.watchlist = {
            "companies": [
                {"name": "Elbit Systems", "aliases": []},
            ],
            "programs": [],
        }
        with patch("eoa.pipeline.triage.settings", return_value=mock_settings):
            result = _watchlist_hits(["Elbit Systems", "Unknown Co"])
            assert "Elbit Systems" in result
            assert "Unknown Co" not in result

    def test_alias_match(self, monkeypatch):
        """Match against company aliases."""
        mock_settings = MagicMock()
        mock_settings.watchlist = {
            "companies": [
                {
                    "name": "Elbit Systems",
                    "aliases": ["ESL", "Elbit"],
                },
            ],
            "programs": [],
        }
        with patch("eoa.pipeline.triage.settings", return_value=mock_settings):
            result = _watchlist_hits(["ESL", "Some Corp"])
            assert "ESL" in result
            assert "Elbit Systems" not in result  # Returns the alias used, not the name

    def test_case_insensitive_match(self, monkeypatch):
        """Matching is case-insensitive."""
        mock_settings = MagicMock()
        mock_settings.watchlist = {
            "companies": [
                {"name": "Elbit Systems", "aliases": []},
            ],
            "programs": [],
        }
        with patch("eoa.pipeline.triage.settings", return_value=mock_settings):
            result = _watchlist_hits(["ELBIT SYSTEMS", "elbit systems"])
            assert "ELBIT SYSTEMS" in result or "elbit systems" in result

    def test_substring_match(self, monkeypatch):
        """Match when watchlist name is substring of entity."""
        mock_settings = MagicMock()
        mock_settings.watchlist = {
            "companies": [
                {"name": "Elbit", "aliases": []},
            ],
            "programs": [],
        }
        with patch("eoa.pipeline.triage.settings", return_value=mock_settings):
            result = _watchlist_hits(["Elbit Systems Inc"])
            assert "Elbit Systems Inc" in result

    def test_substring_match_watchlist_in_entity(self, monkeypatch):
        """Match when watchlist name is substring of entity."""
        mock_settings = MagicMock()
        mock_settings.watchlist = {
            "companies": [
                {"name": "Elbit", "aliases": []},
            ],
            "programs": [],
        }
        with patch("eoa.pipeline.triage.settings", return_value=mock_settings):
            # "Elbit" (lowercased "elbit") is in "Elbit Systems Inc" (lowercased "elbit systems inc")
            result = _watchlist_hits(["Elbit Systems Inc"])
            assert "Elbit Systems Inc" in result

    def test_program_match(self, monkeypatch):
        """Match against watchlist programs."""
        mock_settings = MagicMock()
        mock_settings.watchlist = {
            "companies": [],
            "programs": [
                {"name": "David's Sling", "aliases": ["Tactical High Energy Laser"]},
            ],
        }
        with patch("eoa.pipeline.triage.settings", return_value=mock_settings):
            result = _watchlist_hits(["David's Sling"])
            assert "David's Sling" in result

    def test_deduplicate_hits(self, monkeypatch):
        """Multiple entity hits are deduplicated and sorted."""
        mock_settings = MagicMock()
        mock_settings.watchlist = {
            "companies": [
                {"name": "Elbit", "aliases": []},
                {"name": "Rafael", "aliases": []},
            ],
            "programs": [],
        }
        with patch("eoa.pipeline.triage.settings", return_value=mock_settings):
            result = _watchlist_hits(["Elbit", "Rafael", "Elbit"])
            # Should be deduplicated and sorted
            assert result.count("Elbit") == 1
            assert "Rafael" in result

    def test_no_matches_returns_empty_marker(self, monkeypatch):
        """No matches → 'אין'."""
        mock_settings = MagicMock()
        mock_settings.watchlist = {
            "companies": [
                {"name": "Watchlisted Co", "aliases": []},
            ],
            "programs": [],
        }
        with patch("eoa.pipeline.triage.settings", return_value=mock_settings):
            result = _watchlist_hits(["Unknown Corp", "Random Industries"])
            assert result == "אין"

    def test_mixed_match_and_no_match(self, monkeypatch):
        """Some entities match, some don't."""
        mock_settings = MagicMock()
        mock_settings.watchlist = {
            "companies": [
                {"name": "Elbit Systems", "aliases": []},
            ],
            "programs": [],
        }
        with patch("eoa.pipeline.triage.settings", return_value=mock_settings):
            result = _watchlist_hits(["Elbit Systems", "Random Corp", "Another Corp"])
            assert "Elbit Systems" in result
            assert "Random Corp" not in result
            assert "Another Corp" not in result

    def test_hebrew_entity_names(self, monkeypatch):
        """Works with Hebrew entity names."""
        mock_settings = MagicMock()
        mock_settings.watchlist = {
            "companies": [
                {"name": "אלביט", "aliases": []},
            ],
            "programs": [],
        }
        with patch("eoa.pipeline.triage.settings", return_value=mock_settings):
            result = _watchlist_hits(["אלביט"])
            assert "אלביט" in result
