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
from eoa.pipeline.analyze import _heuristic_kind, _resolve_edge_kinds, persist_analysis


class _FakeCursor:
    """Minimal stand-in for a psycopg cursor's `conn.execute(...)` result."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict]:
        return self._rows


class _FakeConnection:
    """Minimal stand-in for `eoa.db.connection()`'s context-managed connection.

    Records the query it was asked to run and always answers with the rows
    given at construction time, regardless of the actual WHERE-clause names
    -- enough to test that `_resolve_edge_kinds` uses whatever `entities`
    already has on record instead of the heuristic.
    """

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.executed: tuple | None = None

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, query: str, params: tuple | None = None) -> _FakeCursor:
        self.executed = (query, params)
        return _FakeCursor(self._rows)


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
        # Each event carries a distinct `program` so persist_analysis's F9/F16 dedup (kind +
        # normalised parties/customer/program) doesn't collapse these three into one — they are
        # deliberately otherwise-identical here to isolate the date-parsing behavior under test.
        events=[
            EventOut(
                kind="test",
                title="Test",
                date="2025-12-25",
                program="Program A",
                summary_he="test",
                confidence=0.8,
            ),
            EventOut(
                kind="test",
                title="Test",
                date=None,  # No date
                program="Program B",
                customer="Client B",  # anchor so Q3-6's narrative-title filter doesn't reject it
                summary_he="test",
                confidence=0.8,
            ),
            EventOut(
                kind="test",
                title="Test",
                date="invalid-date",  # Unparseable date
                program="Program C",
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


def test_persist_analysis_dedups_identical_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """F9/F16: two events sharing (kind, normalised parties/customer/program) within the same
    item's extraction collapse to one insert -- the richer of the two (more populated fields) is
    kept."""
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
                title="Elbit wins pod contract",
                customer="USAF",
                parties=["Elbit Systems"],
                summary_he="חוזה",
                confidence=0.8,
            ),
            EventOut(
                kind="contract_award",
                title="Elbit wins pod contract",
                customer="usaf",  # same customer, different case -- still a duplicate
                parties=["elbit systems"],
                amount_usd=80_000_000.0,  # the richer duplicate: also carries an amount
                summary_he="חוזה בהיקף 80 מיליון דולר",
                confidence=0.85,
            ),
            EventOut(
                kind="launch",  # different kind -- not a duplicate of the two above
                title="Rafael launches C-UAS system",
                parties=["Rafael"],
                summary_he="השקה",
                confidence=0.7,
            ),
        ],
        edges=[],
    )

    item = {"id": 201, "title": "Test", "url": "https://example.com"}
    n_events, _ = persist_analysis(item, out)

    assert n_events == 2
    assert len(insert_event_stub.calls) == 2
    contract_calls = [kw for _, kw in insert_event_stub.calls if kw["kind"] == "contract_award"]
    assert len(contract_calls) == 1
    assert contract_calls[0]["amount_usd"] == 80_000_000.0  # the richer duplicate won


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

    # upsert_entity should be called twice: once for src, once for dst -- Q3-13 r3 (docs/qa/
    # findings_Q3_r2.md): with the *canonical* watchlist name ("Elbit Systems" -> "Elbit",
    # "Israel Aerospace Industries" -> "IAI"), not the raw as-extracted alias.
    assert len(upsert_entity_stub.calls) == 2

    # First upsert_entity call (src)
    _, kwargs1 = upsert_entity_stub.calls[0]
    assert kwargs1["name"] == "Elbit"
    assert kwargs1["kind"] == "company"
    assert kwargs1["first_seen_item"] == 300

    # Second upsert_entity call (dst)
    _, kwargs2 = upsert_entity_stub.calls[1]
    assert kwargs2["name"] == "IAI"
    assert kwargs2["kind"] == "company"
    assert kwargs2["first_seen_item"] == 300

    # merge_entity should be called twice, with the same canonical names -- regression for the
    # bug where merge_entity's raw `UPDATE entities SET name = ...` was called with the
    # *uncanonicalised* alias, silently renaming the row back and undoing the deduplication.
    assert len(merge_entity_stub.calls) == 2
    assert merge_entity_stub.calls[0][0] == (1, "Elbit", "company", None)
    assert merge_entity_stub.calls[1][0] == (2, "IAI", "company", None)

    # add_edge should be called once
    assert len(add_edge_stub.calls) == 1
    args, _kwargs = add_edge_stub.calls[0]
    assert args == (1, 2, "PARTNER_OF", 300, {"evidence": "הם שותפים בפרויקט משותף"})


def test_persist_analysis_merge_entity_never_reverts_canonicalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for Q3-13 r3 (docs/qa/findings_Q3_r2.md): an edge naming a Hebrew/alias spelling
    of a curated-org entity ("צבא ארה\"ב" for US Army) must have `merge_entity` called with the
    *canonical* name, not the raw extracted spelling -- otherwise `merge_entity`'s raw `UPDATE
    entities SET name = ...` silently renames the already-canonicalised row back, causing a fresh
    duplicate the next time some other item's extraction spells it "US Army" again."""
    upsert_entity_stub = RecordingStub()
    merge_entity_stub = RecordingStub()
    add_edge_stub = RecordingStub()

    monkeypatch.setattr("eoa.pipeline.analyze.update_item_fields", RecordingStub())
    monkeypatch.setattr("eoa.pipeline.analyze.insert_event", RecordingStub())
    monkeypatch.setattr("eoa.pipeline.analyze.upsert_entity", upsert_entity_stub)
    monkeypatch.setattr("eoa.db.connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))

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
            EdgeOut(src='צבא ארה"ב', dst="General Atomics", label="BIDS_AGAINST", evidence_he="x"),
        ],
    )
    item = {"id": 700, "title": "Test", "url": "https://example.com"}

    persist_analysis(item, out)

    _, kwargs_src = upsert_entity_stub.calls[0]
    assert kwargs_src["name"] == "US Army"
    assert merge_entity_stub.calls[0][0] == (1, "US Army", "org", None)


