"""Unit tests for `eoa.pipeline.analyze.persist_analysis`.

Tests the end-to-end persistence of analysis output (summary, key facts,
uncertainty, events, and graph edges) to the relational schema and graph,
verifying that the new fields (key_facts, uncertainty_he, source_name) are
correctly handled in the memory layer.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_persist_analysis.py -q``
"""

from __future__ import annotations

import datetime as dt
import sys
import types

import pytest

# Stub out eoa.db if not available yet, so graph.py's import doesn't fail
if "eoa.db" not in sys.modules:
    try:
        import eoa.db  # noqa: F401
    except ImportError:
        fake_db = types.ModuleType("eoa.db")
        fake_db.connection = lambda: None  # type: ignore[attr-defined]
        fake_db.get_pool = lambda: None  # type: ignore[attr-defined]
        sys.modules["eoa.db"] = fake_db

from eoa.llm.schemas.analysis import AnalyzeOut, EdgeOut, EventOut
from eoa.memory.relational import _ITEM_UPDATABLE_FIELDS, update_item_fields
from eoa.pipeline.analyze import persist_analysis


class RecordingStub:
    """Records function calls for test verification."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, *args, **kwargs) -> int:
        self.calls.append((args, kwargs))
        # Return predictable IDs for entities and events
        return len(self.calls)


def test_updatable_fields_contains_new_columns() -> None:
    """Verify that key_facts, uncertainty_he, source_name are in the allow-list."""
    assert "key_facts" in _ITEM_UPDATABLE_FIELDS
    assert "uncertainty_he" in _ITEM_UPDATABLE_FIELDS
    assert "source_name" in _ITEM_UPDATABLE_FIELDS


def test_update_item_fields_rejects_unknown_fields() -> None:
    """Verify that update_item_fields raises ValueError for unknown fields."""
    with pytest.raises(ValueError, match="cannot update unknown item fields"):
        update_item_fields(1, bogus=1)

    with pytest.raises(ValueError, match="cannot update unknown item fields"):
        update_item_fields(1, title="ok", bogus_field=42)


def test_persist_analysis_updates_item_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that persist_analysis calls update_item_fields with all expected fields."""
    update_stub = RecordingStub()
    insert_event_stub = RecordingStub()
    upsert_entity_stub = RecordingStub()

    monkeypatch.setattr("eoa.pipeline.analyze.update_item_fields", update_stub)
    monkeypatch.setattr("eoa.pipeline.analyze.insert_event", insert_event_stub)
    monkeypatch.setattr("eoa.pipeline.analyze.upsert_entity", upsert_entity_stub)

    # Build an AnalyzeOut with key_facts and uncertainty_he
    out = AnalyzeOut(
        summary_he="תקציר בעברית",
        so_what_he="השלכות עסקיות",
        key_facts=["עובדה 1", "עובדה 2"],
        uncertainty_he="אי-בהירות בנושא X",
        events=[],
        edges=[],
    )

    item = {"id": 42, "title": "Test", "url": "https://example.com"}

    persist_analysis(item, out)

    # Verify update_item_fields was called exactly once with correct args
    assert len(update_stub.calls) == 1
    args, kwargs = update_stub.calls[0]
    assert args == (42,)  # item_id
    assert kwargs["summary_he"] == "תקציר בעברית"
    assert kwargs["so_what_he"] == "השלכות עסקיות"
    assert kwargs["key_facts"] == ["עובדה 1", "עובדה 2"]
    assert kwargs["uncertainty_he"] == "אי-בהירות בנושא X"


