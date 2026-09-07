"""Round 7 follow-up: deep_search jobs carry a populated ``context_he`` from the enqueue site, and
the worker refreshes a blank/stale one from the item row (docs/qa/loop/round_7_fixes.md)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from eoa.pipeline import investigation_context as ic


class _Cursor:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _Conn:
    def __init__(self, row: Any) -> None:
        self.row = row
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        self.queries.append((sql, params))
        return _Cursor(self.row)


def _patch_connection(monkeypatch: pytest.MonkeyPatch, row: Any) -> _Conn:
    conn = _Conn(row)

    @contextmanager
    def _connection(*_a: Any, **_k: Any):
        yield conn

    import eoa.db

    monkeypatch.setattr(eoa.db, "connection", _connection)
    return conn


ITEM_ROW = {
    "title": "AeroVironment wins $464.8M E-HEL production contract",
    "entities_mentioned": ["AeroVironment", "US Army"],
    "summary_he": "חוזה ייצור ראשון למערכת לייזר הגנתית.",
}


class TestFormat:
    def test_lines_match_extract_anchors_shape(self) -> None:
        from eoa.search.deep_search import _ENTITIES_LINE_RE, _TITLE_LINE_RE

        ctx = ic.format_item_context_he(
            ITEM_ROW["title"], ITEM_ROW["entities_mentioned"], ITEM_ROW["summary_he"]
        )
        assert _TITLE_LINE_RE.search(ctx).group(1) == ITEM_ROW["title"]
        assert _ENTITIES_LINE_RE.search(ctx).group(1) == "AeroVironment, US Army"
        assert "תקציר: חוזה ייצור" in ctx
        assert "זרע חיפוש" not in ctx

    def test_seed_line_appended_when_given(self) -> None:
        ctx = ic.format_item_context_he("t", ["A"], "s", "A E-HEL award")
        assert ctx.endswith("זרע חיפוש באנגלית מוצע: A E-HEL award")

    def test_empty_entities_render_dash(self) -> None:
        ctx = ic.format_item_context_he("t", [], "s")
        assert "ישויות: —" in ctx


class TestNeedsRefresh:
    def test_blank_needs_refresh(self) -> None:
        assert ic.context_needs_refresh("") and ic.context_needs_refresh(None)

    def test_dash_entities_needs_refresh(self) -> None:
        assert ic.context_needs_refresh(ic.format_item_context_he("t", [], "s"))

    def test_populated_does_not(self) -> None:
        assert not ic.context_needs_refresh(ic.format_item_context_he("t", ["Anduril"], "s"))


class TestFromDb:
    def test_builds_from_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _patch_connection(monkeypatch, ITEM_ROW)
        ctx = ic.item_context_he_from_db(10)
        assert "AeroVironment, US Army" in ctx and "כותרת הפריט: AeroVironment wins" in ctx
        assert conn.queries[0][1] == (10,)

    def test_tuple_row_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connection(monkeypatch, (ITEM_ROW["title"], ["Anduril"], "s"))
        assert "ישויות: Anduril" in ic.item_context_he_from_db(81)

    def test_missing_item_or_db_error_yields_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connection(monkeypatch, None)
        assert ic.item_context_he_from_db(999) == ""
        assert ic.item_context_he_from_db(None) == ""

        @contextmanager
        def _boom(*_a: Any, **_k: Any):
            raise RuntimeError("db down")
            yield  # pragma: no cover

        import eoa.db

        monkeypatch.setattr(eoa.db, "connection", _boom)
        assert ic.item_context_he_from_db(10) == ""


class TestEnsure:
    def test_blank_payload_context_is_rebuilt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connection(monkeypatch, ITEM_ROW)
        assert "AeroVironment, US Army" in ic.ensure_context_he({"item_id": 10, "question": "q"})

    def test_stale_dash_context_is_rebuilt_keeping_seed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connection(monkeypatch, ITEM_ROW)
        stale = ic.format_item_context_he(ITEM_ROW["title"], [], "s", "AeroVironment E-HEL")
        fresh = ic.ensure_context_he({"item_id": 10, "context_he": stale})
        assert "ישויות: AeroVironment, US Army" in fresh
        assert fresh.endswith("זרע חיפוש באנגלית מוצע: AeroVironment E-HEL")

    def test_complete_context_is_kept_without_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        @contextmanager
        def _boom(*_a: Any, **_k: Any):
            raise AssertionError("must not touch the DB")
            yield  # pragma: no cover

        import eoa.db

        monkeypatch.setattr(eoa.db, "connection", _boom)
        ctx = ic.format_item_context_he("t", ["Anduril"], "s")
        assert ic.ensure_context_he({"item_id": 81, "context_he": ctx}) == ctx

    def test_row_without_entities_keeps_enqueuer_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connection(monkeypatch, {"title": "t", "entities_mentioned": [], "summary_he": "s"})
        stale = ic.format_item_context_he("t", [], "s", "seed")
        assert ic.ensure_context_he({"item_id": 1, "context_he": stale}) == stale


class TestEnqueueSites:
    def test_start_investigation_payload_carries_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.api import services

        _patch_connection(monkeypatch, ITEM_ROW)
        monkeypatch.setattr(services, "_fetchone", lambda *_a, **_k: {"id": 10})
        captured: dict[str, Any] = {}

        def _enqueue(kind: str, payload: dict[str, Any], **kw: Any) -> int:
            captured.update(kind=kind, payload=payload)
            return 123

        monkeypatch.setattr(services.relational, "enqueue_job", _enqueue)
        assert services.start_investigation("מה סטטוס החוזה?", item_id=10) == 123
        assert captured["kind"] == "deep_search"
        assert "ישויות: AeroVironment, US Army" in captured["payload"]["context_he"]

    def test_start_investigation_without_item_has_no_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.api import services

        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            services.relational,
            "enqueue_job",
            lambda kind, payload, **kw: captured.update(payload=payload) or 5,
        )
        assert services.start_investigation("שאלה חופשית", item_id=None) == 5
        assert "context_he" not in captured["payload"]

    def test_triage_enqueue_uses_shared_formatter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.pipeline import triage

        captured: dict[str, Any] = {}
        import eoa.memory.relational as relational

        monkeypatch.setattr(
            relational,
            "enqueue_job",
            lambda kind, payload, **kw: captured.update(kind=kind, payload=payload) or 7,
        )
        monkeypatch.setattr(
            triage,
            "_ensure_valid_investigation_question",
            lambda item, q, seed: (q or "שאלה", seed or "seed en"),
        )
        item = {
            "id": 81,
            "title": "Anduril acquires",
            "entities_mentioned": ["Anduril"],
            "summary_he": "ס",
            "israel_relevance": 0,
        }
        out = type(
            "Out",
            (),
            {
                "deep_search_question": "מה נרכש?",
                "deep_search_seed_en": "Anduril acquisition",
                "level": "red",
            },
        )()
        triage._enqueue_deep_search(item, out)
        ctx = captured["payload"]["context_he"]
        assert "כותרת הפריט: Anduril acquires" in ctx and "ישויות: Anduril" in ctx
        assert ctx.endswith("זרע חיפוש באנגלית מוצע: Anduril acquisition")
