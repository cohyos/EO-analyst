"""Unit tests for Q3-13 (docs/qa/findings_Q3_r1.md): ``eoa.memory.relational.upsert_entity``'s
normalisation integration (canonical name/kind resolution, case-insensitive dedup against an
existing row, technique-like rejection). No DB: ``eoa.memory.relational.connection`` is
monkeypatched with a fake cursor that records the executed SQL/params and returns canned rows.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_upsert_entity_normalization.py -q``
"""

from __future__ import annotations

from eoa.memory import relational


class _FakeCursor:
    def __init__(self, responses: list[dict | None]):
        self._responses = list(responses)
        self.queries: list[tuple[str, dict]] = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params or {}))

    def fetchone(self):
        return self._responses.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_connection(monkeypatch, cursor: _FakeCursor) -> None:
    monkeypatch.setattr(relational, "connection", lambda: _FakeConn(cursor))


def test_technique_like_name_rejected_without_any_db_call(monkeypatch):
    cursor = _FakeCursor([])
    _patch_connection(monkeypatch, cursor)

    result = relational.upsert_entity(name="image captioning", kind="system")

    assert result is None
    assert cursor.queries == []  # never touched the DB at all


def test_watchlist_alias_resolves_to_canonical_name_before_insert(monkeypatch):
    # first fetchone(): case-insensitive lookup (no match); second: the INSERT ... RETURNING id
    cursor = _FakeCursor([None, {"id": 42}])
    _patch_connection(monkeypatch, cursor)

    entity_id = relational.upsert_entity(name="Elbit Systems", kind="company")

    assert entity_id == 42
    _insert_sql, insert_params = cursor.queries[-1]
    assert insert_params["name"] == "Elbit"  # canonicalized from the alias
    assert insert_params["kind"] == "company"
    assert insert_params["country"] == "IL"  # backfilled from the watchlist record


def test_system_designation_keeps_own_name_and_kind_system(monkeypatch):
    cursor = _FakeCursor([None, {"id": 7}])
    _patch_connection(monkeypatch, cursor)

    entity_id = relational.upsert_entity(name="LOCUST", kind="company")

    assert entity_id == 7
    _, insert_params = cursor.queries[-1]
    assert insert_params["name"] == "LOCUST"
    assert insert_params["kind"] == "system"


def test_government_body_kind_normalized_to_org(monkeypatch):
    cursor = _FakeCursor([None, {"id": 9}])
    _patch_connection(monkeypatch, cursor)

    relational.upsert_entity(name="Some Government Agency", kind="country")

    _, insert_params = cursor.queries[-1]
    assert insert_params["kind"] == "org"


def test_genuine_country_kind_kept(monkeypatch):
    """Since migration 0007, the `entities` table's CHECK constraint allows 'country'."""
    cursor = _FakeCursor([None, {"id": 11}])
    _patch_connection(monkeypatch, cursor)

    relational.upsert_entity(name="Israel", kind="country")

    _, insert_params = cursor.queries[-1]
    assert insert_params["kind"] == "country"


def test_case_insensitive_existing_row_reused(monkeypatch):
    # First fetchone(): case-insensitive lookup finds an existing differently-cased row.
    cursor = _FakeCursor([{"name": "Acme Corp"}, {"id": 3}])
    _patch_connection(monkeypatch, cursor)

    entity_id = relational.upsert_entity(name="acme corp", kind="company")

    assert entity_id == 3
    _, insert_params = cursor.queries[-1]
    assert insert_params["name"] == "Acme Corp"  # reused the existing row's exact spelling


def test_non_watchlisted_name_passes_through_unchanged(monkeypatch):
    cursor = _FakeCursor([None, {"id": 5}])
    _patch_connection(monkeypatch, cursor)

    entity_id = relational.upsert_entity(name="Totally New Startup Inc", kind="company")

    assert entity_id == 5
    _, insert_params = cursor.queries[-1]
    assert insert_params["name"] == "Totally New Startup Inc"
    assert insert_params["kind"] == "company"