def test_persist_analysis_empty_uncertainty_becomes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that empty uncertainty_he is converted to None."""
    update_stub = RecordingStub()

    monkeypatch.setattr("eoa.pipeline.analyze.update_item_fields", update_stub)
    monkeypatch.setattr("eoa.pipeline.analyze.insert_event", RecordingStub())
    monkeypatch.setattr("eoa.pipeline.analyze.upsert_entity", RecordingStub())

    out = AnalyzeOut(
        summary_he="תקציר",
        so_what_he="השלכות",
        key_facts=["עובדה"],
        uncertainty_he="",  # Empty string
        events=[],
        edges=[],
    )

    item = {"id": 1, "title": "Test", "url": "https://example.com"}

    persist_analysis(item, out)

    _, kwargs = update_stub.calls[0]
    assert kwargs["uncertainty_he"] is None


def test_persist_analysis_inserts_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that persist_analysis calls insert_event for each event in the output."""
    insert_event_stub = RecordingStub()

    monkeypatch.setattr("eoa.pipeline.analyze.update_item_fields", RecordingStub())
    monkeypatch.setattr("eoa.pipeline.analyze.insert_event", insert_event_stub)
    monkeypatch.setattr("eoa.pipeline.analyze.upsert_entity", RecordingStub())

    out = AnalyzeOut(
        summary_he="תקציר",
        so_what_he="השלכות",
        key_facts=[],
        events=[
            EventOut(
                kind="contract_award",
                title="계약 수상",
                date="2026-09-01",
                amount_usd=1000000.0,
                currency="USD",
                parties=["Company A", "Company B"],
                customer="Client X",
                program="Program Y",
                summary_he="אירוע בעברית",
                confidence=0.95,
            ),
        ],
        edges=[],
    )

    item = {"id": 100, "title": "Test", "url": "https://example.com"}

    n_events, _n_edges = persist_analysis(item, out)

    assert n_events == 1
    assert len(insert_event_stub.calls) == 1

    args, kwargs = insert_event_stub.calls[0]
    assert args == ()
    assert kwargs["item_id"] == 100
    assert kwargs["kind"] == "contract_award"
    assert kwargs["title"] == "계약 수상"
    assert kwargs["date"] == dt.date(2026, 9, 1)  # _parse_date converts ISO string
    assert kwargs["amount_usd"] == 1000000.0
    assert kwargs["currency"] == "USD"
    assert kwargs["parties"] == ["Company A", "Company B"]
    assert kwargs["customer"] == "Client X"
    assert kwargs["program"] == "Program Y"
    assert kwargs["summary_he"] == "אירוע בעברית"
    assert kwargs["confidence"] == 0.95


def test_persist_analysis_handles_date_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that date strings are correctly parsed."""
    insert_event_stub = RecordingStub()

    monkeypatch.setattr("eoa.pipeline.analyze.update_item_fields", RecordingStub())
    monkeypatch.setattr("eoa.pipeline.analyze.insert_event", insert_event_stub)
    monkeypatch.setattr("eoa.pipeline.analyze.upsert_entity", RecordingStub())

    out = AnalyzeOut(
        summary_he="תקציר",
        so_what_he="השלכות",
        key_facts=[],
        events=[
            EventOut(
                kind="test",
                title="Test",
                date="2025-12-25",
                summary_he="test",
                confidence=0.8,
            ),
            EventOut(
                kind="test",
                title="Test",
                date=None,  # No date
                summary_he="test",
                confidence=0.8,
            ),
            EventOut(
                kind="test",
                title="Test",
                date="invalid-date",  # Unparseable date
                summary_he="test",
                confidence=0.8,
            ),
        ],
        edges=[],
    )

    item = {"id": 200, "title": "Test", "url": "https://example.com"}

    persist_analysis(item, out)

    assert len(insert_event_stub.calls) == 3

    # First event: valid date
    _, kwargs1 = insert_event_stub.calls[0]
    assert kwargs1["date"] == dt.date(2025, 12, 25)

    # Second event: None date
    _, kwargs2 = insert_event_stub.calls[1]
    assert kwargs2["date"] is None

    # Third event: unparseable date -> None
    _, kwargs3 = insert_event_stub.calls[2]
    assert kwargs3["date"] is None


def test_persist_analysis_creates_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that persist_analysis creates entities and edges from EdgeOut."""
    upsert_entity_stub = RecordingStub()
    merge_entity_stub = RecordingStub()
    add_edge_stub = RecordingStub()

    monkeypatch.setattr("eoa.pipeline.analyze.update_item_fields", RecordingStub())
    monkeypatch.setattr("eoa.pipeline.analyze.insert_event", RecordingStub())
    monkeypatch.setattr("eoa.pipeline.analyze.upsert_entity", upsert_entity_stub)

    # Stub the graph module functions
    fake_graph = types.ModuleType("eoa.memory.graph")
    fake_graph.merge_entity = merge_entity_stub  # type: ignore[attr-defined]
    fake_graph.add_edge = add_edge_stub  # type: ignore[attr-defined]
    sys.modules["eoa.memory.graph"] = fake_graph

    out = AnalyzeOut(
        summary_he="תקציר",
        so_what_he="השלכות",
        key_facts=[],
        events=[],
        edges=[
            EdgeOut(
                src="Elbit Systems",
                dst="Israel Aerospace Industries",
                label="PARTNER_OF",
                evidence_he="הם שותפים בפרויקט משותף",
            ),
        ],
    )

    item = {"id": 300, "title": "Test", "url": "https://example.com"}

    _n_events, n_edges = persist_analysis(item, out)

    assert n_edges == 1

    # upsert_entity should be called twice: once for src, once for dst
    assert len(upsert_entity_stub.calls) == 2

    # First upsert_entity call (src)
    _, kwargs1 = upsert_entity_stub.calls[0]
    assert kwargs1["name"] == "Elbit Systems"
    assert kwargs1["kind"] == "company"
    assert kwargs1["first_seen_item"] == 300

    # Second upsert_entity call (dst)
    _, kwargs2 = upsert_entity_stub.calls[1]
    assert kwargs2["name"] == "Israel Aerospace Industries"
    assert kwargs2["kind"] == "company"
    assert kwargs2["first_seen_item"] == 300

    # merge_entity should be called twice
    assert len(merge_entity_stub.calls) == 2
    assert merge_entity_stub.calls[0][0] == (1, "Elbit Systems", "company", None)
    assert merge_entity_stub.calls[1][0] == (2, "Israel Aerospace Industries", "company", None)

    # add_edge should be called once
    assert len(add_edge_stub.calls) == 1
    args, kwargs = add_edge_stub.calls[0]
    assert args == (1, 2, "PARTNER_OF", 300, {"evidence": "הם שותפים בפרויקט משותף"})


