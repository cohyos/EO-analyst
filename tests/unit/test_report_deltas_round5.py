"""Unit tests for `eoa.report.deltas` and `eoa.report.indicators` (Round 5 P2: D4, D5, W3).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_report_deltas_round5.py -q``
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

from eoa.report import deltas
from eoa.report import indicators as ind

# --------------------------------------------------------------------------
# fake-cursor DB harness (mirrors tests/unit/test_report_daily.py's own _FakeCursor/_FakeConn)
# --------------------------------------------------------------------------


class _FakeCursor:
    def __init__(
        self, fetchall_rows: list[dict] | None = None, fetchone_rows: list[dict | None] | None = None
    ):
        self.executed: list[tuple[str, dict]] = []
        self._fetchall_rows = fetchall_rows or []
        self._fetchone_queue = list(fetchone_rows or [])

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: dict | None = None) -> None:
        self.executed.append((sql, params or {}))

    def fetchall(self) -> list[dict]:
        return list(self._fetchall_rows)

    def fetchone(self) -> dict | None:
        return self._fetchone_queue.pop(0) if self._fetchone_queue else None


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def cursor(self) -> _FakeCursor:
        return self._cursor


# --------------------------------------------------------------------------
# eoa.report.deltas -- previous_report_state (fake cursor)
# --------------------------------------------------------------------------


class TestPreviousReportState:
    def test_returns_none_when_no_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cursor = _FakeCursor(fetchall_rows=[])
        cursor.fetchone = lambda: None  # type: ignore[method-assign]
        monkeypatch.setattr(deltas, "connection", lambda timeout=None: _FakeConn(cursor))
        result = deltas.previous_report_state("daily", before_period_end=dt.date(2026, 9, 6))
        assert result is None

    def test_returns_none_when_report_state_is_falsy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cursor = _FakeCursor()
        cursor.fetchone = lambda: {"id": 5, "report_state": None}  # type: ignore[method-assign]
        monkeypatch.setattr(deltas, "connection", lambda timeout=None: _FakeConn(cursor))
        result = deltas.previous_report_state("daily", before_period_end=dt.date(2026, 9, 6))
        assert result is None

    def test_returns_id_and_state_and_scopes_query_by_kind_and_before(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = {"item_ids": [1, 2], "item_levels": {}, "trend_titles": [], "indicator_ids": []}
        cursor = _FakeCursor()
        cursor.fetchone = lambda: {"id": 42, "report_state": state}  # type: ignore[method-assign]
        monkeypatch.setattr(deltas, "connection", lambda timeout=None: _FakeConn(cursor))
        result = deltas.previous_report_state(
            "bd_territory", before_period_end=dt.date(2026, 9, 6), territory="US"
        )
        assert result == (42, state)
        sql, params = cursor.executed[0]
        assert "kind = %(kind)s" in sql
        assert "report_state IS NOT NULL" in sql
        assert "period_end < %(before)s" in sql
        assert params == {"kind": "bd_territory", "before": dt.date(2026, 9, 6), "territory": "US"}


# --------------------------------------------------------------------------
# eoa.report.deltas -- build_report_state
# --------------------------------------------------------------------------


class TestBuildReportState:
    def test_shape_without_trends_or_indicators(self) -> None:
        items = [{"id": 1, "level": "red"}, {"id": 2, "level": "orange"}, {"id": None, "level": "yellow"}]
        state = deltas.build_report_state(items)
        assert state == {
            "item_ids": [1, 2],
            "item_levels": {"1": "red", "2": "orange"},
            "trend_titles": [],
            "indicator_ids": [],
        }

    def test_includes_trends_and_indicator_ids(self) -> None:
        items = [{"id": 1, "level": "red"}]
        trends = [{"title_he": "מגמת X", "strength": 3}, {"strength": 2}]  # second has no title -- dropped
        state = deltas.build_report_state(items, trends=trends, indicator_ids=[7, 9])
        assert state["trend_titles"] == [{"title_he": "מגמת X", "strength": 3}]
        assert state["indicator_ids"] == [7, 9]


# --------------------------------------------------------------------------
# eoa.report.deltas -- compute_item_deltas (D4)
# --------------------------------------------------------------------------


class TestComputeItemDeltas:
    def test_new_items_counted_and_top_n_kept_in_order(self) -> None:
        previous_state = {"item_ids": [1], "item_levels": {"1": "orange"}}
        current_items = [
            {"id": 1, "level": "orange", "title": "old"},
            {"id": 2, "level": "red", "title": "new A", "n": 2},
            {"id": 3, "level": "red", "title": "new B", "n": 3},
            {"id": 4, "level": "red", "title": "new C", "n": 4},
            {"id": 5, "level": "red", "title": "new D", "n": 5},
        ]
        result = deltas.compute_item_deltas(current_items, previous_state)
        assert result.new_count == 4
        assert [it["title"] for it in result.new_top_items] == ["new A", "new B", "new C"]

    def test_item_rising_in_level_is_detected(self) -> None:
        previous_state = {"item_ids": [1], "item_levels": {"1": "yellow"}}
        current_items = [{"id": 1, "level": "red", "title": "escalated", "n": 1}]
        result = deltas.compute_item_deltas(current_items, previous_state)
        assert result.new_count == 0
        assert len(result.risen_items) == 1
        assert result.risen_items[0]["from_level"] == "yellow"
        assert result.risen_items[0]["to_level"] == "red"

    def test_item_dropping_in_level_is_not_a_rise(self) -> None:
        previous_state = {"item_ids": [1], "item_levels": {"1": "red"}}
        current_items = [{"id": 1, "level": "orange", "title": "de-escalated"}]
        result = deltas.compute_item_deltas(current_items, previous_state)
        assert result.risen_items == []

    def test_item_with_no_id_is_ignored(self) -> None:
        previous_state = {"item_ids": [], "item_levels": {}}
        result = deltas.compute_item_deltas([{"id": None, "level": "red"}], previous_state)
        assert result.new_count == 0


# --------------------------------------------------------------------------
# eoa.report.deltas -- compute_trend_deltas (W3)
# --------------------------------------------------------------------------


class TestComputeTrendDeltas:
    def test_appeared_strengthened_weakened_vanished(self) -> None:
        previous = [
            {"title_he": "מגמת C-UAS", "strength": 3},
            {"title_he": "מגמת ימית", "strength": 4},
            {"title_he": "מגמה נעלמת", "strength": 2},
        ]
        current = [
            {"title_he": "מגמת C-UAS", "strength": 5, "evidence_item_ids": [10]},
            {"title_he": "מגמת ימית", "strength": 2, "evidence_item_ids": [11]},
            {"title_he": "מגמה חדשה", "strength": 3, "evidence_item_ids": [12]},
        ]
        id_to_n = {10: 1, 11: 2, 12: 3}
        result = deltas.compute_trend_deltas(current, previous, id_to_n=id_to_n)
        by_title = {d.title_he: d for d in result}
        assert by_title["מגמת C-UAS"].status == "strengthened"
        assert by_title["מגמת C-UAS"].previous_strength == 3
        assert by_title["מגמת C-UAS"].current_strength == 5
        assert by_title["מגמת C-UAS"].cites == [1]
        assert by_title["מגמת ימית"].status == "weakened"
        assert by_title["מגמה חדשה"].status == "appeared"
        assert by_title["מגמה נעלמת"].status == "vanished"
        assert by_title["מגמה נעלמת"].previous_strength == 2

    def test_unchanged_strength_not_reported(self) -> None:
        previous = [{"title_he": "יציבה", "strength": 3}]
        current = [{"title_he": "יציבה", "strength": 3}]
        assert deltas.compute_trend_deltas(current, previous) == []

    def test_title_matching_is_whitespace_and_case_insensitive(self) -> None:
        previous = [{"title_he": "  מגמת   C-UAS  ", "strength": 2}]
        current = [{"title_he": "מגמת C-UAS", "strength": 4}]
        result = deltas.compute_trend_deltas(current, previous)
        assert len(result) == 1
        assert result[0].status == "strengthened"


# --------------------------------------------------------------------------
# eoa.report.deltas -- compute_deltas / render_delta_section_he / delta_extra_section
# --------------------------------------------------------------------------


class TestComputeDeltas:
    def test_no_previous_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(deltas, "previous_report_state", lambda *a, **k: None)
        result = deltas.compute_deltas(
            "daily", [{"id": 1, "level": "red"}], before_period_end=dt.date(2026, 9, 6)
        )
        assert result.has_previous is False
        assert result.previous_report_id is None
        assert result.item_delta.new_count == 0
        assert "אין דוח קודם" in result.summary_he

    def test_with_previous_report_computes_item_and_trend_deltas(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = {
            "item_ids": [1],
            "item_levels": {"1": "orange"},
            "trend_titles": [{"title_he": "ת", "strength": 1}],
        }
        monkeypatch.setattr(deltas, "previous_report_state", lambda *a, **k: (7, state))
        current_items = [{"id": 1, "level": "orange"}, {"id": 2, "level": "red", "title": "חדש", "n": 2}]
        current_trends = [{"title_he": "ת", "strength": 4, "evidence_item_ids": []}]
        result = deltas.compute_deltas(
            "weekly",
            current_items,
            before_period_end=dt.date(2026, 9, 6),
            current_trends=current_trends,
        )
        assert result.has_previous is True
        assert result.previous_report_id == 7
        assert result.item_delta.new_count == 1
        assert len(result.trend_deltas) == 1
        assert result.trend_deltas[0].status == "strengthened"
        assert "1 פריטים חדשים" in result.summary_he

    def test_daily_never_computes_trend_deltas_when_current_trends_omitted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = {"item_ids": [], "item_levels": {}, "trend_titles": [{"title_he": "ת", "strength": 1}]}
        monkeypatch.setattr(deltas, "previous_report_state", lambda *a, **k: (1, state))
        result = deltas.compute_deltas("daily", [], before_period_end=dt.date(2026, 9, 6))
        assert result.trend_deltas == []


class TestRenderDeltaSection:
    def test_no_previous_returns_summary_only(self) -> None:
        result = deltas.DeltaResult(
            has_previous=False,
            previous_report_id=None,
            item_delta=deltas.ItemDelta(),
            trend_deltas=[],
            summary_he="זהו הדוח הראשון.",
        )
        assert deltas.render_delta_section_he(result) == "זהו הדוח הראשון."

    def test_with_content_renders_bullets_and_citations(self) -> None:
        item_delta = deltas.ItemDelta(
            new_count=1,
            new_top_items=[{"id": 2, "n": 2, "title": "פריט חדש"}],
            risen_items=[{"id": 3, "n": 3, "title": "פריט שעלה", "from_level": "yellow", "to_level": "red"}],
        )
        trend_delta = deltas.TrendDelta(title_he="מגמת X", status="appeared", current_strength=3, cites=[4])
        result = deltas.DeltaResult(
            has_previous=True,
            previous_report_id=1,
            item_delta=item_delta,
            trend_deltas=[trend_delta],
            summary_he="לעומת הדוח הקודם: 1 פריטים חדשים.",
        )
        body = deltas.render_delta_section_he(result)
        assert "- פריט חדש [2]" in body
        assert "פריט שעלה: צהוב ← אדום [3]" in body
        assert "מגמת X: מגמה חדשה (עוצמה 3) [4]" in body


def test_delta_extra_section_shape() -> None:
    result = deltas.DeltaResult(
        has_previous=False,
        previous_report_id=None,
        item_delta=deltas.ItemDelta(),
        trend_deltas=[],
        summary_he="קו אחד כן.",
    )
    section = deltas.delta_extra_section(result)
    assert section["title_he"] == deltas.SECTION_TITLE_HE == "מה השתנה מאז הדוח הקודם"
    assert section["position"] == "after_summary"
    assert section["body_he"] == "קו אחד כן."


# --------------------------------------------------------------------------
# eoa.report.indicators -- extract_key_terms / _item_matches_indicator (pure)
# --------------------------------------------------------------------------


class TestExtractKeyTerms:
    def test_extracts_english_alphanumeric_tokens(self) -> None:
        terms = ind.extract_key_terms("להערכתנו מערכת ה-DROIC החדשה תיכנס לשלב הבא (Program ABC-2)")
        assert "droic" in terms
        assert "program" in terms
        assert "abc-2" in terms

    def test_hebrew_only_text_has_no_key_terms(self) -> None:
        assert ind.extract_key_terms("להערכתנו יחול שיפור ניכר בשוק") == set()


class TestItemMatchesIndicator:
    def test_matches_when_key_term_in_title(self) -> None:
        item = {"title": "DROIC sensor unveiled", "summary_he": "", "so_what_he": ""}
        assert ind._item_matches_indicator("להערכתנו מערכת ה-DROIC תבשיל", item) is True

    def test_no_match_when_no_key_terms_at_all(self) -> None:
        item = {"title": "כותרת עברית בלבד", "summary_he": "", "so_what_he": ""}
        assert ind._item_matches_indicator("להערכתנו יחול שיפור", item) is False

    def test_no_match_when_key_term_absent_from_item(self) -> None:
        item = {"title": "Something else entirely", "summary_he": "", "so_what_he": ""}
        assert ind._item_matches_indicator("להערכתנו מערכת ה-DROIC תבשיל", item) is False


# --------------------------------------------------------------------------
# eoa.report.indicators -- check_maturation (pure; D5)
# --------------------------------------------------------------------------


class TestCheckMaturation:
    def test_matches_item_matures_with_evidence(self) -> None:
        now = dt.datetime(2026, 9, 6, tzinfo=dt.UTC)
        open_indicators = [
            {"id": 1, "text_he": "להערכתנו מערכת DROIC תבשיל", "first_seen": now - dt.timedelta(days=5)}
        ]
        items = [
            {"id": 99, "n": 9, "title": "DROIC delivered to customer", "summary_he": "", "so_what_he": ""}
        ]
        still_open, matured, dropped = ind.check_maturation(open_indicators, items, now=now)
        assert still_open == []
        assert dropped == []
        assert len(matured) == 1
        assert matured[0]["matured_evidence_item_id"] == 99
        assert matured[0]["_evidence_n"] == 9

    def test_no_match_recent_stays_open(self) -> None:
        now = dt.datetime(2026, 9, 6, tzinfo=dt.UTC)
        open_indicators = [{"id": 1, "text_he": "להערכתנו X", "first_seen": now - dt.timedelta(days=5)}]
        still_open, matured, dropped = ind.check_maturation(open_indicators, [], now=now)
        assert len(still_open) == 1
        assert matured == []
        assert dropped == []

    def test_no_match_past_max_age_is_dropped(self) -> None:
        now = dt.datetime(2026, 9, 6, tzinfo=dt.UTC)
        open_indicators = [{"id": 1, "text_he": "להערכתנו X", "first_seen": now - dt.timedelta(days=45)}]
        still_open, _matured, dropped = ind.check_maturation(open_indicators, [], now=now)
        assert still_open == []
        assert len(dropped) == 1

    def test_match_wins_over_age_even_when_old(self) -> None:
        now = dt.datetime(2026, 9, 6, tzinfo=dt.UTC)
        open_indicators = [{"id": 1, "text_he": "DROIC", "first_seen": now - dt.timedelta(days=90)}]
        items = [{"id": 5, "n": 1, "title": "DROIC news", "summary_he": "", "so_what_he": ""}]
        _, matured, dropped = ind.check_maturation(open_indicators, items, now=now)
        assert len(matured) == 1
        assert dropped == []


# --------------------------------------------------------------------------
# eoa.report.indicators -- DB-touching helpers (fake cursor)
# --------------------------------------------------------------------------


class TestFetchOpenIndicators:
    def test_scopes_by_kind_and_status_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cursor = _FakeCursor(fetchall_rows=[{"id": 1, "text_he": "x", "kind": "daily"}])
        monkeypatch.setattr(ind, "connection", lambda timeout=None: _FakeConn(cursor))
        rows = ind._fetch_open_indicators("daily")
        assert rows == [{"id": 1, "text_he": "x", "kind": "daily"}]
        sql, params = cursor.executed[0]
        assert "status = 'open'" in sql
        assert params == {"kind": "daily"}


class TestApplyMaturation:
    def test_noop_when_nothing_to_persist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom():
            raise AssertionError("connection() should not be called when there is nothing to persist")

        monkeypatch.setattr(ind, "connection", _boom)
        ind._apply_maturation([], [])

    def test_executes_update_per_matured_and_dropped_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cursor = _FakeCursor()
        monkeypatch.setattr(ind, "connection", lambda timeout=None: _FakeConn(cursor))
        ind._apply_maturation(
            matured=[{"id": 1, "matured_evidence_item_id": 99}],
            dropped=[{"id": 2}],
        )
        assert len(cursor.executed) == 2
        assert "matured" in cursor.executed[0][0]
        assert cursor.executed[0][1] == {"eid": 99, "id": 1}
        assert "dropped" in cursor.executed[1][0]
        assert cursor.executed[1][1] == {"id": 2}


class TestUpsertOpen:
    def test_dedupe_bumps_existing_row_without_insert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cursor = _FakeCursor()
        monkeypatch.setattr(ind, "connection", lambda timeout=None: _FakeConn(cursor))
        still_open = [{"id": 1, "text_he": "להערכתנו מערכת ה-DROIC תבשיל בקרוב"}]
        now = dt.datetime(2026, 9, 6, tzinfo=dt.UTC)
        touched_ids, newly_created = ind._upsert_open(
            ["להערכתנו מערכת ה DROIC תבשיל בקרוב"],  # near-identical reword
            still_open,
            kind="daily",
            source_report_id=None,
            now=now,
        )
        assert touched_ids == {1}
        assert newly_created == []
        assert any("UPDATE" in sql for sql, _ in cursor.executed)
        assert not any("INSERT" in sql for sql, _ in cursor.executed)

    def test_no_match_inserts_new_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        now = dt.datetime(2026, 9, 6, tzinfo=dt.UTC)
        new_row = {
            "id": 10,
            "text_he": "להערכתנו איום חדש לגמרי",
            "source_report_id": None,
            "first_seen": now,
            "last_seen": now,
            "status": "open",
            "kind": "daily",
        }
        cursor = _FakeCursor(fetchone_rows=[new_row])
        monkeypatch.setattr(ind, "connection", lambda timeout=None: _FakeConn(cursor))
        touched_ids, newly_created = ind._upsert_open(
            ["להערכתנו איום חדש לגמרי"], [], kind="daily", source_report_id=None, now=now
        )
        assert touched_ids == set()
        assert newly_created == [new_row]
        assert any("INSERT" in sql for sql, _ in cursor.executed)

    def test_empty_text_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cursor = _FakeCursor()
        monkeypatch.setattr(ind, "connection", lambda timeout=None: _FakeConn(cursor))
        touched_ids, newly_created = ind._upsert_open(
            ["   "], [], kind="daily", source_report_id=None, now=dt.datetime.now(dt.UTC)
        )
        assert touched_ids == set()
        assert newly_created == []
        assert cursor.executed == []


# --------------------------------------------------------------------------
# eoa.report.indicators -- process_indicator_watchlist orchestration (monkeypatched pieces)
# --------------------------------------------------------------------------


class TestProcessIndicatorWatchlist:
    def test_rows_tagged_by_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        matured_row = {"id": 1, "text_he": "m", "matured_evidence_item_id": 9}
        dropped_row = {"id": 2, "text_he": "d"}
        still_open_row = {"id": 3, "text_he": "o"}
        new_row = {"id": 4, "text_he": "n"}

        monkeypatch.setattr(
            ind, "_fetch_open_indicators", lambda kind: [matured_row, dropped_row, still_open_row]
        )
        monkeypatch.setattr(
            ind,
            "check_maturation",
            lambda open_indicators, items, **k: ([still_open_row], [matured_row], [dropped_row]),
        )
        monkeypatch.setattr(ind, "_apply_maturation", lambda matured, dropped: None)
        monkeypatch.setattr(ind, "_upsert_open", lambda texts, still_open, **k: (set(), [new_row]))

        rows = ind.process_indicator_watchlist("daily", ["some text"], [])
        by_status = {r["_row_status"]: r["id"] for r in rows}
        assert by_status == {"matured": 1, "dropped": 2, "open": 3, "new": 4}


# --------------------------------------------------------------------------
# eoa.report.indicators -- render_watchlist_table / build_indicator_watchlist_section
# --------------------------------------------------------------------------


class TestRenderWatchlistTable:
    def test_none_when_no_rows(self) -> None:
        assert ind.render_watchlist_table([], []) is None

    def test_renders_markdown_table_with_status_labels_and_evidence(self) -> None:
        rows = [
            {
                "id": 1,
                "text_he": "אינדיקטור חדש",
                "first_seen": dt.datetime(2026, 9, 1),
                "_row_status": "new",
            },
            {
                "id": 2,
                "text_he": "אינדיקטור שהבשיל",
                "first_seen": dt.datetime(2026, 8, 1),
                "_row_status": "matured",
                "matured_evidence_item_id": 55,
            },
        ]
        citation_items = [{"id": 55, "n": 7}]
        section = ind.render_watchlist_table(rows, citation_items)
        assert section is not None
        assert section["title_he"] == "מעקב אינדיקטורים"
        assert section["position"] == "after_outlook"
        body = section["body_he"]
        assert "| אינדיקטור | מאז | סטטוס | ראיה |" in body
        assert "חדש" in body
        assert "הבשיל" in body
        assert "[7]" in body


class TestOutlookIndicatorTexts:
    def test_duck_types_objects_and_dicts(self) -> None:
        class FakeIndicator:
            def __init__(self, text_he: str) -> None:
                self.text_he = text_he

        outlook = [FakeIndicator("א"), {"text_he": "ב"}, {"text_he": ""}, FakeIndicator("")]
        assert ind.outlook_indicator_texts(outlook) == ["א", "ב"]


class TestBuildIndicatorWatchlistSection:
    def test_wires_texts_through_to_process_and_render(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        def fake_process(kind, texts, items, **kwargs):
            captured["kind"] = kind
            captured["texts"] = texts
            return [{"id": 1, "_row_status": "new", "text_he": texts[0] if texts else ""}]

        monkeypatch.setattr(ind, "process_indicator_watchlist", fake_process)
        section, rows = ind.build_indicator_watchlist_section(
            "weekly", [{"text_he": "משהו"}], [{"id": 1}], []
        )
        assert captured["kind"] == "weekly"
        assert captured["texts"] == ["משהו"]
        assert section is not None
        assert len(rows) == 1
