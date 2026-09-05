"""Tests for Q3-6 (docs/qa/findings_Q3_r1.md): events logical-uniqueness upsert and the
narrative-title rejection guard.

- `eoa.memory.relational.insert_event` upserts on (item_id, kind, lower(title)) instead of always
  inserting a new row (db/migrations/versions/0016_events_dedup_unique_index.py adds the backing
  unique index).
- `eoa.pipeline.analyze._is_narrative_event_title` rejects assessment/forecast sentences
  ("השלכות...", "ייתכן...", or a factless, verb-less title) before they ever reach `insert_event`.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_events_dedup.py -q``
"""

from __future__ import annotations

import pytest

from eoa.llm.schemas.analysis import EventOut
from eoa.pipeline.analyze import _is_narrative_event_title, persist_analysis
from eoa.llm.schemas.analysis import AnalyzeOut


class _FakeCursor:
    def __init__(self, row: dict) -> None:
        self._row = row
        self.executed: tuple[str, dict] | None = None

    def execute(self, query: str, params: dict) -> None:
        self.executed = (query, params)

    def fetchone(self) -> dict:
        return self._row

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class TestInsertEventUpsertsOnConflict:
    def test_sql_upserts_on_item_kind_lower_title(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.memory import relational

        cursor = _FakeCursor({"id": 42})
        monkeypatch.setattr(relational, "connection", lambda: _FakeConnection(cursor))

        event_id = relational.insert_event(item_id=70, kind="investment", title="סבב גיוס של 41 מיליון דולר")

        assert event_id == 42
        query, params = cursor.executed
        assert "ON CONFLICT (item_id, kind, (lower(title)))" in query
        assert "DO UPDATE SET" in query
        assert "COALESCE(events.amount_usd, EXCLUDED.amount_usd)" in query
        assert params["item_id"] == 70
        assert params["kind"] == "investment"


class TestNarrativeEventTitleRejection:
    def test_assessment_prefix_rejected(self) -> None:
        ev = EventOut(kind="other", title="השלכות ההשקעה על השוק", summary_he="x", confidence=0.5)
        assert _is_narrative_event_title(ev.title, ev) is True

    def test_expectation_prefix_rejected(self) -> None:
        ev = EventOut(kind="other", title="ייתכן שהחברה תרחיב פעילות", summary_he="x", confidence=0.5)
        assert _is_narrative_event_title(ev.title, ev) is True

    def test_trend_prefix_rejected(self) -> None:
        ev = EventOut(kind="other", title="מגמה של גידול בהשקעות בתחום", summary_he="x", confidence=0.5)
        assert _is_narrative_event_title(ev.title, ev) is True

    def test_factless_verbless_title_rejected(self) -> None:
        ev = EventOut(kind="other", title="שוק התחרות הופך מורכב יותר", summary_he="x", confidence=0.5)
        assert _is_narrative_event_title(ev.title, ev) is True

    def test_occurrence_verb_with_no_anchor_is_accepted(self) -> None:
        """A real occurrence verb is enough even with no party/amount/date attached."""
        ev = EventOut(kind="contract_award", title="החברה חתמה על הסכם חדש", summary_he="x", confidence=0.5)
        assert _is_narrative_event_title(ev.title, ev) is False

    def test_no_verb_but_has_amount_is_accepted(self) -> None:
        ev = EventOut(
            kind="investment", title="סבב גיוס של 41 מיליון דולר", amount_usd=41_000_000, summary_he="x", confidence=0.5
        )
        assert _is_narrative_event_title(ev.title, ev) is False

    def test_no_verb_but_has_party_is_accepted(self) -> None:
        ev = EventOut(kind="partnership", title="שיתוף פעולה בין רפאל לאלביט", parties=["Rafael", "Elbit"], summary_he="x", confidence=0.5)
        assert _is_narrative_event_title(ev.title, ev) is False

    def test_empty_title_not_flagged(self) -> None:
        ev = EventOut(kind="other", title="", summary_he="x", confidence=0.5)
        assert _is_narrative_event_title(ev.title, ev) is False

    def test_ordinary_contract_award_title_accepted(self) -> None:
        ev = EventOut(kind="contract_award", title="Elbit wins pod contract", customer="USAF", summary_he="x", confidence=0.8)
        assert _is_narrative_event_title(ev.title, ev) is False


class TestPersistAnalysisSkipsNarrativeEvents:
    def test_narrative_event_never_reaches_insert_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict] = []
        monkeypatch.setattr("eoa.pipeline.analyze.update_item_fields", lambda *a, **k: None)
        monkeypatch.setattr(
            "eoa.pipeline.analyze.insert_event", lambda **kw: (calls.append(kw), 1)[1]
        )
        monkeypatch.setattr("eoa.pipeline.analyze.upsert_entity", lambda **kw: 1)

        out = AnalyzeOut(
            summary_he="תקציר",
            so_what_he="השלכות",
            key_facts=[],
            events=[
                EventOut(kind="other", title="השלכות ההשקעה על השוק", summary_he="x", confidence=0.5),
                EventOut(kind="contract_award", title="Elbit wins pod contract", customer="USAF", summary_he="x", confidence=0.8),
            ],
            edges=[],
        )
        item = {"id": 1, "title": "t", "url": "https://example.com"}
        n_events, _ = persist_analysis(item, out)

        assert n_events == 1
        assert len(calls) == 1
        assert calls[0]["title"] == "Elbit wins pod contract"
