"""Unit tests for A12 (מעקב טכנולוגי): `eoa.pipeline.tech_watch` + `eoa.report.tech_watch`.

DB access in both modules goes through a small local `_fetchall`/`connection()` pattern (see
their module docstrings) -- these tests fake that layer with an in-memory row queue rather than
touching a real database, per `docs/CONVENTIONS.md` rule 10 (unit tests run without Docker/GPU).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_tech_watch.py -q``
"""

from __future__ import annotations

import datetime as dt
import sys
import types

import pytest

# Stub out eoa.db if not importable in this environment (mirrors test_persist_analysis.py).
if "eoa.db" not in sys.modules:
    try:
        import eoa.db  # noqa: F401
    except ImportError:
        fake_db = types.ModuleType("eoa.db")
        fake_db.connection = lambda: None  # type: ignore[attr-defined]
        fake_db.get_pool = lambda: None  # type: ignore[attr-defined]
        sys.modules["eoa.db"] = fake_db

from eoa.pipeline.tech_watch import (
    SubdomainAggregate,
    TechSoWhatOut,
    _actors,
    compute_momentum,
    generate_so_what,
)
from eoa.report.tech_watch import (
    _extend_registry,
    daily_tech_watch_table,
    weekly_tech_watch_tables,
)


class _FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, query: str, params: dict | None = None) -> None:
        self.last_query = query
        self.last_params = params

    def fetchall(self) -> list[dict]:
        return self._rows


class _FakeConnection:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._rows)


def _fake_connection_factory(rows: list[dict]):
    def _connection():
        return _FakeConnection(rows)

    return _connection


# --------------------------------------------------------------------------
# eoa.pipeline.tech_watch
# --------------------------------------------------------------------------


def test_actors_dedups_preserving_order() -> None:
    items = [
        {"entities_mentioned": ["Teledyne FLIR", "MIT"]},
        {"entities_mentioned": ["MIT", "Caltech"]},
    ]
    assert _actors(items) == ["Teledyne FLIR", "MIT", "Caltech"]


def test_compute_momentum_up_when_baseline_zero_and_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    # First call is this week (3 items); the following 4 baseline-week calls are all 0.
    counts = iter([3, 0, 0, 0, 0])
    monkeypatch.setattr("eoa.pipeline.tech_watch.count_in_window", lambda sub, start, end: next(counts))
    period_end = dt.datetime(2026, 9, 6, tzinfo=dt.UTC)
    momentum, delta = compute_momentum("droic_digital_pixel", period_end)
    assert momentum == "up"
    assert delta is None


def test_compute_momentum_flat_when_no_activity_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("eoa.pipeline.tech_watch.count_in_window", lambda sub, start, end: 0)
    period_end = dt.datetime(2026, 9, 6, tzinfo=dt.UTC)
    momentum, delta = compute_momentum("swir_eswir", period_end)
    assert momentum == "flat"
    assert delta is None


def test_compute_momentum_detects_up_and_down_against_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    # This week: 10; baseline weeks (4 of them, most recent first as called): 5,5,5,5 -> avg 5
    # -> +100% -> "up". Then reverse for "down".
    counts = iter([10, 5, 5, 5, 5])

    def fake_count(sub, start, end):
        return next(counts)

    monkeypatch.setattr("eoa.pipeline.tech_watch.count_in_window", fake_count)
    period_end = dt.datetime(2026, 9, 6, tzinfo=dt.UTC)
    momentum, delta = compute_momentum("event_based", period_end)
    assert momentum == "up"
    assert delta == pytest.approx(100.0)

    counts2 = iter([2, 10, 10, 10, 10])
    monkeypatch.setattr("eoa.pipeline.tech_watch.count_in_window", lambda sub, start, end: next(counts2))
    momentum2, delta2 = compute_momentum("event_based", period_end)
    assert momentum2 == "down"
    assert delta2 == pytest.approx(-80.0)


def test_generate_so_what_skips_llm_call_when_no_items(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_chat_structured(*args, **kwargs):
        nonlocal called
        called = True
        return TechSoWhatOut(so_what_he="should not be reached")

    monkeypatch.setattr("eoa.pipeline.tech_watch.chat_structured", fake_chat_structured)
    result = generate_so_what("FPA עם פיקסל דיגיטלי", [])
    assert result == ""
    assert called is False


def test_generate_so_what_returns_llm_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "eoa.pipeline.tech_watch.chat_structured",
        lambda *a, **k: TechSoWhatOut(so_what_he="להערכתנו, זו התפתחות משמעותית."),
    )
    items = [{"id": 1, "title": "t", "so_what_he": "x", "summary_he": "y"}]
    result = generate_so_what("SWIR/eSWIR", items)
    assert result == "להערכתנו, זו התפתחות משמעותית."


