"""Tests for eoa.patents.scan (A14) -- pure logic + parsing, no DB/network.

Mirrors tests/unit/test_tenders_scan.py's stubbing style: every DB- or network-touching function
is monkeypatched at the module level.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from eoa.patents.models import PatentRecord
from eoa.patents.scan import (
    WatchTopic,
    _assignee_candidates_in_text,
    _clean_search_title,
    _extract_pub_number,
    _google_patents_records,
    _within_window,
    first_run_since_days,
    load_assignees,
    load_cpc_codes,
    load_watch_topics,
    scan_patents,
    search_records,
    structured_sources_configured,
    upsert_records,
)
from eoa.search.provider import SearchHit, SearchResponse


class TestConfigLoaders:
    def test_loads_real_cpc_codes(self):
        codes = load_cpc_codes()
        assert len(codes) >= 5
        assert "G01J5" in codes

    def test_loads_real_watch_topics(self):
        topics = load_watch_topics()
        assert len(topics) >= 5
        assert all(isinstance(t, WatchTopic) for t in topics)
        names = {t.name_he for t in topics}
        assert any("DROIC" in n or "פיקסל" in n for n in names)

    def test_loads_real_assignees(self):
        assignees = load_assignees()
        assert "Elbit" in assignees
        assert "IAI" in assignees

    def test_first_run_since_days_default(self):
        assert first_run_since_days() == 90


class TestExtractPubNumber:
    def test_extracts_from_standard_url(self):
        assert _extract_pub_number("https://patents.google.com/patent/US11234567B2/en") == "US11234567B2"

    def test_extracts_without_lang_suffix(self):
        assert _extract_pub_number("https://patents.google.com/patent/WO2021123456A1") == "WO2021123456A1"

    def test_non_matching_url_returns_none(self):
        assert _extract_pub_number("https://example.com/foo") is None

    def test_empty_url_returns_none(self):
        assert _extract_pub_number("") is None


class TestCleanSearchTitle:
    def test_strips_google_patents_suffix(self):
        assert _clean_search_title("Widget thing - Google Patents") == "Widget thing"

    def test_leaves_plain_title_unchanged(self):
        assert _clean_search_title("Widget thing") == "Widget thing"

    def test_empty_title(self):
        assert _clean_search_title("") == ""


class TestAssigneeCandidatesInText:
    def test_watchlist_company_recognized(self):
        assert "Elbit" in _assignee_candidates_in_text("A patent by Elbit Systems for infrared sensors")

    def test_curated_org_or_country_not_treated_as_assignee(self):
        """F2 (this fix): a bare mention of a curated org/country alias (e.g. "Europe"/"NATO",
        present in the alias table for unrelated report-entity-extraction purposes) must never be
        fabricated into a patent assignee."""
        assert _assignee_candidates_in_text("A patent filed somewhere in Europe") == []

    def test_no_match_returns_empty_list(self):
        assert _assignee_candidates_in_text("Just a generic sentence about optics") == []


class TestWithinWindow:
    def test_undated_record_always_within_window(self):
        rec = PatentRecord(pub_number="US1")
        assert _within_window(rec, since_days=30, today=dt.date(2026, 9, 6)) is True

    def test_recent_record_within_window(self):
        rec = PatentRecord(pub_number="US1", publication_date=dt.date(2026, 9, 1))
        assert _within_window(rec, since_days=30, today=dt.date(2026, 9, 6)) is True

    def test_old_record_outside_window(self):
        rec = PatentRecord(pub_number="US1", publication_date=dt.date(2020, 1, 1))
        assert _within_window(rec, since_days=30, today=dt.date(2026, 9, 6)) is False


class TestGooglePatentsRecords:
    def test_parses_hits_into_records(self):
        hits = [
            SearchHit(
                url="https://patents.google.com/patent/US11234567B2/en",
                title="Digital pixel readout circuit - Google Patents",
                snippet="A readout circuit for infrared focal plane arrays by Elbit.",
                engine="ddgs",
            )
        ]
        with patch("eoa.patents.scan.search", return_value=SearchResponse(query="q", lang="en", hits=hits)):
            records = _google_patents_records("digital pixel readout")
        assert len(records) == 1
        rec = records[0]
        assert rec.pub_number == "US11234567B2"
        assert rec.title == "Digital pixel readout circuit"
        assert rec.source == "google_patents_search"
        assert "Elbit" in rec.assignees

    def test_search_error_returns_empty_list(self):
        with patch(
            "eoa.patents.scan.search",
            return_value=SearchResponse(query="q", lang="en", error="rate limited"),
        ):
            assert _google_patents_records("q") == []

    def test_deduplicates_by_pub_number(self):
        hits = [
            SearchHit(url="https://patents.google.com/patent/US1/en", title="A", snippet="", engine="ddgs"),
            SearchHit(
                url="https://patents.google.com/patent/US1/de", title="A (DE)", snippet="", engine="ddgs"
            ),
        ]
        with patch("eoa.patents.scan.search", return_value=SearchResponse(query="q", lang="en", hits=hits)):
            records = _google_patents_records("q")
        assert len(records) == 1

    def test_hits_without_a_recognizable_pub_number_are_skipped(self):
        hits = [SearchHit(url="https://patents.google.com/foo", title="x", snippet="", engine="ddgs")]
        with patch("eoa.patents.scan.search", return_value=SearchResponse(query="q", lang="en", hits=hits)):
            assert _google_patents_records("q") == []


class TestStructuredSourcesConfigured:
    def test_false_when_no_keys_set(self, monkeypatch):
        monkeypatch.delenv("EPO_OPS_KEY", raising=False)
        monkeypatch.delenv("EPO_OPS_SECRET", raising=False)
        monkeypatch.delenv("PATENTSVIEW_API_KEY", raising=False)
        assert structured_sources_configured() is False

    def test_true_when_epo_keys_set(self, monkeypatch):
        monkeypatch.setenv("EPO_OPS_KEY", "k")
        monkeypatch.setenv("EPO_OPS_SECRET", "s")
        monkeypatch.delenv("PATENTSVIEW_API_KEY", raising=False)
        assert structured_sources_configured() is True


class TestSearchRecords:
    def test_caps_at_limit_and_dedupes(self):
        records = [PatentRecord(pub_number=f"US{i}") for i in range(5)] + [PatentRecord(pub_number="US0")]
        with (
            patch("eoa.patents.scan._structured_sources_available", return_value=False),
            patch("eoa.patents.scan._google_patents_records", return_value=records),
        ):
            out = search_records("q", limit=3)
        assert len(out) == 3
        assert [r.pub_number for r in out] == ["US0", "US1", "US2"]


class TestUpsertRecords:
    def test_new_and_existing_records(self, monkeypatch):
        class _FakeCursor:
            def __init__(self):
                self.calls = []

            def execute(self, query, params=None):
                self.calls.append((query, params))
                return self

            def fetchone(self):
                if "SELECT id FROM patents WHERE pub_number" in self.calls[-1][0]:
                    return {"id": 100}
                return {"id": 200}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _FakeConnection:
            def __init__(self, cur):
                self._cur = cur

            def cursor(self):
                return self._cur

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        cur = _FakeCursor()
        monkeypatch.setattr("eoa.patents.scan.connection", lambda: _FakeConnection(cur))
        with (
            patch("eoa.patents.scan._patent_exists", side_effect=[True, False]),
            patch("eoa.patents.scan._insert_patent", return_value=200),
        ):
            ids = upsert_records([PatentRecord(pub_number="US1"), PatentRecord(pub_number="US2")])
        assert ids == {"US1": 100, "US2": 200}

    def test_existing_record_with_no_new_fields_never_touches_db_for_backfill(self, monkeypatch):
        """A repeat hit for an already-known pub_number whose new PatentRecord carries no
        assignees/cpc at all (the common shape) must not issue any UPDATE -- _backfill_patent_fields
        short-circuits before touching the connection at all."""

        class _FakeCursor:
            def execute(self, query, params=None):
                assert "UPDATE patents" not in query
                return self

            def fetchone(self):
                return {"id": 100}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _FakeConnection:
            def cursor(self):
                return _FakeCursor()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr("eoa.patents.scan.connection", lambda: _FakeConnection())
        with patch("eoa.patents.scan._patent_exists", return_value=True):
            ids = upsert_records([PatentRecord(pub_number="US1")])
        assert ids == {"US1": 100}

    def test_existing_record_backfills_empty_assignees_via_coalesce_nullif(self, monkeypatch):
        """A repeat hit that *does* carry newly-found assignees/cpc issues a non-destructive
        UPDATE (COALESCE(NULLIF(...), new-value) -- never overwrites an already-populated field)."""
        update_calls = []

        class _FakeCursor:
            def execute(self, query, params=None):
                if "UPDATE patents" in query:
                    update_calls.append((query, params))
                return self

            def fetchone(self):
                return {"id": 100}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _FakeConnection:
            def cursor(self):
                return _FakeCursor()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr("eoa.patents.scan.connection", lambda: _FakeConnection())
        with patch("eoa.patents.scan._patent_exists", return_value=True):
            ids = upsert_records([PatentRecord(pub_number="US1", assignees=["Anduril"], cpc=["G01J5"])])
        assert ids == {"US1": 100}
        assert len(update_calls) == 1
        query, params = update_calls[0]
        assert "NULLIF(assignees" in query
        assert "NULLIF(cpc" in query
        assert params["assignees"] == ["Anduril"]
        assert params["cpc"] == ["G01J5"]


class TestScanPatentsOrchestration:
    def test_topic_and_assignee_scans_insert_new_records(self):
        """A record with the same pub_number surfacing from both the topic and the assignee query
        is deduped across the whole scan run (one shared `seen_pub_numbers`, mirroring
        eoa.tenders.scan's `seen_refs` convention) -- inserted (and _insert_patent called) once,
        not once per query."""
        rec = PatentRecord(pub_number="US1", title="t")
        with (
            patch("eoa.patents.scan._structured_sources_available", return_value=False),
            patch("eoa.patents.scan._records_for_query", return_value=[rec]),
            patch("eoa.patents.scan._any_patents_exist", return_value=True),
            patch("eoa.patents.scan._patent_exists", return_value=False),
            patch("eoa.patents.scan._insert_patent", return_value=1) as mock_insert,
        ):
            stats = scan_patents(
                topics=[WatchTopic(name_he="t", query="q")], assignees=["Elbit"], since_days=30
            )
        assert stats.topics_scanned == 1
        assert stats.assignees_scanned == 1
        assert stats.inserted == 1
        assert mock_insert.call_count == 1

    def test_distinct_records_across_topic_and_assignee_both_inserted(self):
        topic_rec = PatentRecord(pub_number="US1", title="t")
        assignee_rec = PatentRecord(pub_number="US2", title="t2")

        def side_effect(query, *, assignee, structured_ok):
            return [assignee_rec] if assignee else [topic_rec]

        with (
            patch("eoa.patents.scan._structured_sources_available", return_value=False),
            patch("eoa.patents.scan._records_for_query", side_effect=side_effect),
            patch("eoa.patents.scan._any_patents_exist", return_value=True),
            patch("eoa.patents.scan._patent_exists", return_value=False),
            patch("eoa.patents.scan._insert_patent", return_value=1) as mock_insert,
        ):
            stats = scan_patents(
                topics=[WatchTopic(name_he="t", query="q")], assignees=["Elbit"], since_days=30
            )
        assert stats.inserted == 2
        assert mock_insert.call_count == 2

    def test_duplicate_pub_number_across_queries_counted_once(self):
        rec = PatentRecord(pub_number="US1", title="t")
        with (
            patch("eoa.patents.scan._structured_sources_available", return_value=False),
            patch("eoa.patents.scan._records_for_query", return_value=[rec]),
            patch("eoa.patents.scan._any_patents_exist", return_value=True),
        ):
            stats = scan_patents(topics=[WatchTopic(name_he="t", query="q")], assignees=[], since_days=30)
        # seen_pub_numbers dedupes within the same call to _ingest_records only once per query, so
        # a single topic query with one record inserts exactly once when the DB has never seen it.
        assert stats.records_fetched == 1

    def test_query_failure_does_not_stop_scan(self):
        with (
            patch("eoa.patents.scan._structured_sources_available", return_value=False),
            patch("eoa.patents.scan._any_patents_exist", return_value=True),
        ):

            def side_effect(query, **kwargs):
                if query == "bad":
                    raise RuntimeError("boom")
                return []

            with patch("eoa.patents.scan._records_for_query", side_effect=side_effect):
                stats = scan_patents(
                    topics=[WatchTopic(name_he="bad", query="bad"), WatchTopic(name_he="good", query="good")],
                    assignees=[],
                    since_days=30,
                )
        assert stats.queries_failed == 1
        assert stats.topics_scanned == 1
