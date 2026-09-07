"""Round-6 entities-repair tests ("R6-entities", follow-up to R6-data --
docs/qa/loop/round_6_fixes.md's "R6-data status" -- covering the R6-entities brief: entities
mentioned only by out-of-scope/archived items pollute the `entities` table and the monthly's
"ישויות חדשות החודש" list).

No DB: every ``eoa.db.connection`` (or ``scripts.repair_round6.connection``) call is monkeypatched
with a fake cursor/connection pair, mirroring ``tests/unit/test_round6_data.py`` and
``tests/unit/test_upsert_entity_normalization.py``.

Run with:
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_round6_entities.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import scripts.repair_round6 as repair_mod

from eoa.memory import relational
from eoa.pipeline import analyze as analyze_mod

# ---------------------------------------------------------------------------
# fake DB plumbing (no real connection ever opens) -- mirrors test_round6_data.py
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, responder, recorder):
        self._responder = responder
        self._recorder = recorder
        self._rows: list = []

    def execute(self, sql, params=None):
        params = params or {}
        sql_text = sql.as_string(None) if hasattr(sql, "as_string") else sql
        self._recorder.append((sql_text, params))
        self._rows = self._responder(sql_text, params) or []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    @property
    def rowcount(self):
        return len(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, responder, recorder):
        self._responder = responder
        self._recorder = recorder

    def cursor(self, row_factory=None):
        return _FakeCursor(self._responder, self._recorder)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _connection_stub(responder, recorder):
    def _factory():
        return _FakeConn(responder, recorder)

    return _factory


def _sql_router(table_rows: dict[str, list]):
    def _respond(sql, params):
        for key, rows in table_rows.items():
            if key in sql:
                return rows
        return []

    return _respond


# ---------------------------------------------------------------------------
# task 2 -- eoa.pipeline.analyze._entity_persistence_allowed (D3/D9)
# ---------------------------------------------------------------------------


def test_entity_persistence_allowed_when_domain_and_level_unset():
    assert analyze_mod._entity_persistence_allowed({"id": 1}) is True


def test_entity_persistence_blocked_for_out_of_scope_domain():
    assert analyze_mod._entity_persistence_allowed({"id": 1, "domain": "out_of_scope"}) is False


def test_entity_persistence_blocked_for_archive_level():
    assert (
        analyze_mod._entity_persistence_allowed({"id": 1, "domain": "air_defense", "level": "archive"})
        is False
    )


def test_entity_persistence_allowed_for_in_scope_domain_and_level():
    assert analyze_mod._entity_persistence_allowed({"id": 1, "domain": "air_defense", "level": "red"}) is True


# ---------------------------------------------------------------------------
# task 3 -- eoa.pipeline.analyze.is_junk_candidate_entity_name (D9)
# ---------------------------------------------------------------------------


def test_junk_candidate_platform_designation_plural():
    assert analyze_mod.is_junk_candidate_entity_name("F-16s") is True
    assert analyze_mod.is_junk_candidate_entity_name("M1s") is True
    assert analyze_mod.is_junk_candidate_entity_name("AK47s") is True


def test_junk_candidate_platform_designation_singular_not_junk():
    # No trailing "s" -- a real single designation, not the plural shape this filter targets.
    assert analyze_mod.is_junk_candidate_entity_name("F-16") is False


def test_junk_candidate_generic_two_word_stoplist_case_insensitive():
    assert analyze_mod.is_junk_candidate_entity_name("Western Partners") is True
    assert analyze_mod.is_junk_candidate_entity_name("western partners") is True
    assert analyze_mod.is_junk_candidate_entity_name("Defense Officials") is True
    assert analyze_mod.is_junk_candidate_entity_name("Government Officials") is True


def test_junk_candidate_wildlife_word_only_junk_for_company_kind():
    assert analyze_mod.is_junk_candidate_entity_name("Western Burrowing Owl", kind="company") is True
    # Same wildlife word, different kind (e.g. a genuine conservation program) -- not junk.
    assert analyze_mod.is_junk_candidate_entity_name("Western Burrowing Owl", kind="program") is False
    assert analyze_mod.is_junk_candidate_entity_name("Western Burrowing Owl") is False


def test_junk_candidate_real_entities_not_flagged():
    assert analyze_mod.is_junk_candidate_entity_name("AIM-120 AMRAAM") is False
    assert analyze_mod.is_junk_candidate_entity_name("NASAMS") is False
    assert analyze_mod.is_junk_candidate_entity_name("Elbit", kind="company") is False
    assert analyze_mod.is_junk_candidate_entity_name(None) is False
    assert analyze_mod.is_junk_candidate_entity_name("") is False


# ---------------------------------------------------------------------------
# task 2 -- eoa.pipeline.analyze._entities_from_persisted_events (D3)
# ---------------------------------------------------------------------------


def test_entities_from_persisted_events_dedupes_order_preserving():
    names = analyze_mod._entities_from_persisted_events(
        ["TC-Next", "GraphCast", "TC-Next"], ["GraphCast", "AIM-120 AMRAAM"]
    )
    assert names == ["TC-Next", "GraphCast", "AIM-120 AMRAAM"]


def test_entities_from_persisted_events_empty_inputs():
    assert analyze_mod._entities_from_persisted_events([], []) == []


# ---------------------------------------------------------------------------
# task 2 -- eoa.pipeline.analyze.persist_analysis entity-persistence guard (integration)
# ---------------------------------------------------------------------------

from eoa.llm.schemas.analysis import AnalyzeOut, EdgeOut, EventOut  # noqa: E402


def _out(**overrides) -> AnalyzeOut:
    base = {"summary_he": "תקציר", "so_what_he": "השלכות", "key_facts": [], "events": [], "edges": []}
    base.update(overrides)
    return AnalyzeOut(**base)


def test_persist_analysis_skips_entities_mentioned_write_for_archived_item(monkeypatch):
    update_calls = []
    monkeypatch.setattr(analyze_mod, "update_item_fields", lambda item_id, **kw: update_calls.append(kw))
    monkeypatch.setattr(analyze_mod, "insert_event", lambda **kw: None)
    monkeypatch.setattr(
        analyze_mod, "upsert_entity", lambda **kw: (_ for _ in ()).throw(AssertionError("must not upsert"))
    )
    # item already carries entities_mentioned=["Ukraine"] -> the A13 relevance-scoring block's
    # per-entity loop would otherwise call the real (DB-touching) score_and_persist_entity_israeli;
    # stubbed here so this test stays DB-free and focused on the entity-persistence guard.
    monkeypatch.setattr("eoa.pipeline.israel_focus.score_and_persist_entity_israeli", lambda name: None)

    item = {"id": 22, "domain": "air_defense", "level": "archive", "entities_mentioned": ["Ukraine"]}
    out = _out(edges=[EdgeOut(src="A", dst="B", label="PARTNER_OF", evidence_he="x")])

    persist_analysis = analyze_mod.persist_analysis
    _n_events, n_edges = persist_analysis(item, out)

    assert n_edges == 0
    # First (and only) update_item_fields call must never carry entities_mentioned.
    assert all("entities_mentioned" not in kw for kw in update_calls)
    # Summary/so_what are unaffected by the entity-persistence guard.
    assert update_calls[0]["summary_he"] == "תקציר"
    assert update_calls[0]["so_what_he"] == "השלכות"


def test_persist_analysis_still_creates_edges_for_in_scope_item(monkeypatch):
    import sys
    import types

    upsert_calls = []
    monkeypatch.setattr(analyze_mod, "update_item_fields", lambda item_id, **kw: None)
    monkeypatch.setattr(analyze_mod, "insert_event", lambda **kw: None)
    monkeypatch.setattr(
        analyze_mod, "upsert_entity", lambda **kw: upsert_calls.append(kw) or len(upsert_calls)
    )

    fake_graph = types.ModuleType("eoa.memory.graph")
    fake_graph.merge_entity = lambda *a, **k: None
    fake_graph.add_edge = lambda *a, **k: None
    sys.modules["eoa.memory.graph"] = fake_graph

    item = {"id": 300, "domain": "air_defense", "level": "red"}
    out = _out(edges=[EdgeOut(src="Elbit", dst="IAI", label="PARTNER_OF", evidence_he="x")])

    _n_events, n_edges = analyze_mod.persist_analysis(item, out)

    assert n_edges == 1
    assert len(upsert_calls) == 2


def test_persist_analysis_backfills_entities_from_events_when_still_empty(monkeypatch):
    update_calls = []
    monkeypatch.setattr(analyze_mod, "update_item_fields", lambda item_id, **kw: update_calls.append(kw))
    monkeypatch.setattr(analyze_mod, "insert_event", lambda **kw: None)
    monkeypatch.setattr(analyze_mod, "upsert_entity", lambda **kw: None)

    item = {"id": 153, "domain": "tech_dev", "level": "orange", "entities_mentioned": []}
    out = _out(
        events=[
            EventOut(
                kind="test",
                title="ניסוי מערכת",
                parties=["TC-Next", "WeatherNext"],
                customer="NOAA",
                summary_he="e",
                confidence=0.8,
            )
        ]
    )

    analyze_mod.persist_analysis(item, out)

    # Second update_item_fields call is the events-fallback backfill.
    assert any(kw.get("entities_mentioned") == ["TC-Next", "WeatherNext"] for kw in update_calls)


def test_persist_analysis_events_fallback_filters_junk_names(monkeypatch):
    update_calls = []
    monkeypatch.setattr(analyze_mod, "update_item_fields", lambda item_id, **kw: update_calls.append(kw))
    monkeypatch.setattr(analyze_mod, "insert_event", lambda **kw: None)
    monkeypatch.setattr(analyze_mod, "upsert_entity", lambda **kw: None)

    item = {"id": 22, "domain": "air_defense", "level": "orange", "entities_mentioned": []}
    out = _out(
        events=[
            EventOut(
                kind="deployment",
                title="פריסת כוחות",
                parties=["Ukraine", "F-16s", "Western Partners"],
                customer="NATO",
                summary_he="e",
                confidence=0.8,
            )
        ]
    )

    analyze_mod.persist_analysis(item, out)

    backfill_calls = [kw for kw in update_calls if "entities_mentioned" in kw]
    assert len(backfill_calls) == 1
    assert backfill_calls[0]["entities_mentioned"] == ["Ukraine"]


def test_persist_analysis_events_fallback_skipped_when_entities_already_present(monkeypatch):
    update_calls = []
    monkeypatch.setattr(analyze_mod, "update_item_fields", lambda item_id, **kw: update_calls.append(kw))
    monkeypatch.setattr(analyze_mod, "insert_event", lambda **kw: None)
    monkeypatch.setattr(analyze_mod, "upsert_entity", lambda **kw: None)
    # entities_mentioned=["Elbit"] is non-empty -> stub the A13 block's real per-entity scoring
    # call so this test stays DB-free (see the note in the archived-item test above).
    monkeypatch.setattr("eoa.pipeline.israel_focus.score_and_persist_entity_israeli", lambda name: None)

    item = {"id": 1, "domain": "air_defense", "level": "orange", "entities_mentioned": ["Elbit"]}
    out = _out(
        events=[
            EventOut(kind="test", title="t", parties=["Rafael"], customer="c", summary_he="e", confidence=0.8)
        ]
    )

    analyze_mod.persist_analysis(item, out)

    assert all("entities_mentioned" not in kw for kw in update_calls)


def test_persist_analysis_events_fallback_skipped_for_out_of_scope_item(monkeypatch):
    update_calls = []
    monkeypatch.setattr(analyze_mod, "update_item_fields", lambda item_id, **kw: update_calls.append(kw))
    monkeypatch.setattr(analyze_mod, "insert_event", lambda **kw: None)
    monkeypatch.setattr(analyze_mod, "upsert_entity", lambda **kw: None)

    item = {"id": 2463, "domain": "out_of_scope", "level": "archive", "entities_mentioned": []}
    out = _out(
        events=[
            EventOut(
                kind="regulation",
                title="t",
                parties=["Western Burrowing Owl"],
                customer="c",
                summary_he="e",
                confidence=0.8,
            )
        ]
    )

    analyze_mod.persist_analysis(item, out)

    assert all("entities_mentioned" not in kw for kw in update_calls)


# ---------------------------------------------------------------------------
# task 3 -- eoa.memory.relational upsert_entity junk-shape guard (D9)
# ---------------------------------------------------------------------------


class _UpsertCursor:
    def __init__(self, responses):
        self._responses = list(responses)
        self.queries: list[tuple] = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params or {}))

    def fetchone(self):
        return self._responses.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _UpsertConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_upsert_entity_rejects_platform_designation_plural_without_db_call(monkeypatch):
    cursor = _UpsertCursor([])
    monkeypatch.setattr(relational, "connection", lambda: _UpsertConn(cursor))

    result = relational.upsert_entity(name="F-16s", kind="company")

    assert result is None
    assert cursor.queries == []


def test_upsert_entity_rejects_stoplist_phrase_without_db_call(monkeypatch):
    cursor = _UpsertCursor([])
    monkeypatch.setattr(relational, "connection", lambda: _UpsertConn(cursor))

    result = relational.upsert_entity(name="Western Partners", kind="company")

    assert result is None
    assert cursor.queries == []


def test_upsert_entity_rejects_wildlife_company_without_db_call(monkeypatch):
    cursor = _UpsertCursor([])
    monkeypatch.setattr(relational, "connection", lambda: _UpsertConn(cursor))

    result = relational.upsert_entity(name="Western Burrowing Owl", kind="company")

    assert result is None
    assert cursor.queries == []


def test_upsert_entity_allows_wildlife_word_for_non_company_kind(monkeypatch):
    # first fetchone(): case-insensitive lookup (no match); second: the INSERT ... RETURNING id
    cursor = _UpsertCursor([None, {"id": 9}])
    monkeypatch.setattr(relational, "connection", lambda: _UpsertConn(cursor))

    result = relational.upsert_entity(name="Habitat Restoration Program", kind="program")

    assert result == 9


# ---------------------------------------------------------------------------
# task 1 -- scripts.repair_round6._watchlist_protected_names (D9)
# ---------------------------------------------------------------------------


def test_watchlist_protected_names_covers_every_declared_source(monkeypatch, tmp_path):
    import types

    fake_watchlist = {
        "companies": [{"name": "Safran", "aliases": ["Safran Electronics"], "strict_aliases": ["SAF"]}],
        "programs": [{"name": "Replicator", "aliases": ["Repl"]}],
        "agencies": [{"name": "משרד הביטחון", "aliases": ["IMOD"]}],
        "acquisition_watch": [{"name": "PVP Advanced EO Systems", "peers_of": ["Opgal", "SCD"]}],
    }
    monkeypatch.setattr(repair_mod, "settings", lambda: types.SimpleNamespace(watchlist=fake_watchlist))
    payloads_file = tmp_path / "payloads_seed.yaml"
    payloads_file.write_text(
        "payloads:\n  - canonical_name: X\n    vendor_entity_name: 'Northrop Grumman'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(repair_mod, "CONFIG_DIR", tmp_path)

    names = repair_mod._watchlist_protected_names()

    from eoa.pipeline.entity_normalize import normalize_name_key

    for expected in [
        "Safran",
        "Safran Electronics",
        "SAF",
        "Replicator",
        "Repl",
        "IMOD",
        "Opgal",
        "SCD",
        "Northrop Grumman",
    ]:
        assert normalize_name_key(expected) in names


# ---------------------------------------------------------------------------
# task 1 -- scripts.repair_round6._protection_reason (D9)
# ---------------------------------------------------------------------------


def test_protection_reason_watchlist_match():
    from eoa.pipeline.entity_normalize import normalize_name_key

    row = {"id": 1, "name": "Safran", "kind": "company", "country": "EU", "has_in_scope_mention": False}
    reason = repair_mod._protection_reason(row, {normalize_name_key("Safran")}, "")
    assert reason == "watchlist_or_payloads_vendor"


def test_protection_reason_report_state_reference():
    row = {"id": 2, "name": "Hezbollah", "kind": "org", "country": None, "has_in_scope_mention": False}
    reports_blob = "מגמה: פעילות מוגברת סביב Hezbollah בתחום out_of_scope"
    reason = repair_mod._protection_reason(row, set(), reports_blob)
    assert reason == "referenced_by_report_state"


def test_protection_reason_none_when_no_match():
    row = {"id": 3, "name": "Airbus", "kind": "company", "country": "EU", "has_in_scope_mention": False}
    reason = repair_mod._protection_reason(row, set(), "no mentions here")
    assert reason is None


def test_protection_reason_never_fires_for_country_rule_without_in_scope_mention():
    # By construction of find_out_of_scope_only_entities, has_in_scope_mention is always False --
    # confirms the (b) rule alone (kind+country, no in-scope mention) never protects a candidate.
    row = {"id": 4, "name": "Honeywell", "kind": "company", "country": "US", "has_in_scope_mention": False}
    reason = repair_mod._protection_reason(row, set(), "")
    assert reason is None


# ---------------------------------------------------------------------------
# task 1 -- scripts.repair_round6.find_out_of_scope_only_entities / find_junk_shaped_entities
# ---------------------------------------------------------------------------


def test_find_out_of_scope_only_entities_query_shape(monkeypatch):
    rows = [{"id": 1, "name": "Western Burrowing Owl", "kind": "company", "country": None}]
    recorder: list = []
    monkeypatch.setattr(
        repair_mod, "connection", _connection_stub(_sql_router({"FROM entities": rows}), recorder)
    )

    found = repair_mod.find_out_of_scope_only_entities()

    assert found == rows
    sql_text, _params = recorder[-1]
    assert "out_of_scope" in sql_text
    assert "level" in sql_text.lower()


def test_find_junk_shaped_entities_flags_matching_rows(monkeypatch):
    rows = [
        {"id": 1, "name": "F-16s", "kind": "company"},
        {"id": 2, "name": "Elbit", "kind": "company"},
        {"id": 3, "name": "Western Burrowing Owl", "kind": "company"},
    ]
    recorder: list = []
    monkeypatch.setattr(
        repair_mod, "connection", _connection_stub(_sql_router({"FROM entities": rows}), recorder)
    )

    flagged = repair_mod.find_junk_shaped_entities()

    assert sorted(r["id"] for r in flagged) == [1, 3]


# ---------------------------------------------------------------------------
# task 1 -- scripts.repair_round6.repair_entities_cleanup (D9)
# ---------------------------------------------------------------------------


def test_repair_entities_cleanup_dry_run_never_deletes(monkeypatch):
    monkeypatch.setattr(
        repair_mod,
        "find_out_of_scope_only_entities",
        lambda: [
            {"id": 1, "name": "Airbus", "kind": "company", "country": "EU", "has_in_scope_mention": False},
            {"id": 2, "name": "Safran", "kind": "company", "country": "EU", "has_in_scope_mention": False},
        ],
    )
    from eoa.pipeline.entity_normalize import normalize_name_key

    monkeypatch.setattr(repair_mod, "_watchlist_protected_names", lambda: {normalize_name_key("Safran")})
    monkeypatch.setattr(repair_mod, "_reports_report_state_text", lambda: "")
    monkeypatch.setattr(repair_mod, "find_junk_shaped_entities", lambda: [])
    monkeypatch.setattr(
        repair_mod, "connection", lambda: (_ for _ in ()).throw(AssertionError("must not touch DB"))
    )

    report = repair_mod.repair_entities_cleanup(apply=False)

    assert report["to_delete_ids"] == [1]
    assert report["protected_count"] == 1
    assert report["deleted_count"] == 0


def test_repair_entities_cleanup_apply_deletes_dependents_then_entities(monkeypatch):
    monkeypatch.setattr(
        repair_mod,
        "find_out_of_scope_only_entities",
        lambda: [
            {"id": 1, "name": "Airbus", "kind": "company", "country": "EU", "has_in_scope_mention": False}
        ],
    )
    monkeypatch.setattr(repair_mod, "_watchlist_protected_names", lambda: set())
    monkeypatch.setattr(repair_mod, "_reports_report_state_text", lambda: "")
    monkeypatch.setattr(repair_mod, "find_junk_shaped_entities", lambda: [])
    monkeypatch.setattr(repair_mod, "_entity_fk_columns", lambda: [("graph_edges", "src_entity_id")])

    recorder: list = []

    def _respond(sql, params):
        if "information_schema.columns" in sql:
            return []  # patents.entity_ids "not found" -- skip that branch
        return []

    monkeypatch.setattr(repair_mod, "connection", _connection_stub(_respond, recorder))

    report = repair_mod.repair_entities_cleanup(apply=True)

    assert report["to_delete_ids"] == [1]
    sqls = [s for s, _p in recorder]
    assert any("DELETE FROM" in s and "graph_edges" in s for s in sqls)
    assert any("information_schema.columns" in s for s in sqls)
    assert any("DELETE FROM entities" in s for s in sqls)
    # dependent-row delete happens before the entities delete, within the same recorded sequence
    graph_edges_idx = next(i for i, s in enumerate(sqls) if "graph_edges" in s)
    entities_idx = next(i for i, s in enumerate(sqls) if s.strip().startswith("DELETE FROM entities"))
    assert graph_edges_idx < entities_idx


def test_repair_entities_cleanup_apply_strips_patents_entity_ids_when_column_exists(monkeypatch):
    monkeypatch.setattr(
        repair_mod,
        "find_out_of_scope_only_entities",
        lambda: [
            {"id": 1, "name": "Airbus", "kind": "company", "country": "EU", "has_in_scope_mention": False}
        ],
    )
    monkeypatch.setattr(repair_mod, "_watchlist_protected_names", lambda: set())
    monkeypatch.setattr(repair_mod, "_reports_report_state_text", lambda: "")
    monkeypatch.setattr(repair_mod, "find_junk_shaped_entities", lambda: [])
    monkeypatch.setattr(repair_mod, "_entity_fk_columns", lambda: [])

    recorder: list = []

    def _respond(sql, params):
        if "information_schema.columns" in sql:
            return [{"exists": True}]  # patents.entity_ids "found"
        return []

    monkeypatch.setattr(repair_mod, "connection", _connection_stub(_respond, recorder))

    repair_mod.repair_entities_cleanup(apply=True)

    sqls = [s for s, _p in recorder]
    assert any("UPDATE patents SET entity_ids" in s for s in sqls)


def test_repair_entities_cleanup_no_op_when_nothing_to_delete(monkeypatch):
    monkeypatch.setattr(
        repair_mod,
        "find_out_of_scope_only_entities",
        lambda: [
            {"id": 1, "name": "Safran", "kind": "company", "country": "EU", "has_in_scope_mention": False}
        ],
    )
    from eoa.pipeline.entity_normalize import normalize_name_key

    monkeypatch.setattr(repair_mod, "_watchlist_protected_names", lambda: {normalize_name_key("Safran")})
    monkeypatch.setattr(repair_mod, "_reports_report_state_text", lambda: "")
    monkeypatch.setattr(repair_mod, "find_junk_shaped_entities", lambda: [])
    monkeypatch.setattr(
        repair_mod, "connection", lambda: (_ for _ in ()).throw(AssertionError("must not touch DB"))
    )

    report = repair_mod.repair_entities_cleanup(apply=True)

    assert report["deleted_count"] == 0
    assert report["to_delete_ids"] == []


# ---------------------------------------------------------------------------
# task 1 -- scripts.repair_round6._table_has_column
# ---------------------------------------------------------------------------


def test_table_has_column_true_and_false():
    class _Cur:
        def __init__(self, row):
            self._row = row

        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return self._row

    assert repair_mod._table_has_column(_Cur({"exists": True}), "patents", "entity_ids") is True
    assert repair_mod._table_has_column(_Cur(None), "patents", "entity_ids") is False


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
