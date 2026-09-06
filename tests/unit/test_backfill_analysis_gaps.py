"""Unit tests for scripts/backfill_analysis_gaps.py (Q3-8/Q3-9/Q3-10, docs/qa/findings_Q3_r2.md).

- Pass 1 (deterministic entities backfill) and pass 3 (stub cleanup) are exercised against a fake
  DB connection -- no live Postgres needed.
- Pass 2 (LLM re-analyze) is exercised only at the resource-gate/error-handling level, with
  ``analyze_item``/``persist_analysis`` monkeypatched -- no real LLM call.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_backfill_analysis_gaps.py -q``
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "backfill_analysis_gaps.py"
_spec = importlib.util.spec_from_file_location("backfill_analysis_gaps", _SCRIPT_PATH)
bag = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(bag)


class _FakeCursor:
    def __init__(self, fetchall_result: list | None = None) -> None:
        self._fetchall = fetchall_result or []
        self.calls: list[tuple[str, dict]] = []

    def execute(self, query: str, params: dict | None = None) -> None:
        self.calls.append((query, params or {}))

    def fetchall(self) -> list:
        return self._fetchall

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class TestDeterministicEntitiesBackfill:
    def test_fills_items_with_watchlist_alias_in_title(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {"id": 1, "title": "Elbit Systems wins new contract", "clean_text": ""},
            {"id": 2, "title": "Nothing relevant here", "clean_text": "still nothing"},
        ]
        cursor = _FakeCursor(fetchall_result=rows)
        from eoa.db import connection as real_connection  # noqa: F401 -- ensure importable

        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConnection(cursor))
        update_calls: list[tuple[int, dict]] = []
        monkeypatch.setattr(
            "eoa.memory.relational.update_item_fields",
            lambda item_id, **fields: update_calls.append((item_id, fields)),
        )

        report = bag.deterministic_entities_backfill(dry_run=False)

        assert report["candidates"] == 2
        assert len(report["filled"]) == 1
        assert report["filled"][0]["id"] == 1
        assert report["filled"][0]["entities_mentioned"] == ["Elbit"]
        assert report["still_empty_after"] == 1
        assert update_calls == [(1, {"entities_mentioned": ["Elbit"]})]

    def test_dry_run_does_not_write(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"id": 1, "title": "Elbit Systems wins new contract", "clean_text": ""}]
        cursor = _FakeCursor(fetchall_result=rows)
        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConnection(cursor))
        update_calls: list[tuple[int, dict]] = []
        monkeypatch.setattr(
            "eoa.memory.relational.update_item_fields",
            lambda item_id, **fields: update_calls.append((item_id, fields)),
        )

        report = bag.deterministic_entities_backfill(dry_run=True)

        assert len(report["filled"]) == 1
        assert update_calls == []


class TestTitleOnlyClassificationDefensible:
    def test_defensible_when_named_entity_and_level_domain_present(self) -> None:
        item = {"title": "Elbit Systems wins contract", "level": "orange", "domain": "contracts"}
        assert bag._title_only_classification_defensible(item) is True

    def test_not_defensible_without_level(self) -> None:
        item = {"title": "Elbit Systems wins contract", "level": None, "domain": "contracts"}
        assert bag._title_only_classification_defensible(item) is False

    def test_not_defensible_without_domain(self) -> None:
        item = {"title": "Elbit Systems wins contract", "level": "orange", "domain": None}
        assert bag._title_only_classification_defensible(item) is False

    def test_not_defensible_without_named_entity_in_title(self) -> None:
        item = {"title": "Some vague headline with nothing recognisable", "level": "orange", "domain": "contracts"}
        assert bag._title_only_classification_defensible(item) is False


class TestStubCleanupPass:
    def test_clears_analysis_fields_and_resets_level_when_not_defensible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"id": 1, "title": "Vague unclear headline", "level": "yellow", "domain": "contracts"}]
        cursor = _FakeCursor(fetchall_result=rows)
        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConnection(cursor))
        update_calls: list[tuple[int, dict]] = []
        monkeypatch.setattr(
            "eoa.memory.relational.update_item_fields",
            lambda item_id, **fields: update_calls.append((item_id, fields)),
        )

        report = bag.stub_cleanup_pass(dry_run=False)

        assert len(report["cleared"]) == 1
        assert report["cleared"][0]["title_only_defensible"] is False
        assert update_calls == [
            (1, {"summary_he": None, "so_what_he": None, "key_facts": None, "level": None, "domain": "out_of_scope"})
        ]

    def test_keeps_level_domain_when_title_defensible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"id": 2, "title": "Elbit Systems announces new program", "level": "orange", "domain": "contracts"}]
        cursor = _FakeCursor(fetchall_result=rows)
        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConnection(cursor))
        update_calls: list[tuple[int, dict]] = []
        monkeypatch.setattr(
            "eoa.memory.relational.update_item_fields",
            lambda item_id, **fields: update_calls.append((item_id, fields)),
        )

        report = bag.stub_cleanup_pass(dry_run=False)

        assert report["cleared"][0]["title_only_defensible"] is True
        assert update_calls == [(2, {"summary_he": None, "so_what_he": None, "key_facts": None})]


class TestLlmReanalyzePass:
    def test_stops_on_resource_unavailable_without_raising(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.errors import ResourceUnavailable

        candidates = [{"id": 1, "level": "red", "title": "a"}, {"id": 2, "level": "red", "title": "b"}]
        monkeypatch.setattr(bag, "_llm_candidates", lambda limit: candidates)
        monkeypatch.setattr("eoa.pipeline.analyze.analyze_item", lambda item, role="resident": (_ for _ in ()).throw(ResourceUnavailable("busy")))
        monkeypatch.setattr("eoa.pipeline.analyze.persist_analysis", lambda item, out: (0, 0))

        report = bag.llm_reanalyze_pass(limit=10, dry_run=False)

        assert report["deferred_resources"] is True
        assert report["done"] == []
        assert report["failed"] == []

    def test_counts_llm_output_error_as_failure_and_continues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.errors import LLMOutputError

        candidates = [{"id": 1, "level": "red", "title": "a"}, {"id": 2, "level": "red", "title": "b"}]
        monkeypatch.setattr(bag, "_llm_candidates", lambda limit: candidates)

        def _analyze(item, role="resident"):
            if item["id"] == 1:
                raise LLMOutputError("bad schema")
            return "OUT"

        monkeypatch.setattr("eoa.pipeline.analyze.analyze_item", _analyze)
        monkeypatch.setattr("eoa.pipeline.analyze.persist_analysis", lambda item, out: (1, 0))

        report = bag.llm_reanalyze_pass(limit=10, dry_run=False)

        assert report["deferred_resources"] is False
        assert [f["id"] for f in report["failed"]] == [1]
        assert [d["id"] for d in report["done"]] == [2]

    def test_dry_run_lists_candidates_without_calling_llm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        candidates = [{"id": 1, "level": "red", "title": "a"}]
        monkeypatch.setattr(bag, "_llm_candidates", lambda limit: candidates)
        called = []
        monkeypatch.setattr("eoa.pipeline.analyze.analyze_item", lambda item, role="resident": called.append(item["id"]))

        report = bag.llm_reanalyze_pass(limit=10, dry_run=True)

        assert called == []
        assert [d["id"] for d in report["done"]] == [1]
