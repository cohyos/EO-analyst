"""Unit tests for eoa.memory.relational.insert_item's F09/F36 quality-aware upsert, the "return an
inserted/conflict flag from the item upsert" efficiency item (both SOL-AUDIT-2026-09-24), and the
round-2 SOL-REVIEW-2026-09-24 fixes: F36/N10 (existing-row lookup runs on every call plus an
advisory lock keyed by canonical identity), F09/N04 (a refresh also requires `text_hash` to have
changed), N09 (`canonical_url` only changes together with accepted content), and N05 (an accepted
content update clears `dedup_of`).

No DB: `eoa.db.connection` is monkeypatched with a fake cursor that records every executed
SQL/params and returns canned `fetchone()` rows in order -- same pattern as
tests/unit/test_relational_stage_filter.py.

Every call now runs THREE statements in order: (1) `pg_advisory_xact_lock` (no `fetchone()`),
(2) the merged existing-row lookup (`fetchone()` -> existing id or None), (3) either the by-id
UPDATE or the plain `ON CONFLICT (url)` upsert (`fetchone()` -> the final row). `_FakeCursor` is
given exactly two canned `fetchone()` results per call: the lookup result, then the final row.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_insert_item_upsert.py -q``
"""

from __future__ import annotations

import pytest

from eoa.memory.relational import ItemUpsertResult, insert_item


class _FakeCursor:
    def __init__(self, fetchone_results: list[dict | None]):
        self._results = list(fetchone_results)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params or {}))

    def fetchone(self):
        return self._results.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_connection(monkeypatch, cursor: _FakeCursor) -> None:
    monkeypatch.setattr("eoa.memory.relational.connection", lambda: _FakeConn(cursor))


# --------------------------------------------------------------------------
# ItemUpsertResult: int-compatible, carries .inserted
# --------------------------------------------------------------------------


class TestItemUpsertResult:
    def test_behaves_as_a_plain_int(self):
        result = ItemUpsertResult(42, inserted=True)
        assert result == 42
        assert int(result) == 42
        assert result + 1 == 43
        assert bool(result) is True  # truthy, like the old bare id

    def test_zero_id_is_still_falsy_like_a_plain_int(self):
        result = ItemUpsertResult(0, inserted=True)
        assert not result

    def test_carries_inserted_flag(self):
        assert ItemUpsertResult(1, inserted=True).inserted is True
        assert ItemUpsertResult(1, inserted=False).inserted is False


# --------------------------------------------------------------------------
# F36/N10: the existing-row lookup and advisory lock now run on EVERY call.
# --------------------------------------------------------------------------


class TestLookupAndLockRunEveryCall:
    def test_brand_new_url_takes_lock_looks_up_then_inserts(self, monkeypatch):
        cursor = _FakeCursor([None, {"id": 101, "inserted": True}])
        _patch_connection(monkeypatch, cursor)

        result = insert_item(source_id=None, url="https://example.com/a", title="A")

        assert result == 101
        assert result.inserted is True
        assert len(cursor.calls) == 3
        lock_sql, lock_params = cursor.calls[0]
        assert "pg_advisory_xact_lock" in lock_sql
        assert lock_params["key"] == "https://example.com/a"  # lookup_identity = canonical_url or url
        lookup_sql, _ = cursor.calls[1]
        assert "SELECT id FROM items WHERE" in lookup_sql
        upsert_sql, params = cursor.calls[2]
        assert "ON CONFLICT (url) DO UPDATE SET" in upsert_sql
        assert params["quality_aware"] is False
        assert params["content_status"] == "full"  # resolved default, never NULL (NOT NULL column)
        assert params["security_status"] == "clean"

    def test_returned_value_works_anywhere_a_plain_int_id_was_expected(self, monkeypatch):
        """int-subclass compatibility for existing callers like eoa.tenders.scan and
        update_item_fields(item_id, ...) that just treat the return value as a bare id."""
        cursor = _FakeCursor([None, {"id": 9, "inserted": True}])
        _patch_connection(monkeypatch, cursor)

        item_id = insert_item(source_id=None, url="https://example.com/b")
        assert isinstance(item_id, int)
        assert f"item {item_id}" == "item 9"

    def test_lookup_matches_url_on_either_side_of_the_or(self, monkeypatch):
        """F36/N10: 'alias-first then direct canonical fetch' -- an earlier alias fetch already
        stored a row with `canonical_url` equal to this (now direct) fetch's plain `url`. The
        lookup must catch it via `canonical_url = %(url)s`, not just `url = %(url)s`, even though
        THIS call passes no `canonical_url` of its own. Fails against the old (round-1) code, which
        skipped the lookup entirely whenever `canonical_url` was falsy and went straight to a plain
        `ON CONFLICT (url)` insert -- creating a second row for the same article."""
        cursor = _FakeCursor([{"id": 55}, {"id": 55}])
        _patch_connection(monkeypatch, cursor)

        result = insert_item(source_id=None, url="https://example.com/canonical")

        assert result == 55
        assert result.inserted is False
        assert len(cursor.calls) == 3
        lookup_sql, lookup_params = cursor.calls[1]
        assert "canonical_url = %(url)s" in lookup_sql
        assert lookup_params["url"] == "https://example.com/canonical"
        update_sql, update_params = cursor.calls[2]
        assert "UPDATE items SET" in update_sql
        assert update_params["item_id"] == 55


