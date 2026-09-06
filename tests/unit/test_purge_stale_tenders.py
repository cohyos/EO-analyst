"""Tests for scripts/purge_stale_tenders.py (F24, docs/QA_PROGRAM.md section 4, 2026-09-06):
the re-check gate matrix (`fails_gate`, pure/no DB) and the DB purge orchestration (mocked)."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.purge_stale_tenders import fails_gate, purge

TODAY = dt.date(2026, 9, 6)

DOMAIN_KEYWORDS = ["electro-optical", "infrared", "targeting pod"]
PROCUREMENT_SIGNALS = ["tender", "RFP", "RFI", "sources sought"]


class _FakeSource:
    def __init__(self, kind: str, keywords: list[str]):
        self.kind = kind
        self.keywords = keywords


SOURCES_BY_ID = {
    "ted_eu": _FakeSource("api_json", DOMAIN_KEYWORDS),
    "rfi_rfp_news": _FakeSource("search", DOMAIN_KEYWORDS),
}


def _row(**overrides) -> dict:
    base = dict(
        id=1,
        source="ted_eu",
        external_ref="ted_eu:1",
        title="Supply of electro-optical targeting pod systems",
        summary_he="",
        url="https://ted.europa.eu/notice/1",
        relevance=6,
        status="open",
        deadline=None,
        published_at=None,
    )
    base.update(overrides)
    return base


class TestFailsGateMatrix:
    def test_clean_row_passes(self):
        row = _row(deadline=dt.date(2099, 1, 1))
        assert fails_gate(row, SOURCES_BY_ID, PROCUREMENT_SIGNALS, [], today=TODAY) is None

    def test_denylisted_domain_purged(self):
        row = _row(url="https://www.scribd.com/document/1/x")
        reason = fails_gate(row, SOURCES_BY_ID, PROCUREMENT_SIGNALS, ["scribd.com"], today=TODAY)
        assert reason == "deny_domain"

    def test_no_domain_signal_purged(self):
        """The 'Green Tech Projects Corp. | CanadaBuys' case: no EO/IR/CV term at all."""
        row = _row(title="Green Tech Projects Corp. | CanadaBuys", summary_he="")
        reason = fails_gate(row, SOURCES_BY_ID, PROCUREMENT_SIGNALS, [], today=TODAY)
        assert reason == "no_domain_signal"

    def test_search_source_without_procurement_signal_purged(self):
        row = _row(
            source="rfi_rfp_news",
            external_ref="rfi_rfp_news:1",
            title="Infrared: how thermal imaging works",
        )
        reason = fails_gate(row, SOURCES_BY_ID, PROCUREMENT_SIGNALS, [], today=TODAY)
        assert reason == "no_procurement_signal"

    def test_relevance_below_floor_purged(self):
        row = _row(relevance=3)
        reason = fails_gate(row, SOURCES_BY_ID, PROCUREMENT_SIGNALS, [], today=TODAY)
        assert reason == "relevance_below_floor"

    def test_relevance_at_floor_kept(self):
        row = _row(relevance=6, deadline=dt.date(2099, 1, 1))
        assert fails_gate(row, SOURCES_BY_ID, PROCUREMENT_SIGNALS, [], today=TODAY) is None

    def test_undated_unknown_purged(self):
        """F24: the review's '13 undated unknown rows' case -- no stored evidence the page was
        ever actually verified."""
        row = _row(status="unknown", deadline=None, published_at=None)
        reason = fails_gate(row, SOURCES_BY_ID, PROCUREMENT_SIGNALS, [], today=TODAY)
        assert reason == "unverified_undated_unknown"

    def test_dated_unknown_kept(self):
        row = _row(status="unknown", published_at=dt.date(2026, 8, 20))
        assert fails_gate(row, SOURCES_BY_ID, PROCUREMENT_SIGNALS, [], today=TODAY) is None

    def test_stale_published_open_purged(self):
        row = _row(status="open", published_at=TODAY - dt.timedelta(days=91))
        reason = fails_gate(row, SOURCES_BY_ID, PROCUREMENT_SIGNALS, [], today=TODAY)
        assert reason == "stale_published_still_open_or_unknown"

    def test_recent_published_open_kept(self):
        row = _row(status="open", published_at=TODAY - dt.timedelta(days=10))
        assert fails_gate(row, SOURCES_BY_ID, PROCUREMENT_SIGNALS, [], today=TODAY) is None

    def test_closed_status_exempt_from_undated_and_stale_checks(self):
        """A 'closed' row's lifecycle is owned by _transition_closed/_archive_stale_closed, not
        this purge -- it must never be deleted outright just for being old/undated."""
        row = _row(status="closed", published_at=dt.date(2015, 1, 1), deadline=None)
        assert fails_gate(row, SOURCES_BY_ID, PROCUREMENT_SIGNALS, [], today=TODAY) is None

    def test_awarded_status_exempt_from_undated_and_stale_checks(self):
        row = _row(status="awarded", published_at=None, deadline=None)
        assert fails_gate(row, SOURCES_BY_ID, PROCUREMENT_SIGNALS, [], today=TODAY) is None

    def test_unknown_source_treated_as_general_web(self):
        """A row whose `source` id no longer matches any configured source (e.g. removed from
        config/tenders.yaml) still gets checked, as a general (non-api_json) source."""
        row = _row(source="some-removed-source", external_ref="x:1", title="A random RFI notice")
        reason = fails_gate(row, SOURCES_BY_ID, PROCUREMENT_SIGNALS, [], today=TODAY)
        assert reason == "no_domain_signal"


class _FakeCursor:
    def __init__(self, fetchall_result=None, fetchone_result=None, rowcount=0):
        self.executed: list[tuple[str, object]] = []
        self._fetchall_result = fetchall_result if fetchall_result is not None else []
        self._fetchone_result = fetchone_result
        self.rowcount = rowcount

    def execute(self, query, params=None):
        self.executed.append((query, params))
        return self

    def fetchall(self):
        return self._fetchall_result

    def fetchone(self):
        return self._fetchone_result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestPurgeOrchestration:
    def test_dry_run_reports_without_deleting(self):
        rows = [
            _row(id=1, relevance=1, status="open"),  # fails: relevance
            _row(id=2, relevance=6, deadline=dt.date(2099, 1, 1), status="open"),  # passes
        ]
        cur = _FakeCursor(fetchall_result=rows)
        conn = _FakeConnection(cur)
        with patch("scripts.purge_stale_tenders.db.connection", return_value=conn):
            result = purge(dry_run=True)

        assert result["dry_run"] is True
        assert result["before_total"] == 2
        assert result["to_delete"] == 1
        assert result["reasons"] == {"relevance_below_floor": 1}
        assert result["deleted_tenders"] == 0
        assert result["after_total"] == 2  # nothing actually removed on a dry run
        # Only the read query ran -- no DELETE statements issued.
        assert not any("DELETE" in q for q, _ in cur.executed)

    def test_real_run_deletes_failing_rows_and_reports_status_breakdown(self):
        rows = [
            _row(id=1, relevance=1, status="open"),
            _row(id=2, relevance=6, deadline=dt.date(2099, 1, 1), status="open"),
        ]
        select_cur = _FakeCursor(fetchall_result=rows)
        select_conn = _FakeConnection(select_cur)

        delete_cur = _FakeCursor(fetchall_result=[], fetchone_result={"n": 0}, rowcount=1)
        delete_conn = _FakeConnection(delete_cur)

        with patch("scripts.purge_stale_tenders.db.connection", side_effect=[select_conn, delete_conn]):
            result = purge(dry_run=False)

        assert result["to_delete"] == 1
        assert result["deleted_tenders"] == 1
        assert result["before_by_status"] == {"open": 2}
        assert result["after_by_status"] == {"open": 1}
        delete_query = next(q for q, _ in delete_cur.executed if "DELETE FROM tenders" in q)
        assert delete_query  # the tenders row was actually targeted for deletion

    def test_item_with_no_other_use_is_deleted_alongside_its_tender(self):
        rows = [_row(id=1, relevance=1, status="open")]
        select_conn = _FakeConnection(_FakeCursor(fetchall_result=rows))

        # First query (deletable item ids): one item, never triaged (level IS NULL).
        # Second query (kept-items count): 0. Then the two DELETEs.
        delete_cur = _FakeCursor()
        call_sequence = [
            [{"id": 501}],  # deletable_item_ids
            [],  # (unused fetchall for the count query -- it uses fetchone)
        ]
        fetchall_iter = iter(call_sequence)
        delete_cur.fetchall = lambda: next(fetchall_iter, [])
        delete_cur.fetchone = lambda: {"n": 0}

        def _execute(query, params=None):
            delete_cur.executed.append((query, params))
            if "DELETE FROM tenders" in query:
                delete_cur.rowcount = 1
            elif "DELETE FROM items" in query:
                delete_cur.rowcount = 1
            return delete_cur

        delete_cur.execute = _execute
        delete_conn = _FakeConnection(delete_cur)

        with patch("scripts.purge_stale_tenders.db.connection", side_effect=[select_conn, delete_conn]):
            result = purge(dry_run=False)

        assert result["deleted_items"] == 1
        assert result["kept_items"] == 0
        assert any("DELETE FROM items" in q for q, _ in delete_cur.executed)