def test_heuristic_kind_org_program_company() -> None:
    """`_heuristic_kind` -- the fallback used when an entity is genuinely new.

    Regression for the bug where every edge endpoint (e.g. "Air Force")
    was upserted with kind="company" regardless of what it actually was.
    """
    assert _heuristic_kind("US Air Force") == "org"
    assert _heuristic_kind("Israeli Air Force Command") == "org"
    assert _heuristic_kind("Ministry of Defense") == "org"
    assert _heuristic_kind("NATO") == "org"
    assert _heuristic_kind("Department of the Navy") == "org"
    assert _heuristic_kind("Collaborative Combat Aircraft Program") == "program"
    assert _heuristic_kind("Falcon Project") == "program"
    assert _heuristic_kind("Elbit Systems") == "company"
    assert _heuristic_kind("RTX") == "company"


def test_resolve_edge_kinds_falls_back_to_heuristic_when_db_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `entities` row on record (or DB unreachable) -> heuristic decides."""
    monkeypatch.setattr("eoa.db.connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))

    kinds = _resolve_edge_kinds(["Aeropolis Air Force", "Blue Horizon Program", "Acme Corp"])

    assert kinds == {
        "Aeropolis Air Force": "org",
        "Blue Horizon Program": "program",
        "Acme Corp": "company",
    }


def test_resolve_edge_kinds_prefers_existing_db_kind_over_heuristic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An entity already on record keeps its recorded kind, even when the
    heuristic would have guessed something else -- the "never overwrite a
    better guess with a worse one" rule."""
    fake_conn = _FakeConnection([{"name": "Aeropolis Air Force", "kind": "system"}])
    monkeypatch.setattr("eoa.db.connection", lambda: fake_conn)

    kinds = _resolve_edge_kinds(["Aeropolis Air Force", "New Startup Inc"])

    # "system" (already on record) wins over the "org" heuristic guess.
    assert kinds["Aeropolis Air Force"] == "system"
    # Names with no existing row still fall back to the heuristic.
    assert kinds["New Startup Inc"] == "company"


def test_resolve_edge_kinds_empty_names_returns_empty_without_db_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> None:
        raise AssertionError("connection() should not be called for an empty name list")

    monkeypatch.setattr("eoa.db.connection", fail)
    assert _resolve_edge_kinds([]) == {}


def test_persist_analysis_resolves_org_and_program_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: an edge naming a military body and a program gets the
    right `kind` on both `upsert_entity` and `merge_entity`, not the old
    hardcoded "company" for everything."""
    upsert_entity_stub = RecordingStub()
    merge_entity_stub = RecordingStub()
    add_edge_stub = RecordingStub()

    monkeypatch.setattr("eoa.pipeline.analyze.update_item_fields", RecordingStub())
    monkeypatch.setattr("eoa.pipeline.analyze.insert_event", RecordingStub())
    monkeypatch.setattr("eoa.pipeline.analyze.upsert_entity", upsert_entity_stub)
    # No entities on record for these fictional names -> heuristic decides.
    monkeypatch.setattr("eoa.db.connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))

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
                src="Aeropolis Air Force",
                dst="Blue Horizon Program",
                label="BIDS_AGAINST",
                evidence_he="חיל האוויר מתמודד על פרויקט",
            ),
        ],
    )

    item = {"id": 600, "title": "Test", "url": "https://example.com"}

    _n_events, n_edges = persist_analysis(item, out)

    assert n_edges == 1
    _, kwargs_src = upsert_entity_stub.calls[0]
    assert kwargs_src["name"] == "Aeropolis Air Force"
    assert kwargs_src["kind"] == "org"
    _, kwargs_dst = upsert_entity_stub.calls[1]
    assert kwargs_dst["name"] == "Blue Horizon Program"
    assert kwargs_dst["kind"] == "program"

    assert merge_entity_stub.calls[0][0] == (1, "Aeropolis Air Force", "org", None)
    assert merge_entity_stub.calls[1][0] == (2, "Blue Horizon Program", "program", None)


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
        # Distinct `program` per event so F9/F16 dedup doesn't collapse these three (otherwise
        # identical) events into one -- this test is about the events/edges counts, not dedup.
        # `customer` gives each event an anchor so Q3-6's narrative-title filter doesn't reject
        # these factless placeholder titles -- this test is about counts, not that filter.
        events=[
            EventOut(
                kind="test",
                title="e1",
                program="Program A",
                customer="Client",
                summary_he="e1",
                confidence=0.8,
            ),
            EventOut(
                kind="test",
                title="e2",
                program="Program B",
                customer="Client",
                summary_he="e2",
                confidence=0.8,
            ),
            EventOut(
                kind="test",
                title="e3",
                program="Program C",
                customer="Client",
                summary_he="e3",
                confidence=0.8,
            ),
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
