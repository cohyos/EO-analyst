"""Unit tests for eoa.fetch.service's IngestStats counting (F19, docs/REVIEW_2026-09-05.md).

No DB, no network: `eoa.memory.relational.insert_item` and `eoa.fetch.service._url_already_seen`
are monkeypatched; `_store_item` otherwise runs its real (pure-python) sanitize pipeline over a
small literal HTML snippet.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_fetch_service.py -q``
"""

from __future__ import annotations

import pytest

from eoa.fetch import service

_SAMPLE_HTML = (
    "<html><head><title>Elbit wins new EO/IR contract</title></head>"
    "<body><article><p>Elbit Systems announced a new contract for targeting pods today.</p>"
    "<p>The deal is worth a significant sum and covers several years of deliveries.</p>"
    "</article></body></html>"
)


class _FakeConnCtx:
    """Minimal ``eoa.db.connection()`` stand-in for `_url_already_seen`'s own probe query --
    only used by tests that exercise that function directly rather than via monkeypatch."""

    def __init__(self, row: dict | None, *, raises: bool = False):
        self._row = row
        self._raises = raises

    def __enter__(self):
        if self._raises:
            raise RuntimeError("db unreachable")
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        return None

    def fetchone(self):
        return self._row


# --------------------------------------------------------------------------
# _url_already_seen: best-effort existence probe
# --------------------------------------------------------------------------


class TestUrlAlreadySeen:
    def test_true_when_a_row_comes_back(self, monkeypatch):
        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConnCtx({"?column?": 1}))
        assert service._url_already_seen("https://example.com/a") is True

    def test_false_when_no_row_comes_back(self, monkeypatch):
        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConnCtx(None))
        assert service._url_already_seen("https://example.com/b") is False

    def test_false_on_db_error_best_effort(self, monkeypatch):
        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConnCtx(None, raises=True))
        assert service._url_already_seen("https://example.com/c") is False


# --------------------------------------------------------------------------
# _store_item: IngestStats accounting (F19)
# --------------------------------------------------------------------------


class TestStoreItemStats:
    def test_new_url_counts_as_inserted(self, monkeypatch):
        monkeypatch.setattr(service, "_url_already_seen", lambda url: False)
        monkeypatch.setattr("eoa.memory.relational.insert_item", lambda **kw: 101)

        stats = service.IngestStats()
        service._store_item(
            source_db_id=1, url="https://example.com/new", html_text=_SAMPLE_HTML, stats=stats
        )

        assert stats.items_inserted == 1
        assert stats.items_skipped == 0

    def test_conflict_refresh_of_seen_url_counts_as_skipped_not_inserted(self, monkeypatch):
        """The regression this fixes: `relational.insert_item`'s `ON CONFLICT (url) DO UPDATE`
        always `RETURNING id` -- even for a URL already in the DB, just refreshed `fetched_at` --
        so the id it returns is truthy either way. Before F19, `_store_item` counted every such
        refresh as a fresh `items_inserted`, which is how one run reported 115 while only 61 rows
        were actually new."""
        monkeypatch.setattr(service, "_url_already_seen", lambda url: True)
        monkeypatch.setattr("eoa.memory.relational.insert_item", lambda **kw: 202)  # pre-existing id

        stats = service.IngestStats()
        service._store_item(
            source_db_id=1, url="https://example.com/seen", html_text=_SAMPLE_HTML, stats=stats
        )

        assert stats.items_inserted == 0
        assert stats.items_skipped == 1

    def test_insert_failure_counts_as_skipped_regardless_of_seen(self, monkeypatch):
        monkeypatch.setattr(service, "_url_already_seen", lambda url: False)

        def _boom(**kw):
            raise RuntimeError("db write failed")

        monkeypatch.setattr("eoa.memory.relational.insert_item", _boom)

        stats = service.IngestStats()
        service._store_item(
            source_db_id=1, url="https://example.com/fails", html_text=_SAMPLE_HTML, stats=stats
        )

        assert stats.items_inserted == 0
        assert stats.items_skipped == 1

    def test_multiple_calls_accumulate_on_shared_stats(self, monkeypatch):
        monkeypatch.setattr(service, "_url_already_seen", lambda url: "seen" in url)
        monkeypatch.setattr("eoa.memory.relational.insert_item", lambda **kw: 1)

        stats = service.IngestStats()
        service._store_item(source_db_id=None, url="https://example.com/1", html_text=_SAMPLE_HTML, stats=stats)
        service._store_item(source_db_id=None, url="https://example.com/2", html_text=_SAMPLE_HTML, stats=stats)
        service._store_item(
            source_db_id=None, url="https://example.com/seen-3", html_text=_SAMPLE_HTML, stats=stats
        )

        assert stats.items_inserted == 2
        assert stats.items_skipped == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
