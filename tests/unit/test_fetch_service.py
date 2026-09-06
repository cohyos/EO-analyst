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

    def cursor(self, row_factory=None):
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
        service._store_item(
            source_db_id=None, url="https://example.com/1", html_text=_SAMPLE_HTML, stats=stats
        )
        service._store_item(
            source_db_id=None, url="https://example.com/2", html_text=_SAMPLE_HTML, stats=stats
        )
        service._store_item(
            source_db_id=None, url="https://example.com/seen-3", html_text=_SAMPLE_HTML, stats=stats
        )

        assert stats.items_inserted == 2
        assert stats.items_skipped == 1


# --------------------------------------------------------------------------
# D9 round-1 fix (docs/qa/loop/round_1_fixes.md, sources_recently_fetched): per-source
# last_fetched_at/fail_count bookkeeping, once per _ingest_one_source call.
# --------------------------------------------------------------------------


class _FakeSource:
    def __init__(self, kind="rss", source_id=42, name="Example Source", url="https://example.com/feed"):
        self.kind = kind
        self.id = source_id
        self.name = name
        self.url = url


class TestTouchSourceFetched:
    def test_success_calls_relational_touch_with_ok_true(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "eoa.memory.relational.touch_source_fetched", lambda sid, ok: calls.append((sid, ok))
        )
        service._touch_source_fetched(7, "Example Source", ok=True)
        assert calls == [(7, True)]

    def test_failure_calls_relational_touch_with_ok_false(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "eoa.memory.relational.touch_source_fetched", lambda sid, ok: calls.append((sid, ok))
        )
        service._touch_source_fetched(7, "Example Source", ok=False)
        assert calls == [(7, False)]

    def test_no_source_id_falls_back_to_name_keyed_bump_on_failure(self, monkeypatch):
        bumped = []
        monkeypatch.setattr(service, "_bump_fail_count", lambda name: bumped.append(name))
        service._touch_source_fetched(None, "Example Source", ok=False)
        assert bumped == ["Example Source"]

    def test_no_source_id_and_ok_is_a_noop(self, monkeypatch):
        bumped = []
        monkeypatch.setattr(service, "_bump_fail_count", lambda name: bumped.append(name))
        service._touch_source_fetched(None, "Example Source", ok=True)
        assert bumped == []

    def test_relational_error_is_swallowed(self, monkeypatch):
        def _boom(sid, ok):
            raise RuntimeError("db down")

        monkeypatch.setattr("eoa.memory.relational.touch_source_fetched", _boom)
        service._touch_source_fetched(7, "Example Source", ok=True)  # must not raise


class TestIngestOneSourceTouchesBookkeeping:
    @pytest.mark.asyncio
    async def test_success_touches_ok_true(self, monkeypatch):
        calls = []
        monkeypatch.setattr(service, "_touch_source_fetched", lambda sid, name, ok: calls.append((sid, ok)))

        async def _fake_rss(source, *, source_db_id, since_days, throttle, stats):
            return None

        monkeypatch.setattr(service, "_ingest_rss_source", _fake_rss)
        stats = service.IngestStats()
        await service._ingest_one_source(
            _FakeSource(), source_db_id=7, since_days=3, throttle=service._DomainThrottle(), stats=stats
        )
        assert calls == [(7, True)]
        assert stats.sources_failed == 0

    @pytest.mark.asyncio
    async def test_fetch_error_touches_ok_false(self, monkeypatch):
        from eoa.errors import FetchError

        calls = []
        monkeypatch.setattr(service, "_touch_source_fetched", lambda sid, name, ok: calls.append((sid, ok)))

        async def _fake_rss(source, *, source_db_id, since_days, throttle, stats):
            raise FetchError("boom")

        monkeypatch.setattr(service, "_ingest_rss_source", _fake_rss)
        stats = service.IngestStats()
        await service._ingest_one_source(
            _FakeSource(), source_db_id=7, since_days=3, throttle=service._DomainThrottle(), stats=stats
        )
        assert calls == [(7, False)]
        assert stats.sources_failed == 1

    @pytest.mark.asyncio
    async def test_unexpected_error_touches_ok_false(self, monkeypatch):
        calls = []
        monkeypatch.setattr(service, "_touch_source_fetched", lambda sid, name, ok: calls.append((sid, ok)))

        async def _fake_rss(source, *, source_db_id, since_days, throttle, stats):
            raise ValueError("unexpected")

        monkeypatch.setattr(service, "_ingest_rss_source", _fake_rss)
        stats = service.IngestStats()
        await service._ingest_one_source(
            _FakeSource(), source_db_id=7, since_days=3, throttle=service._DomainThrottle(), stats=stats
        )
        assert calls == [(7, False)]
        assert stats.sources_failed == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
