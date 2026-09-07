"""Tests for scripts/cleanup_round10.py (R10-cleanup, round-10 QA loop, 2026-09-07): the pure
detection/retention logic (no DB) plus a handful of SQL-shape orchestration tests against a
mocked cursor -- same convention as tests/unit/test_purge_stale_tenders.py's own
``_FakeCursor``/``_FakeConnection`` pair, generalized here to dispatch a canned result by matching
a substring of the executed SQL (each category issues several distinct statements per call, so a
single fixed ``fetchall_result`` isn't enough)."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cleanup_round10 import (
    COOKIE_CONSENT_PHRASES,
    cleanup_dedup_chains,
    cleanup_events,
    cleanup_stale_jobs,
    cleanup_watchlist,
    find_orphan_report_files,
    interstitial_match,
    investigation_log_row_is_disposable,
    purge_search_cache,
    report_group_key,
    reports_to_retire,
    resolve_dedup_root,
    superseded_error_note,
)

# ---------------------------------------------------------------------------------------------
# Category 1 pure helpers
# ---------------------------------------------------------------------------------------------


class TestSupersededErrorNote:
    def test_formats_the_brief_s_exact_fallback_text(self) -> None:
        assert superseded_error_note(145) == "[superseded by rerun 145]"


class TestInvestigationLogRowIsDisposable:
    def test_zero_reads_zero_answer_is_disposable(self) -> None:
        assert investigation_log_row_is_disposable(0, False) is True

    def test_zero_reads_but_has_answer_is_kept(self) -> None:
        """A job that logged no page-read count but still produced a real answer (e.g. answered
        purely from the trigger item's own text) must not have its trail deleted."""
        assert investigation_log_row_is_disposable(0, True) is False

    def test_nonzero_reads_is_kept_even_without_an_answer(self) -> None:
        assert investigation_log_row_is_disposable(3, False) is False

    def test_none_reads_treated_as_zero(self) -> None:
        assert investigation_log_row_is_disposable(None, False) is True


# ---------------------------------------------------------------------------------------------
# Category 2 pure helper: interstitial_match
# ---------------------------------------------------------------------------------------------


class TestInterstitialMatch:
    def test_cloudflare_challenge_text_matches(self) -> None:
        text = "This website is using a security service to protect itself from online attacks."
        assert interstitial_match(None, text) is not None

    def test_just_a_moment_matches(self) -> None:
        assert interstitial_match("Just a moment...", "Please wait while we verify you are human.") is not None

    def test_cookie_consent_wall_matches(self) -> None:
        match = interstitial_match(None, "We use cookies to improve your experience on this site.")
        assert match in COOKIE_CONSENT_PHRASES

    def test_hebrew_nav_shell_fingerprint_matches(self) -> None:
        """The empirically-confirmed israelhayom.co.il site-chrome fingerprint (19/20 of that
        source's stored items in the live DB turned out to be nothing but this nav bar)."""
        text = 'יום שני, 7.9.2026\nכ"ה באלול תשפ"ו\nחדשות\nדעות\nספורט\nForReal\nאנחנו מגייסים\nEnglish\nX'
        assert interstitial_match(None, text) == "אנחנו מגייסים"

    def test_long_real_article_mentioning_cookies_is_not_flagged(self) -> None:
        """The length-guard regression case: en.globes.co.il's own real, 16.7k-char privacy-policy
        article legitimately contains the phrase "use of cookies" -- it must NOT be treated as an
        interstitial just because it mentions cookies once, deep in real, long content."""
        long_article = "This article explains our privacy practices. " * 400 + " We describe our use of cookies here."
        assert len(long_article) > 2000
        assert interstitial_match("Privacy Policy", long_article) is None

    def test_legitimate_short_teaser_is_not_flagged(self) -> None:
        """A real, short RFI/tender teaser (no boilerplate phrase at all) must survive -- length
        alone is deliberately NOT a trigger (see the script's module docstring for why)."""
        text = (
            "Sniper advanced targeting pod (atp) Tender, ID 3340718 - usarfp\n\n"
            "The DEPARTMENT OF THE AIR FORCE has announced a new tender for Sniper advanced "
            "targeting pod (atp)."
        )
        assert interstitial_match("Sniper advanced targeting pod (atp) Tender", text) is None

    def test_empty_text_is_not_flagged(self) -> None:
        assert interstitial_match(None, None) is None
        assert interstitial_match("", "") is None


# ---------------------------------------------------------------------------------------------
# Category 3 pure helper: resolve_dedup_root
# ---------------------------------------------------------------------------------------------


class TestResolveDedupRoot:
    def test_one_hop_resolves_to_the_target(self) -> None:
        assert resolve_dedup_root(1, {1: 2}) == 2

    def test_multi_hop_chain_resolves_to_the_true_root(self) -> None:
        """The live-DB case: item 317 -> 309 -> 269 (269's own dedup_of is NULL, i.e. not a key)."""
        dedup_map = {317: 309, 309: 269}
        assert resolve_dedup_root(317, dedup_map) == 269

    def test_already_one_hop_from_root_is_unchanged(self) -> None:
        dedup_map = {317: 309, 309: 269}
        assert resolve_dedup_root(309, dedup_map) == 269

    def test_cycle_returns_none_rather_than_looping_forever(self) -> None:
        dedup_map = {1: 2, 2: 1}
        assert resolve_dedup_root(1, dedup_map) is None

    def test_chain_into_a_missing_item_is_unresolvable_this_pass(self) -> None:
        """When valid_ids is given, walking into an id that isn't a real row returns None -- the
        caller's separate missing-target check owns fixing that hop; a second (idempotent) run
        then converges the rest of the chain."""
        dedup_map = {1: 2}
        assert resolve_dedup_root(1, dedup_map, valid_ids={1}) is None


# ---------------------------------------------------------------------------------------------
# Category 5 pure helpers: report_group_key / reports_to_retire
# ---------------------------------------------------------------------------------------------


class TestReportGroupKey:
    def test_non_patent_survey_ignores_topic(self) -> None:
        key_a = report_group_key("daily", dt.date(2026, 9, 6), None, "some topic")
        key_b = report_group_key("daily", dt.date(2026, 9, 6), None, "a different topic")
        assert key_a == key_b

    def test_patent_survey_splits_by_topic(self) -> None:
        """The live-DB regression case: 17 passed patent_survey rows sharing one
        (kind, period_end, territory) tuple actually spanned 3 distinct topics."""
        key_a = report_group_key("patent_survey", dt.date(2026, 9, 6), None, "DROIC")
        key_b = report_group_key("patent_survey", dt.date(2026, 9, 6), None, "Anduril Lattice")
        assert key_a != key_b


def _report_row(id: int, qa_passed: bool, created_at: dt.datetime) -> dict:
    return {"id": id, "qa_passed": qa_passed, "created_at": created_at}


class TestReportsToRetire:
    def test_newest_row_is_never_retired_even_if_failed(self) -> None:
        rows = [_report_row(1, False, dt.datetime(2026, 9, 6, 10, 0))]
        assert reports_to_retire(rows) == []

    def test_older_failed_row_retired_once_a_newer_passed_row_exists(self) -> None:
        rows = [
            _report_row(1, False, dt.datetime(2026, 9, 6, 10, 0)),
            _report_row(2, True, dt.datetime(2026, 9, 6, 12, 0)),
        ]
        assert reports_to_retire(rows) == [1]

    def test_older_failed_row_kept_when_no_passed_row_exists_at_all(self) -> None:
        rows = [
            _report_row(1, False, dt.datetime(2026, 9, 6, 10, 0)),
            _report_row(2, False, dt.datetime(2026, 9, 6, 12, 0)),
        ]
        assert reports_to_retire(rows) == []

    def test_passed_rows_are_never_retired(self) -> None:
        rows = [
            _report_row(1, True, dt.datetime(2026, 9, 6, 10, 0)),
            _report_row(2, True, dt.datetime(2026, 9, 6, 12, 0)),
            _report_row(3, True, dt.datetime(2026, 9, 6, 14, 0)),
        ]
        assert reports_to_retire(rows) == []

    def test_failed_row_created_after_the_last_pass_is_kept_not_retired(self) -> None:
        """A rerun that failed AFTER an earlier pass is a new problem, not stale cruft from before
        the report passed QA -- only failed rows OLDER than the newest pass are cleaned up."""
        rows = [
            _report_row(1, True, dt.datetime(2026, 9, 6, 10, 0)),
            _report_row(2, False, dt.datetime(2026, 9, 6, 12, 0)),  # newest row, also happens to fail
        ]
        assert reports_to_retire(rows) == []

    def test_only_rows_older_than_the_newest_pass_are_retired(self) -> None:
        rows = [
            _report_row(1, False, dt.datetime(2026, 9, 6, 8, 0)),  # older than the pass -> retire
            _report_row(2, True, dt.datetime(2026, 9, 6, 10, 0)),  # the pass
            _report_row(3, False, dt.datetime(2026, 9, 6, 12, 0)),  # after the pass -> kept (newest, too)
        ]
        assert reports_to_retire(rows) == [1]


# ---------------------------------------------------------------------------------------------
# Categories 8/9: pure filesystem helpers (tmp_path, no DB)
# ---------------------------------------------------------------------------------------------


class TestPurgeSearchCache:
    def test_dry_run_reports_without_deleting(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "search"
        cache_dir.mkdir()
        old_file = cache_dir / "old.json"
        old_file.write_text("{}")
        _backdate(old_file, days=20)
        fresh_file = cache_dir / "fresh.json"
        fresh_file.write_text("{}")

        result = purge_search_cache(cache_dir, apply=False)

        assert result["checked"] == 2
        assert result["deleted"] == 1
        assert old_file.exists()  # nothing actually removed on a dry run

    def test_apply_deletes_only_files_older_than_14_days(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "search"
        cache_dir.mkdir()
        old_file = cache_dir / "old.json"
        old_file.write_text("{}")
        _backdate(old_file, days=20)
        fresh_file = cache_dir / "fresh.json"
        fresh_file.write_text("{}")

        result = purge_search_cache(cache_dir, apply=True)

        assert result["deleted"] == 1
        assert not old_file.exists()
        assert fresh_file.exists()

    def test_missing_cache_dir_is_a_no_op(self, tmp_path: Path) -> None:
        result = purge_search_cache(tmp_path / "does_not_exist", apply=True)
        assert result == {"checked": 0, "stale_files": [], "deleted": 0, "bytes_freed": 0, "apply": True}


class TestFindOrphanReportFiles:
    def test_referenced_file_is_never_an_orphan(self, tmp_path: Path) -> None:
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        f = reports_dir / "daily_2026-09-06.md"
        f.write_text("content")
        _backdate(f, days=5)

        referenced = {str(f.resolve()).lower()}
        assert find_orphan_report_files(reports_dir, referenced) == []

    def test_unreferenced_old_file_is_an_orphan(self, tmp_path: Path) -> None:
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        f = reports_dir / "sample_daily.docx"
        f.write_text("content")
        _backdate(f, days=3)

        assert find_orphan_report_files(reports_dir, referenced_paths=set()) == [f]

    def test_unreferenced_but_too_young_file_is_not_yet_an_orphan(self, tmp_path: Path) -> None:
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        f = reports_dir / "just_written.md"
        f.write_text("content")  # mtime = now, younger than the 1-day floor

        assert find_orphan_report_files(reports_dir, referenced_paths=set()) == []


def _backdate(path: Path, *, days: int) -> None:
    import os

    ts = (dt.datetime.now() - dt.timedelta(days=days)).timestamp()
    os.utime(path, (ts, ts))


# ---------------------------------------------------------------------------------------------
# SQL-shape orchestration tests against a mocked cursor
# ---------------------------------------------------------------------------------------------


class _DispatchCursor:
    """Returns a canned result for whichever registered SQL substring appears in the executed
    query (each script function issues several distinct statements per call, unlike
    test_purge_stale_tenders.py's single-query ``purge`` -- a fixed ``fetchall_result`` isn't
    enough here). ``responses`` is an ordered list of ``(substring, result)`` -- ``result`` is a
    list for a ``fetchall()`` caller, or a dict for a ``fetchone()`` caller. The first matching
    substring wins; unmatched queries return an empty list."""

    def __init__(self, responses: list[tuple[str, object]]):
        self._responses = responses
        self.executed: list[tuple[str, object]] = []
        self.rowcount = 0
        self._last: object = []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        for substr, result in self._responses:
            if substr in query:
                self._last = result
                if query.strip().upper().startswith(("DELETE", "UPDATE")):
                    self.rowcount = len(result) if isinstance(result, list) else 1
                return self
        self._last = []
        return self

    def fetchall(self):
        return self._last if isinstance(self._last, list) else []

    def fetchone(self):
        return self._last if isinstance(self._last, dict) else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, cursor: _DispatchCursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestCleanupStaleJobsOrchestration:
    def test_dry_run_finds_rerun_and_disposable_log_rows(self) -> None:
        cur = _DispatchCursor(
            [
                (
                    "kind = 'deep_search' AND state = 'failed'",
                    [{"id": 137, "error": "cannot import name 'X'", "has_result": False}],
                ),
                ("rerun_of_job_id')::bigint AS origin_id", [{"id": 145, "origin_id": 137}]),
                ("SELECT job_id, sum(COALESCE(pages_read", [{"job_id": 137, "reads": 0, "n": 3}]),
                ("SELECT count(*) AS n FROM investigation_log", {"n": 3}),
            ]
        )
        conn = _FakeConnection(cur)
        with patch("scripts.cleanup_round10.db.connection", return_value=conn):
            result = cleanup_stale_jobs(apply=False)

        assert result["failed_job_ids"] == [137]
        assert result["superseded"] == [{"job_id": 137, "rerun_job_id": 145, "new_error": "[superseded by rerun 145]"}]
        assert result["disposable_log_job_ids"] == [137]
        assert result["investigation_log_rows_deleted"] == 3
        # Dry run: no UPDATE/DELETE statement was issued.
        assert not any("UPDATE" in q or "DELETE" in q for q, _ in cur.executed)

    def test_apply_writes_the_superseded_note_and_deletes_log_rows(self) -> None:
        cur = _DispatchCursor(
            [
                (
                    "kind = 'deep_search' AND state = 'failed'",
                    [{"id": 137, "error": "cannot import name 'X'", "has_result": False}],
                ),
                ("rerun_of_job_id')::bigint AS origin_id", [{"id": 145, "origin_id": 137}]),
                ("SELECT job_id, sum(COALESCE(pages_read", [{"job_id": 137, "reads": 0, "n": 3}]),
                ("DELETE FROM investigation_log", [1, 2, 3]),
            ]
        )
        conn = _FakeConnection(cur)
        with patch("scripts.cleanup_round10.db.connection", return_value=conn):
            result = cleanup_stale_jobs(apply=True)

        assert result["investigation_log_rows_deleted"] == 3
        update_query = next(q for q, _ in cur.executed if "UPDATE jobs SET error" in q)
        assert update_query
        delete_query = next(q for q, _ in cur.executed if "DELETE FROM investigation_log" in q)
        assert delete_query

    def test_no_failed_jobs_is_a_clean_no_op(self) -> None:
        cur = _DispatchCursor([("kind = 'deep_search' AND state = 'failed'", [])])
        conn = _FakeConnection(cur)
        with patch("scripts.cleanup_round10.db.connection", return_value=conn):
            result = cleanup_stale_jobs(apply=True)

        assert result == {
            "failed_job_ids": [],
            "superseded": [],
            "disposable_log_job_ids": [],
            "investigation_log_rows_deleted": 0,
            "apply": True,
        }


class TestCleanupDedupChainsOrchestration:
    def test_apply_repoints_a_multi_hop_chain_at_its_root(self) -> None:
        cur = _DispatchCursor(
            [
                (
                    "SELECT id, dedup_of FROM items WHERE dedup_of IS NOT NULL",
                    [{"id": 317, "dedup_of": 309}, {"id": 309, "dedup_of": 269}],
                ),
                ("SELECT id FROM items", [{"id": 317}, {"id": 309}, {"id": 269}]),
            ]
        )
        conn = _FakeConnection(cur)
        with patch("scripts.cleanup_round10.db.connection", return_value=conn):
            result = cleanup_dedup_chains(apply=True)

        assert result["missing_target_fixes"] == []
        assert result["chain_fixes"] == [{"id": 317, "old_dedup_of": 309, "new_dedup_of": 269}]
        update_query = next(q for q, _ in cur.executed if "SET dedup_of = %(root)s" in q)
        assert update_query

    def test_missing_target_is_cleared_to_null(self) -> None:
        cur = _DispatchCursor(
            [
                ("SELECT id, dedup_of FROM items WHERE dedup_of IS NOT NULL", [{"id": 5, "dedup_of": 999}]),
                ("SELECT id FROM items", [{"id": 5}]),  # 999 no longer exists
            ]
        )
        conn = _FakeConnection(cur)
        with patch("scripts.cleanup_round10.db.connection", return_value=conn):
            result = cleanup_dedup_chains(apply=True)

        assert result["missing_target_fixes"] == [{"id": 5, "old_dedup_of": 999}]
        assert result["chain_fixes"] == []
        null_query = next(q for q, _ in cur.executed if "SET dedup_of = NULL" in q)
        assert null_query


class TestCleanupEventsOrchestration:
    def test_apply_merges_all_three_sub_categories_and_deletes_once(self) -> None:
        cur = _DispatchCursor(
            [
                ("e.item_id IS NOT NULL AND i.id IS NULL", [{"id": 900}]),  # orphan
                ("COALESCE(i.domain, 'out_of_scope')", [{"id": 265, "item_id": 22}]),  # out-of-scope
                ("HAVING count(*) > 1", [{"item_id": 1, "kind": "launch", "title": "T", "ids": [10, 11, 12]}]),
                ("DELETE FROM events", list(range(4))),  # 4 ids total deleted
            ]
        )
        conn = _FakeConnection(cur)
        with patch("scripts.cleanup_round10.db.connection", return_value=conn):
            result = cleanup_events(apply=True)

        assert result["orphan_item_id_ids"] == [900]
        assert result["out_of_scope_ids"] == [265]
        assert result["duplicate_ids"] == [11, 12]  # lowest id (10) kept
        assert result["total_delete_ids"] == sorted({900, 265, 11, 12})
        delete_query = next(q for q, _ in cur.executed if "DELETE FROM events" in q)
        assert delete_query


class TestCleanupWatchlistOrchestration:
    def test_apply_deletes_dropped_and_empty_text_rows(self) -> None:
        cur = _DispatchCursor(
            [
                ("status = 'dropped'", [{"id": 1}, {"id": 2}]),
                ("text_he IS NULL OR text_he = ''", [{"id": 3}]),
                ("DELETE FROM indicator_watchlist", [1, 2, 3]),
            ]
        )
        conn = _FakeConnection(cur)
        with patch("scripts.cleanup_round10.db.connection", return_value=conn):
            result = cleanup_watchlist(apply=True)

        assert result["dropped_stale_ids"] == [1, 2]
        assert result["empty_text_ids"] == [3]
        assert result["total_delete_ids"] == [1, 2, 3]
        assert result["deleted"] == 3

    def test_dry_run_reports_without_deleting(self) -> None:
        cur = _DispatchCursor(
            [
                ("status = 'dropped'", [{"id": 1}]),
                ("text_he IS NULL OR text_he = ''", []),
            ]
        )
        conn = _FakeConnection(cur)
        with patch("scripts.cleanup_round10.db.connection", return_value=conn):
            result = cleanup_watchlist(apply=False)

        assert result["deleted"] == 1  # counted for the report, nothing actually removed
        assert not any("DELETE" in q for q, _ in cur.executed)
