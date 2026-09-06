"""Unit tests for `eoa.report.israel_section` (A13: "תעשייה ישראלית" report section).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_israel_section.py -q``
"""

from __future__ import annotations

import datetime as dt
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


class TestCollectIsraelItemsGuards:
    """User request 2026-09-06: an ``out_of_scope``/``archive`` item must never reach the Israeli
    industry tables on ``israel_relevance`` alone (report 28 rendered op-eds 4679/4375/4671, all
    correctly out_of_scope/archive in the DB). The SQL guard landed in 84bb998; this pins it."""

    def test_query_requires_in_scope_triaged_item(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        def fake_fetchall(sql: str, params: dict | None = None) -> list[dict]:
            captured["sql"] = sql
            captured["params"] = params
            return []

        monkeypatch.setattr(isec, "_fetchall", fake_fetchall)
        isec.collect_israel_items(dt.datetime(2026, 9, 1), dt.datetime(2026, 9, 7))
        sql = str(captured["sql"])
        assert "i.domain <> 'out_of_scope'" in sql
        assert "i.level IN ('red', 'orange', 'yellow')" in sql
        assert "israel_relevance" in sql  # still ranked/filtered by relevance, but never by it alone
        assert "NOT EXISTS (SELECT 1 FROM tenders" in sql


# --------------------------------------------------------------------------
# Round 5 P2 (D6): the four category tables merged into ONE table with a "סוג" column, one row
# per item (an item eligible for more than one category gets a single row joining its labels).
# --------------------------------------------------------------------------


class TestMergedIsraelTable:
    def test_one_row_per_item_with_joined_category_labels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # item 1: wins + competition (contract_award + competitor reason) -> one row, "זכייה/תחרות"
        # item 2: export only -> one row, "יצוא"
        monkeypatch.setattr(
            isec,
            "_item_event_kinds",
            lambda item_id: {"contract_award"} if item_id == 1 else set(),
        )
        items = [
            {
                "id": 1,
                "title": "עסקה משולבת",
                "israel_reasons": ["competitor_to_israeli_company"],
                "summary_he": "",
                "so_what_he": "כך וכך",
                "title_he": "",
                "entities_mentioned": ["Elbit"],
                "n": 1,
            },
            {
                "id": 2,
                "title": "יצוא חדש",
                "israel_reasons": ["export_market_signal"],
                "summary_he": "",
                "so_what_he": "יצוא",
                "entities_mentioned": [],
                "n": 2,
            },
        ]
        citation_items: list[dict] = []
        table = isec._merged_israel_table(citation_items, items, max_items=10)
        assert table is not None
        assert table["title_he"] == "תעשייה ישראלית"
        assert table["headers"] == ["כותרת", "סוג", "ישויות", "מה זה אומר", "מקור"]
        assert len(table["rows"]) == 2  # one row per item, never one row per category
        row1, row2 = table["rows"]
        assert row1[0] == "עסקה משולבת"
        assert row1[1] == "זכייה/תחרות"
        assert row2[1] == "יצוא"

    def test_excludes_items_with_no_category(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: set())
        items = [
            {
                "id": 1,
                "title": "אופ-אד פוליטי",
                "israel_reasons": [],
                "summary_he": "",
                "so_what_he": "",
                "entities_mentioned": ["IDF"],
                "n": 1,
            }
        ]
        table = isec._merged_israel_table([], items, max_items=10)
        assert table is None

    def test_respects_max_items_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: {"contract_award"})
        items = [
            {"id": i, "title": f"item {i}", "israel_reasons": [], "summary_he": "", "so_what_he": "", "n": i}
            for i in range(1, 6)
        ]
        table = isec._merged_israel_table([], items, max_items=2)
        assert table is not None
        assert len(table["rows"]) == 2


class TestDailyIsraelTablesMerged:
    def test_returns_single_merged_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: {"contract_award"})
        items = [{"id": 1, "title": "t", "israel_reasons": [], "summary_he": "", "so_what_he": "", "n": 1}]
        monkeypatch.setattr(isec, "collect_israel_items", lambda start, end, min_relevance=0.5: items)
        tables = isec.daily_israel_tables([], dt.datetime(2026, 9, 1), dt.datetime(2026, 9, 2))
        assert len(tables) == 1
        assert tables[0]["title_he"] == "תעשייה ישראלית"


class TestWeeklyIsraelTablesMerged:
    def test_returns_merged_table_plus_company_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("eoa.pipeline.israel_focus.israeli_watchlist_names", lambda: ["Elbit", "Rafael"])
        monkeypatch.setattr(isec, "_item_event_kinds", lambda item_id: {"contract_award"})
        items = [
            {
                "id": 1,
                "title": "t",
                "israel_reasons": [],
                "summary_he": "",
                "so_what_he": "",
                "entities_mentioned": ["Elbit"],
                "n": 1,
            }
        ]
        monkeypatch.setattr(isec, "collect_israel_items", lambda start, end, min_relevance=0.5: items)
        tables = isec.weekly_israel_tables([], dt.datetime(2026, 9, 1), dt.datetime(2026, 9, 8))
        assert len(tables) == 2
        assert tables[0]["title_he"] == "תעשייה ישראלית"
        assert tables[1]["title_he"] == "תעשייה ישראלית — סיכום שבועי לפי חברה"