def test_persist_analysis_truncates_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that evidence_he is truncated to 300 chars."""
    add_edge_stub = RecordingStub()

    monkeypatch.setattr("eoa.pipeline.analyze.update_item_fields", RecordingStub())
    monkeypatch.setattr("eoa.pipeline.analyze.insert_event", RecordingStub())
    monkeypatch.setattr("eoa.pipeline.analyze.upsert_entity", RecordingStub())

    # Stub graph module
    fake_graph = types.ModuleType("eoa.memory.graph")
    fake_graph.merge_entity = RecordingStub()  # type: ignore[attr-defined]
    fake_graph.add_edge = add_edge_stub  # type: ignore[attr-defined]
    sys.modules["eoa.memory.graph"] = fake_graph

    long_evidence = "א" * 500  # Very long evidence text

    out = AnalyzeOut(
        summary_he="תקציר",
        so_what_he="השלכות",
        key_facts=[],
        events=[],
        edges=[
            EdgeOut(
                src="A",
                dst="B",
                label="PARTNER_OF",
                evidence_he=long_evidence,
            ),
        ],
    )

    item = {"id": 400, "title": "Test", "url": "https://example.com"}

    persist_analysis(item, out)

    args, _kwargs = add_edge_stub.calls[0]
    # add_edge is called with: (src_id, dst_id, label, item_id, {"evidence": ...})
    evidence_dict = args[4]
    assert len(evidence_dict["evidence"]) == 300
    assert evidence_dict["evidence"] == long_evidence[:300]


def test_persist_analysis_returns_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that persist_analysis returns correct event and edge counts."""
    monkeypatch.setattr("eoa.pipeline.analyze.update_item_fields", RecordingStub())
    monkeypatch.setattr("eoa.pipeline.analyze.insert_event", RecordingStub())
    monkeypatch.setattr("eoa.pipeline.analyze.upsert_entity", RecordingStub())

    # Stub graph module
    fake_graph = types.ModuleType("eoa.memory.graph")
    fake_graph.merge_entity = RecordingStub()  # type: ignore[attr-defined]
    fake_graph.add_edge = RecordingStub()  # type: ignore[attr-defined]
    sys.modules["eoa.memory.graph"] = fake_graph

    out = AnalyzeOut(
        summary_he="תקציר",
        so_what_he="השלכות",
        key_facts=["עובדה 1", "עובדה 2"],
        events=[
            EventOut(kind="test", title="e1", summary_he="e1", confidence=0.8),
            EventOut(kind="test", title="e2", summary_he="e2", confidence=0.8),
            EventOut(kind="test", title="e3", summary_he="e3", confidence=0.8),
        ],
        edges=[
            EdgeOut(src="A", dst="B", label="PARTNER_OF", evidence_he="test"),
            EdgeOut(src="C", dst="D", label="COMPETITOR_OF", evidence_he="test"),
        ],
    )

    item = {"id": 500, "title": "Test", "url": "https://example.com"}

    n_events, n_edges = persist_analysis(item, out)

    assert n_events == 3
    assert n_edges == 2
