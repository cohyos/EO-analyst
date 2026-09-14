"""Unit tests for R12-reports (docs/qa/loop/round_11_judge.md worst #3, #4, #8, #9).

Worst #3 (D6): the monthly report never rendered a "מעקב אינדיקטורים" (I&W watchlist) section at
all. Covered by ``TestMonthlyIndicatorWatchlistWiring``: :func:`eoa.report.monthly.build_monthly`
now calls ``eoa.report.indicators.build_indicator_watchlist_section`` with ``kind="monthly"`` and
folds a non-``None`` result into ``extra_sections``; a failure inside it is caught and never breaks
the build (mirrors the existing ``_israel_month_tables`` failure-isolation contract).

Worst #4 (D6): the weekly indicator table had grown to 10 rows against the brief's own <= 8-row
cap (the per-story cap was daily-only). Covered by ``TestIndicatorWatchlistCapAppliesToEveryKind``:
:func:`eoa.report.indicators.render_watchlist_table` now runs :func:`_cap_watchlist_rows`
regardless of ``kind``.

Worst #9 (D3/D6): "Operation Atlantic City" (a NATO exercise name correctly rendered in the events
table via ``events.program``) also leaked into two unrelated entity-shaped monthly tables because
the entity-extraction pipeline (out of this round's scope) had created an ``entities`` row for it.
Covered by ``TestExerciseLabelFilter``/``TestPlayersMapExcludesExerciseLabels``/
``TestWatchlistChangesExcludesExerciseLabels``: :func:`eoa.report.monthly.players_map` and
:func:`eoa.report.monthly.watchlist_changes` both drop any row whose name matches
:func:`eoa.report.monthly._is_exercise_or_operation_label`.

Worst #8 (D7): pl_mws_eo was built on an out-of-scope-tagged sole item, and its patents table
showed duplicate rows. Covered by ``TestProductLineItemScope``/``TestPatentDedupe``:
:func:`eoa.report.product_line.collect_market_items` now excludes ``domain='out_of_scope'`` (the
same scope rule ``eoa.report.monthly``'s own item collectors already apply), and
:func:`eoa.report.product_line.collect_patents`/``_dedupe_patents_by_pub_number`` dedupe by
``pub_number``, keeping the newest (first, since the query is already newest-first-ordered).

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_reports_round12.py -q``
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

from eoa.llm.schemas.analysis import OutlookIndicator, Sentence, StructuredSection
from eoa.llm.schemas.reports import MonthlyReportDraft
from eoa.report import indicators, monthly
from eoa.report import product_line as pl

UTC = dt.UTC


# --------------------------------------------------------------------------
# Worst #9 -- exercise/operation label filter (pure function)
# --------------------------------------------------------------------------


class TestExerciseLabelFilter:
    def test_matches_english_operation_prefix(self) -> None:
        assert monthly._is_exercise_or_operation_label("Operation Atlantic City") is True

    def test_matches_english_operation_prefix_case_insensitive(self) -> None:
        assert monthly._is_exercise_or_operation_label("operation Desert Focus") is True

    def test_matches_hebrew_targil_token(self) -> None:
        assert monthly._is_exercise_or_operation_label('תרגיל נאט"ו 2026') is True

    def test_matches_hebrew_mivtsa_token(self) -> None:
        assert monthly._is_exercise_or_operation_label("מבצע חרב ברזל") is True

    def test_does_not_match_legitimate_program_name(self) -> None:
        # Arctic Sentry / Defense Innovation Unit (DIU) are real kind='program' entities on the
        # live monthly -- neither starts with "Operation " nor contains a Hebrew exercise token.
        assert monthly._is_exercise_or_operation_label("Arctic Sentry") is False
        assert monthly._is_exercise_or_operation_label("Defense Innovation Unit (DIU)") is False

    def test_does_not_match_word_merely_starting_with_operation(self) -> None:
        # "^operation\s" requires a following whitespace char -- "Operationally" must not match.
        assert monthly._is_exercise_or_operation_label("Operationally superior radar") is False

    def test_none_and_empty_are_false(self) -> None:
        assert monthly._is_exercise_or_operation_label(None) is False
        assert monthly._is_exercise_or_operation_label("") is False


# --------------------------------------------------------------------------
# Worst #9 -- players_map()/watchlist_changes() drop exercise-labeled rows
# --------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def execute(self, sql, params=None) -> None:
        return None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


class _FakeConn:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def cursor(self, row_factory=None):
        return _FakeCursor(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


class TestPlayersMapExcludesExerciseLabels:
    def test_exercise_entity_dropped_legit_entity_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {"id": 1, "name": "Kongsberg", "domain": "naval_surveillance", "n": 5},
            {"id": 2, "name": "Operation Atlantic City", "domain": "naval_surveillance", "n": 3},
        ]
        monkeypatch.setattr(monthly, "connection", lambda: _FakeConn(rows))
        monkeypatch.setattr(monthly.graph_mod, "neighbors", lambda eid, label: [])

        out = monthly.players_map()

        names = {r["name"] for domain_rows in out.values() for r in domain_rows}
        assert "Kongsberg" in names
        assert "Operation Atlantic City" not in names

    def test_all_rows_are_exercise_labels_yields_empty_map(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"id": 9, "name": "מבצע חרב ברזל", "domain": "c_uas", "n": 1}]
        monkeypatch.setattr(monthly, "connection", lambda: _FakeConn(rows))
        monkeypatch.setattr(monthly.graph_mod, "neighbors", lambda eid, label: [])

        assert monthly.players_map() == {}


class TestWatchlistChangesExcludesExerciseLabels:
    def test_exercise_entity_dropped_legit_entity_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {
                "id": 1,
                "name": "NewCo",
                "kind": "company",
                "country": "US",
                "created_at": dt.datetime(2026, 9, 3, tzinfo=UTC),
            },
            {
                "id": 2,
                "name": "Operation Atlantic City",
                "kind": "program",
                "country": None,
                "created_at": dt.datetime(2026, 9, 5, tzinfo=UTC),
            },
        ]
        monkeypatch.setattr(monthly, "connection", lambda: _FakeConn(rows))

        out = monthly.watchlist_changes(dt.date(2026, 9, 1), dt.date(2026, 9, 30))

        names = {r["name"] for r in out}
        assert names == {"NewCo"}

    def test_rendered_bullet_list_never_contains_exercise_label(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [
            {
                "id": 2,
                "name": "Operation Atlantic City",
                "kind": "program",
                "country": None,
                "created_at": dt.datetime(2026, 9, 5, tzinfo=UTC),
            },
        ]
        monkeypatch.setattr(monthly, "connection", lambda: _FakeConn(rows))

        out = monthly.watchlist_changes(dt.date(2026, 9, 1), dt.date(2026, 9, 30))
        assert out == []
        # format_watchlist_he falls back to its "nothing added" sentence when entries is empty --
        # the exercise label never reaches the rendered "glossary" bullet list.
        rendered = monthly.format_watchlist_he(out)
        assert "Operation Atlantic City" not in rendered


# --------------------------------------------------------------------------
# Worst #4 -- indicator watchlist per-story/8-row cap applies to every kind
# --------------------------------------------------------------------------


#: Ten distinct two-word topic pairs -- each row's `_cluster_key` (its own two longest content
#: tokens) is therefore unique, so `_cap_watchlist_rows`'s per-cluster pass (max 3/cluster) never
#: fires and only the 8-row *total* cap this test targets is exercised.
_TOPICS = [
    "Alphaville Brontosaurus",
    "Cascadilla Driftwood",
    "Everglade Firestorm",
    "Glockenspiel Hurricane",
    "Ironclad Juggernaut",
    "Katydidae Landslide",
    "Marmalade Nightingale",
    "Obsidianite Pergola",
    "Quicksilver Ravensworth",
    "Silverback Tumbleweed",
]


def _watchlist_rows(n: int, *, day0: dt.date = dt.date(2026, 8, 1)) -> list[dict]:
    return [
        {
            "id": i,
            "text_he": f"{_TOPICS[i]} indicator note",
            "first_seen": dt.datetime.combine(day0, dt.time.min, tzinfo=UTC) + dt.timedelta(days=i),
            "_row_status": "open",
        }
        for i in range(n)
    ]


class TestIndicatorWatchlistCapAppliesToEveryKind:
    @pytest.mark.parametrize("kind", ["daily", "weekly", "monthly", None])
    def test_ten_rows_capped_at_eight_for_every_kind(self, kind) -> None:
        rows = _watchlist_rows(10)
        section = indicators.render_watchlist_table(rows, [], [], kind=kind)
        assert section is not None
        # header (2 lines) + at most 8 data rows
        assert len(section["body_he"].splitlines()) == 2 + 8

    def test_under_cap_row_count_is_unaffected(self) -> None:
        rows = _watchlist_rows(3)
        section = indicators.render_watchlist_table(rows, [], [], kind="monthly")
        assert len(section["body_he"].splitlines()) == 2 + 3


# --------------------------------------------------------------------------
# Worst #3 -- monthly wires the indicator watchlist section
# --------------------------------------------------------------------------


def _monthly_draft_fixture() -> MonthlyReportDraft:
    return MonthlyReportDraft(
        exec_summary=[Sentence(text_he="IAI זכתה בחוזה חדש לאספקת מערכת EO ימית.", cites=[1])],
        trends=[],
        sections=[
            StructuredSection(
                title_he="תצפית ימית",
                domain="naval_surveillance",
                sentences=[Sentence(text_he="IAI זכתה בחוזה חדש.", cites=[1])],
            ),
        ],
        outlook=[OutlookIndicator(text_he="להערכתנו המגמה תימשך.", cites=[], is_assessment=True)],
        open_points_he=[],
    )


@pytest.fixture
def patch_monthly_collectors(monkeypatch: pytest.MonkeyPatch, tmp_path):
    month_items = [
        {
            "id": 201,
            "n": 1,
            "title": "IAI wins naval radar deal",
            "domain": "naval_surveillance",
            "source_name": "Naval News",
            "url": "https://example.com/201",
            "published_at": dt.date(2026, 8, 5),
            "level": "red",
            "summary_he": "IAI זכתה בחוזה חדש לאספקת מערכת EO ימית [1].",
            "so_what_he": "מחזק את מעמדה התחרותי.",
        },
    ]
    monkeypatch.setattr(monthly, "collect_month_items", lambda s, e: [dict(it) for it in month_items])
    monkeypatch.setattr(monthly, "collect_yellow_domain_summary", lambda s, e, limit=None: [])
    monkeypatch.setattr(monthly, "collect_events", lambda s, e, limit=None: [])
    monkeypatch.setattr(monthly, "collect_deep_search", lambda s, e, limit=None: [])
    monkeypatch.setattr(monthly, "collect_open_clarifications", lambda: [])
    monkeypatch.setattr(monthly.trends_mod, "detect_trends", lambda period: [])
    monkeypatch.setattr(monthly, "collect_previous_monthly_trends", lambda period_start: [])
    monkeypatch.setattr(monthly, "_has_previous_monthly_report", lambda period_start: False)
    monkeypatch.setattr(monthly, "draft_monthly", lambda *a, **k: _monthly_draft_fixture())
    monkeypatch.setattr(monthly, "players_map", lambda: {})
    monkeypatch.setattr(monthly, "top_events_by_amount", lambda s, e, limit=10: [])
    monkeypatch.setattr(monthly, "full_horizon_table", lambda: [])
    monkeypatch.setattr(monthly, "watchlist_changes", lambda s, e, limit=None: [])
    # Not this round's target -- kept DB-free so this test never depends on live Postgres.
    monkeypatch.setattr(monthly, "_israel_month_tables", lambda citation_items, s, e: [])
    monkeypatch.setattr(
        "eoa.patents.report_section.collect_patents_landscape",
        lambda **k: {"by_subdomain": [], "by_assignee": []},
    )
    monkeypatch.setattr(monthly, "_persist_report", lambda *a, **k: 999)
    monkeypatch.setattr(monthly, "_report_path", lambda period_end, ext: tmp_path / f"monthly.{ext}")
    return tmp_path


class TestMonthlyIndicatorWatchlistWiring:
    def test_build_monthly_calls_indicator_section_with_monthly_kind(
        self, patch_monthly_collectors, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_section(kind, outlook, items, citation_items, **kwargs):
            captured["kind"] = kind
            captured["outlook"] = outlook
            return (
                {
                    "title_he": "מעקב אינדיקטורים",
                    "body_he": "| x |\n|---|\n| y |",
                    "position": "after_outlook",
                },
                [],
            )

        monkeypatch.setattr(indicators, "build_indicator_watchlist_section", fake_section)

        paths = monthly.build_monthly(period_end=dt.date(2026, 8, 31))

        assert captured["kind"] == "monthly"
        assert paths.qa.passed
        md_text = paths.md.read_text(encoding="utf-8")
        assert "מעקב אינדיקטורים" in md_text

    def test_indicator_section_failure_does_not_break_the_build(
        self, patch_monthly_collectors, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*a, **k):
            raise RuntimeError("indicator_watchlist unreachable")

        monkeypatch.setattr(indicators, "build_indicator_watchlist_section", boom)

        paths = monthly.build_monthly(period_end=dt.date(2026, 8, 31))

        assert paths.qa.passed
        assert paths.md.exists()


# --------------------------------------------------------------------------
# Worst #8 -- product_line.py item scope (domain != out_of_scope) + patent dedupe
# --------------------------------------------------------------------------


class TestProductLineItemScope:
    def test_collect_market_items_sql_excludes_out_of_scope_domain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_fetchall(query, params=None):
            captured["query"] = query
            captured["params"] = params
            return []

        monkeypatch.setattr(pl, "_fetchall", fake_fetchall)

        pl.collect_market_items("mws_eo", dt.date(2026, 6, 1), dt.date(2026, 9, 1))

        query = captured["query"]
        assert "out_of_scope" in query
        assert "domain" in query


class TestPatentDedupe:
    def test_dedupe_by_pub_number_keeps_first_seen_i_e_newest(self) -> None:
        patents = [
            {"id": 1, "pub_number": "US123", "title": "newer filing"},
            {"id": 2, "pub_number": "US999", "title": "unrelated"},
            {"id": 3, "pub_number": "US123", "title": "older duplicate"},
        ]
        out = pl._dedupe_patents_by_pub_number(patents)
        assert [p["id"] for p in out] == [1, 2]

    def test_rows_with_no_pub_number_are_never_collapsed_together(self) -> None:
        patents = [
            {"id": 1, "pub_number": None, "title": "a"},
            {"id": 2, "pub_number": None, "title": "b"},
        ]
        out = pl._dedupe_patents_by_pub_number(patents)
        assert [p["id"] for p in out] == [1, 2]

    def test_collect_patents_applies_dedupe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {"id": 1, "pub_number": "US123", "publication_date": dt.date(2026, 9, 1)},
            {"id": 2, "pub_number": "US123", "publication_date": dt.date(2026, 8, 1)},
        ]
        monkeypatch.setattr(pl, "_fetchall", lambda query, params=None: list(rows))

        out = pl.collect_patents("mws_eo")

        assert len(out) == 1
        assert out[0]["id"] == 1
