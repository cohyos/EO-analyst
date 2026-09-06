"""Unit tests for `eoa.report.israel_section` (A13: "תעשייה ישראלית" report section).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_israel_section.py -q``
"""

from __future__ import annotations

import sys
import types

import pytest

if "eoa.db" not in sys.modules:
    try:
        import eoa.db  # noqa: F401
    except ImportError:
        fake_db = types.ModuleType("eoa.db")
        fake_db.connection = lambda: None  # type: ignore[attr-defined]
        fake_db.get_pool = lambda: None  # type: ignore[attr-defined]
        sys.modules["eoa.db"] = fake_db

from eoa.report import israel_section as isec


def test_extend_registry_assigns_continuing_n_and_mutates_in_place() -> None:
    citation_items = [{"id": 1, "n": 1}]
    rows = [{"id": 2, "title": "t2", "source_name": "s", "url": "u", "published_at": None}]
    isec._extend_registry(citation_items, rows)
    assert rows[0]["n"] == 2
    assert citation_items[-1]["id"] == 2


class TestCategorize:
    def test_win_event_kind_puts_item_in_wins_bucket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: {"contract_award"})
        item = {"id": 1, "israel_reasons": [], "summary_he": "", "so_what_he": "", "title": ""}
        assert isec._CATEGORY_WINS in isec._categorize(item)

    def test_competitor_reason_puts_item_in_competition_bucket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: set())
        item = {
            "id": 1,
            "israel_reasons": ["competitor_to_israeli_company"],
            "summary_he": "",
            "so_what_he": "",
            "title": "",
        }
        assert isec._categorize(item) == {isec._CATEGORY_COMPETITION}

    def test_export_market_reason_puts_item_in_export_bucket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: set())
        item = {
            "id": 1,
            "israel_reasons": ["export_market_signal"],
            "summary_he": "",
            "so_what_he": "",
            "title": "",
        }
        assert isec._categorize(item) == {isec._CATEGORY_EXPORT}

    def test_threat_keyword_puts_item_in_threats_bucket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: set())
        item = {
            "id": 1,
            "israel_reasons": [],
            "summary_he": "איום רגולטורי חדש",
            "so_what_he": "",
            "title": "",
        }
        assert isec._categorize(item) == {isec._CATEGORY_THREATS}

    def test_item_can_land_in_multiple_categories(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: {"contract_award"})
        item = {
            "id": 1,
            "israel_reasons": ["export_market_signal"],
            "summary_he": "",
            "so_what_he": "",
            "title": "",
        }
        cats = isec._categorize(item)
        assert isec._CATEGORY_WINS in cats
        assert isec._CATEGORY_EXPORT in cats

    def test_uncategorized_item_with_no_company_or_event_is_excluded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Round 3 (2026-09-06, D6 judge finding 2): the old behaviour (any uncategorized-but-
        relevant item defaults into 'competition') let political op-eds whose only Israeli hook is
        a government/military org mention (e.g. 'IDF') into the section -- an item with no Israeli
        company entity and no business event kind is now excluded entirely instead."""
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: set())
        item = {
            "id": 1,
            "israel_reasons": [],
            "summary_he": "",
            "so_what_he": "",
            "title": "",
            "entities_mentioned": ["IDF"],
        }
        assert isec._categorize(item) == set()

    def test_uncategorized_item_with_israeli_company_entity_defaults_to_competition(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: set())
        monkeypatch.setattr("eoa.pipeline.israel_focus.israeli_watchlist_names", lambda: ["Elbit", "Rafael"])
        item = {
            "id": 1,
            "israel_reasons": [],
            "summary_he": "",
            "so_what_he": "",
            "title": "",
            "entities_mentioned": ["Elbit"],
        }
        assert isec._categorize(item) == {isec._CATEGORY_COMPETITION}

    def test_uncategorized_item_with_eligible_event_kind_defaults_to_competition(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: {"partnership"})
        item = {
            "id": 1,
            "israel_reasons": [],
            "summary_he": "",
            "so_what_he": "",
            "title": "",
            "entities_mentioned": ["IDF"],
        }
        assert isec._categorize(item) == {isec._CATEGORY_COMPETITION}


class TestCompanySummaryRows:
    def test_counts_mentions_wins_and_competitors_per_israeli_company(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("eoa.pipeline.israel_focus.israeli_watchlist_names", lambda: ["Elbit", "Rafael"])
        monkeypatch.setattr(
            isec,
            "_item_event_kinds",
            lambda item_id: {"contract_award"} if item_id == 1 else set(),
        )
        items = [
            {"id": 1, "entities_mentioned": ["Elbit", "Northrop Grumman"]},
            {"id": 2, "entities_mentioned": ["Elbit", "Thales"]},
            {"id": 3, "entities_mentioned": ["Rafael"]},
            {"id": 4, "entities_mentioned": ["Northrop Grumman"]},  # no Israeli company -- ignored
        ]
        rows = isec._company_summary_rows(items)
        by_name = {r[0]: r for r in rows}
        assert by_name["Elbit"][1] == 2  # mentions
        assert by_name["Elbit"][2] == 1  # wins (item 1 only)
        assert by_name["Elbit"][3] == 2  # competitors: Northrop Grumman, Thales
        assert by_name["Rafael"][1] == 1
        assert by_name["Rafael"][2] == 0


def test_daily_israel_tables_empty_when_no_items(monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime as dt

    monkeypatch.setattr(isec, "collect_israel_items", lambda start, end, min_relevance=0.5: [])
    tables = isec.daily_israel_tables([], dt.datetime(2026, 9, 1), dt.datetime(2026, 9, 2))
    assert tables == []