# --------------------------------------------------------------------------
# N10: advisory lock key is the canonical identity (canonical_url when given, else url) --
# serializes concurrent upserts of two different aliases of the same new article.
# --------------------------------------------------------------------------


class TestAdvisoryLockKey:
    def test_lock_key_is_canonical_url_when_given(self, monkeypatch):
        cursor = _FakeCursor([None, {"id": 3, "inserted": True}])
        _patch_connection(monkeypatch, cursor)

        insert_item(
            source_id=None,
            url="https://example.com/alias?utm_source=x",
            canonical_url="https://example.com/canonical-3",
        )

        _, lock_params = cursor.calls[0]
        assert lock_params["key"] == "https://example.com/canonical-3"


# --------------------------------------------------------------------------
# F09/N04: a refresh requires BOTH a strictly-better quality rank AND a changed text_hash.
# --------------------------------------------------------------------------


class TestQualityAwareParams:
    def test_content_and_security_status_passed_through(self, monkeypatch):
        cursor = _FakeCursor([None, {"id": 5, "inserted": True}])
        _patch_connection(monkeypatch, cursor)

        insert_item(
            source_id=1,
            url="https://example.com/c",
            content_status="stub",
            security_status="blocked",
        )

        _, params = cursor.calls[2]
        assert params["quality_aware"] is True
        assert params["content_status"] == "stub"
        assert params["security_status"] == "blocked"

    def test_upsert_sql_requires_hash_change_and_never_downgrades(self, monkeypatch):
        """The quality-rank CASE expression must appear in the SET clause for every
        content-bearing column, guarded by BOTH `quality_aware` and a `text_hash IS DISTINCT FROM`
        check (N04: an identical re-fetch must never re-trigger a stage reset just because
        analysis later downgraded the stored `content_status`) -- a static check that the refresh
        logic is actually wired into the query text; the DB-level behavior itself needs a live
        Postgres (see tests/unit/test_insert_item_upsert_postgres.py)."""
        cursor = _FakeCursor([None, {"id": 5, "inserted": True}])
        _patch_connection(monkeypatch, cursor)

        insert_item(source_id=1, url="https://example.com/d", content_status="full", security_status="clean")

        sql, _ = cursor.calls[2]
        assert "ON CONFLICT (url) DO UPDATE SET" in sql
        assert "(xmax = 0) AS inserted" in sql
        assert "%(quality_aware)s" in sql
        assert "text_hash IS DISTINCT FROM items.text_hash" in sql
        for col in ("title", "clean_text", "published_at", "content_status", "security_status"):
            assert f"{col} = CASE WHEN" in sql
        assert "processed_stages = CASE WHEN" in sql
        assert "'{}'::text[]" in sql  # reset target on a genuine quality upgrade
        # N09: canonical_url only refreshes together with accepted content, not unconditionally.
        assert "canonical_url = CASE WHEN" in sql
        # N05: dedup_of is cleared atomically with an accepted content update.
        assert "dedup_of = CASE WHEN" in sql


# --------------------------------------------------------------------------
# F36: canonical_url identity -- a redirect alias/tracking-variant upserts the SAME row as an
# already-stored article instead of creating a duplicate.
# --------------------------------------------------------------------------


class TestCanonicalUrlIdentity:
    def test_different_canonical_url_with_existing_match_updates_that_row(self, monkeypatch):
        cursor = _FakeCursor(
            [
                {"id": 55},  # the canonical-url lookup SELECT finds an existing row
                {"id": 55},  # the by-id UPDATE ... RETURNING id
            ]
        )
        _patch_connection(monkeypatch, cursor)

        result = insert_item(
            source_id=None,
            url="https://example.com/alias-b",
            canonical_url="https://example.com/canonical",
        )

        assert result == 55
        assert result.inserted is False  # an update of an existing row, never a fresh insert
        assert len(cursor.calls) == 3
        select_sql, select_params = cursor.calls[1]
        assert "url = %(canonical_url)s OR canonical_url = %(canonical_url)s" in select_sql
        assert select_params["canonical_url"] == "https://example.com/canonical"
        update_sql, update_params = cursor.calls[2]
        assert "UPDATE items SET" in update_sql
        assert update_params["item_id"] == 55

    def test_different_canonical_url_with_no_existing_match_falls_back_to_plain_upsert(self, monkeypatch):
        cursor = _FakeCursor(
            [
                None,  # canonical-url lookup finds nothing
                {"id": 77, "inserted": True},  # the ordinary ON CONFLICT (url) upsert
            ]
        )
        _patch_connection(monkeypatch, cursor)

        result = insert_item(
            source_id=None,
            url="https://example.com/alias-c",
            canonical_url="https://example.com/brand-new-canonical",
        )

        assert result == 77
        assert result.inserted is True
        assert len(cursor.calls) == 3
        assert "ON CONFLICT (url) DO UPDATE SET" in cursor.calls[2][0]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