def test_generate_so_what_degrades_to_empty_on_llm_output_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.errors import LLMOutputError

    def raise_error(*args, **kwargs):
        raise LLMOutputError("bad json")

    monkeypatch.setattr("eoa.pipeline.tech_watch.chat_structured", raise_error)
    items = [{"id": 1, "title": "t", "so_what_he": "x", "summary_he": "y"}]
    assert generate_so_what("SWIR/eSWIR", items) == ""


# --------------------------------------------------------------------------
# eoa.report.tech_watch
# --------------------------------------------------------------------------


def test_extend_registry_assigns_continuing_n_and_mutates_in_place() -> None:
    citation_items = [{"id": 1, "n": 1, "title": "existing"}]
    rows = [
        {"id": 2, "title": "new tech item", "url": "https://x.test/2", "source_name": "arXiv"},
        {"id": 1, "title": "existing", "url": "https://x.test/1", "source_name": "arXiv"},
    ]
    result = _extend_registry(citation_items, rows)
    assert result is citation_items
    assert rows[0]["n"] == 2  # new row gets the next n
    assert rows[1]["n"] == 1  # already-registered id reuses its n
    assert len(citation_items) == 2


def test_daily_tech_watch_table_none_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("eoa.report.tech_watch.connection", _fake_connection_factory([]))
    citation_items: list[dict] = []
    table = daily_tech_watch_table(citation_items, dt.datetime(2026, 9, 1), dt.datetime(2026, 9, 2))
    assert table is None
    assert citation_items == []


def test_daily_tech_watch_table_builds_rows_and_extends_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {
            "id": 42,
            "url": "https://arxiv.test/42",
            "title": "Digital-pixel FPA readout",
            "subdomain": "droic_digital_pixel",
            "published_at": dt.datetime(2026, 9, 1),
            "score": 9,
            "trl": "academic",
            "tech_maturity": "lab",
            "tech_actor_kind": "academia",
            "so_what_he": "להערכתנו, חשוב.",
            "summary_he": "תקציר",
            "source_name": "arXiv eess.IV",
        }
    ]
    monkeypatch.setattr("eoa.report.tech_watch.connection", _fake_connection_factory(rows))
    citation_items: list[dict] = []
    table = daily_tech_watch_table(citation_items, dt.datetime(2026, 9, 1), dt.datetime(2026, 9, 2))
    assert table is not None
    assert table["title_he"] == "מעקב טכנולוגי (Technology Watch)"
    assert table["rows"][0][0] == "Digital-pixel FPA readout"
    assert table["rows"][0][-1] == "[1]"  # first citation in an empty registry
    assert citation_items[0]["id"] == 42


def test_weekly_tech_watch_tables_includes_momentum_and_follow_list() -> None:
    aggregates = [
        SubdomainAggregate(
            subdomain="droic_digital_pixel",
            label_he="FPA עם פיקסל דיגיטלי",
            new_count=5,
            notable_items=[
                {"id": 1, "title": "paper A", "url": "https://x.test/1", "source_name": "arXiv", "score": 9},
            ],
            actors=["MIT"],
            momentum="up",
            momentum_delta_pct=42.0,
            so_what_he="להערכתנו, מבטיח.",
        ),
        SubdomainAggregate(
            subdomain="swir_eswir",
            label_he="SWIR/eSWIR",
            new_count=0,
            notable_items=[],
            actors=[],
            momentum="flat",
            momentum_delta_pct=None,
            so_what_he="",
        ),
    ]
    citation_items: list[dict] = []
    tables = weekly_tech_watch_tables(citation_items, aggregates)
    assert len(tables) == 2  # radar matrix + "developments to follow"
    matrix = tables[0]
    assert matrix["rows"] == [["FPA עם פיקסל דיגיטלי", "5", "MIT", "⬆ (+42%)", "להערכתנו, מבטיח."]]
    follow = tables[1]
    assert follow["rows"] == [["paper A", "[1]"]]
    assert citation_items[0]["id"] == 1
