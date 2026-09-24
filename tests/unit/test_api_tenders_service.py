"""Tests for eoa.api.services.list_tenders (F24, docs/QA_PROGRAM.md section 4, 2026-09-06):
default status/since_days narrowing, include_closed/include_archived widening, and the
status-count summary returned for the UI's header chips. _fetchall is monkeypatched -- no DB."""

from __future__ import annotations

from unittest.mock import patch

from eoa.api import services


def _fake_fetchall(rows_by_call):
    """Return successive canned results for successive _fetchall calls (list_tenders makes
    exactly two: the tender rows, then the status-count rows)."""
    calls = iter(rows_by_call)

    def _fn(query, params=None):
        return next(calls)

    return _fn


_TENDER_WINDOW_SQL = (
    "COALESCE(deadline, (published_at AT TIME ZONE 'Asia/Jerusalem')::date, "
    "(created_at AT TIME ZONE 'Asia/Jerusalem')::date) >="
)


class TestListTendersDefaultView:
    def test_default_status_is_open_and_unknown_only(self):
        with patch("eoa.api.services._fetchall", side_effect=_fake_fetchall([[], []])) as mock_fetchall:
            services.list_tenders()
        query, params = mock_fetchall.call_args_list[0].args
        assert "status = ANY(%(statuses)s)" in query
        assert set(params["statuses"]) == {"open", "unknown"}

    def test_default_applies_since_days_window(self):
        with patch("eoa.api.services._fetchall", side_effect=_fake_fetchall([[], []])) as mock_fetchall:
            services.list_tenders()
        query, params = mock_fetchall.call_args_list[0].args
        assert _TENDER_WINDOW_SQL in query
        assert params["since_days"] == services.DEFAULT_SINCE_DAYS

    def test_include_closed_widens_default_statuses(self):
        with patch("eoa.api.services._fetchall", side_effect=_fake_fetchall([[], []])) as mock_fetchall:
            services.list_tenders(include_closed=True)
        _, params = mock_fetchall.call_args_list[0].args
        assert set(params["statuses"]) == {"open", "unknown", "closed"}

    def test_include_archived_widens_default_statuses(self):
        with patch("eoa.api.services._fetchall", side_effect=_fake_fetchall([[], []])) as mock_fetchall:
            services.list_tenders(include_archived=True)
        _, params = mock_fetchall.call_args_list[0].args
        assert set(params["statuses"]) == {"open", "unknown", "archived"}

    def test_include_closed_without_explicit_since_days_lifts_the_window(self):
        """Q5-11 (docs/qa/findings_Q5_r2.md): closed tenders are old by definition -- asking to see
        them must not leave the 90-day window in place, or the toggle reveals nothing."""
        with patch("eoa.api.services._fetchall", side_effect=_fake_fetchall([[], []])) as mock_fetchall:
            services.list_tenders(include_closed=True)
        query, params = mock_fetchall.call_args_list[0].args
        assert "since_days" not in params
        assert _TENDER_WINDOW_SQL not in query

    def test_include_archived_without_explicit_since_days_lifts_the_window(self):
        with patch("eoa.api.services._fetchall", side_effect=_fake_fetchall([[], []])) as mock_fetchall:
            services.list_tenders(include_archived=True)
        query, params = mock_fetchall.call_args_list[0].args
        assert "since_days" not in params
        assert _TENDER_WINDOW_SQL not in query

    def test_include_closed_with_explicit_since_days_still_applies_it(self):
        """An explicit since_days always wins, include_* or not."""
        with patch("eoa.api.services._fetchall", side_effect=_fake_fetchall([[], []])) as mock_fetchall:
            services.list_tenders(include_closed=True, since_days=30)
        query, params = mock_fetchall.call_args_list[0].args
        assert _TENDER_WINDOW_SQL in query
        assert params["since_days"] == 30

    def test_explicit_status_bypasses_default_set_and_since_days(self):
        """An operator who explicitly asks for status=closed wants every closed tender, not just
        recent ones -- since_days only narrows the default (no explicit status) view."""
        with patch("eoa.api.services._fetchall", side_effect=_fake_fetchall([[], []])) as mock_fetchall:
            services.list_tenders(status="closed")
        query, params = mock_fetchall.call_args_list[0].args
        assert "status = %(status)s" in query
        assert params["status"] == "closed"
        assert "since_days" not in params

    def test_never_shown_as_open_means_unknown_is_a_distinct_status(self):
        """F24: an 'unknown' tender must never be indistinguishable from 'open' in the response --
        each row keeps its own real status."""
        row = {"id": 1, "status": "unknown", "title": "t"}
        with patch("eoa.api.services._fetchall", side_effect=_fake_fetchall([[row], []])):
            result = services.list_tenders()
        assert result["tenders"][0]["status"] == "unknown"


class TestListTendersCounts:
    def test_returns_counts_by_status(self):
        count_rows = [
            {"status": "open", "n": 5},
            {"status": "unknown", "n": 2},
            {"status": "closed", "n": 8},
            {"status": "archived", "n": 3},
        ]
        with patch("eoa.api.services._fetchall", side_effect=_fake_fetchall([[], count_rows])):
            result = services.list_tenders()
        assert result["counts"] == {"open": 5, "unknown": 2, "closed": 8, "archived": 3}

    def test_counts_query_ignores_status_narrowing(self):
        """The count summary must reflect the TRUE totals regardless of the list's own
        status/since_days/include_* filtering -- that's the whole point of a header chip."""
        with patch("eoa.api.services._fetchall", side_effect=_fake_fetchall([[], []])) as mock_fetchall:
            services.list_tenders(status="closed")
        count_query, count_params = mock_fetchall.call_args_list[1].args
        assert "status" not in count_params
        assert "GROUP BY status" in count_query

    def test_counts_honor_country_and_q_filters(self):
        with patch("eoa.api.services._fetchall", side_effect=_fake_fetchall([[], []])) as mock_fetchall:
            services.list_tenders(country="US", q="infrared")
        _count_query, count_params = mock_fetchall.call_args_list[1].args
        assert count_params["country"] == "US"
        assert count_params["q"] == "%infrared%"

    def test_response_shape_has_tenders_and_counts_keys(self):
        with patch("eoa.api.services._fetchall", side_effect=_fake_fetchall([[], []])):
            result = services.list_tenders()
        assert set(result.keys()) == {"tenders", "counts"}
        assert result["tenders"] == []
        assert result["counts"] == {}


class TestTenderSourceStatus:
    """Unit tests for the pure classification helper (A15, docs/TENDER_PORTALS.md)."""

    def test_search_kind_always_integrated_keyless(self):
        assert services._tender_source_status("search", False, None) == "integrated_keyless"
        assert services._tender_source_status("search", True, None) == "integrated_keyless"

    def test_verified_api_json_is_integrated_keyless(self):
        assert services._tender_source_status("api_json", True, None) == "integrated_keyless"

    def test_unverified_with_key_env_var_is_waiting_for_key(self):
        assert services._tender_source_status("api_json", False, "SAM_GOV_API_KEY") == "waiting_for_key"

    def test_unverified_without_key_env_var_is_not_integrated(self):
        assert services._tender_source_status("api_json", False, None) == "not_integrated"
        assert services._tender_source_status("html", False, None) == "not_integrated"

    def test_html_kind_is_never_integrated_keyless_even_if_verified(self):
        """scan_tenders unconditionally skips every kind: html source -- a verified: true html
        entry (e.g. canada_buys, whose own page loads fine but is never scraped directly) must
        still report not_integrated, not integrated_keyless."""
        assert services._tender_source_status("html", True, None) == "not_integrated"


class TestTenderSourceCoverage:
    """A15 (docs/TENDER_PORTALS.md): the coverage panel's backing service -- combines the real
    config/tenders.yaml registry with a (monkeypatched) DB rollup. Uses the real
    load_tender_sources() (a config-file read, not a DB call) so a config typo would be caught."""

    def test_totals_sum_to_source_count(self):
        with (
            patch("eoa.api.services._fetchall", return_value=[]),
            patch("eoa.tenders.feedback.get_source_priorities", return_value={}),
        ):
            cov = services.tender_source_coverage()
        assert sum(cov["totals"].values()) == cov["source_count"]
        assert cov["source_count"] > 30

    def test_every_source_has_a_status_and_region(self):
        with (
            patch("eoa.api.services._fetchall", return_value=[]),
            patch("eoa.tenders.feedback.get_source_priorities", return_value={}),
        ):
            cov = services.tender_source_coverage()
        all_ids = set()
        for region in cov["regions"]:
            assert region["sources"], f"region {region['region']} has no sources"
            for src in region["sources"]:
                assert src["status"] in ("integrated_keyless", "waiting_for_key", "not_integrated")
                # W2b (additive): every source also reports its self-tuning scan-priority decrement.
                assert src["priority_decrement"] == 0
                all_ids.add(src["id"])
        assert "ted_eu" in all_ids
        assert "uk_find_tender" in all_ids

    def test_sam_gov_api_flagged_waiting_for_key(self):
        with (
            patch("eoa.api.services._fetchall", return_value=[]),
            patch("eoa.tenders.feedback.get_source_priorities", return_value={}),
        ):
            cov = services.tender_source_coverage()
        us_sources = next(r["sources"] for r in cov["regions"] if r["region"] == "US")
        sam = next(s for s in us_sources if s["id"] == "sam_gov_api")
        assert sam["status"] == "waiting_for_key"
        assert sam["needs_key_env_var"] == "SAM_GOV_API_KEY"

    def test_notice_counts_and_last_fetch_merged_from_db(self):
        fake_rows = [
            {"source": "ted_eu", "n": 7, "last_created_at": "2026-09-05T10:00:00+00:00"},
        ]
        with (
            patch("eoa.api.services._fetchall", return_value=fake_rows),
            patch("eoa.tenders.feedback.get_source_priorities", return_value={}),
        ):
            cov = services.tender_source_coverage()
        eu_sources = next(r["sources"] for r in cov["regions"] if r["region"] == "EU")
        ted = next(s for s in eu_sources if s["id"] == "ted_eu")
        assert ted["notices_stored"] == 7
        assert ted["last_fetch_at"] == "2026-09-05T10:00:00+00:00"

    def test_source_with_no_stored_notices_shows_zero_and_null(self):
        with (
            patch("eoa.api.services._fetchall", return_value=[]),
            patch("eoa.tenders.feedback.get_source_priorities", return_value={}),
        ):
            cov = services.tender_source_coverage()
        eu_sources = next(r["sources"] for r in cov["regions"] if r["region"] == "EU")
        ted = next(s for s in eu_sources if s["id"] == "ted_eu")
        assert ted["notices_stored"] == 0
        assert ted["last_fetch_at"] is None

    def test_config_load_failure_degrades_to_empty_not_an_exception(self):
        with (
            patch("eoa.tenders.scan.load_tender_sources", side_effect=RuntimeError("bad yaml")),
            patch("eoa.api.services._fetchall", return_value=[]),
            patch("eoa.tenders.feedback.get_source_priorities", return_value={}),
        ):
            cov = services.tender_source_coverage()
        assert cov["source_count"] == 0
        assert cov["regions"] == []

    def test_source_priority_decrement_surfaced_per_source(self):
        """W2b: a source that has earned a scan-priority decrement (eoa.tenders.feedback) shows it
        on the coverage panel -- additive, never affects status/verified/etc."""
        with (
            patch("eoa.api.services._fetchall", return_value=[]),
            patch("eoa.tenders.feedback.get_source_priorities", return_value={"ted_eu": -1}),
        ):
            cov = services.tender_source_coverage()
        eu_sources = next(r["sources"] for r in cov["regions"] if r["region"] == "EU")
        ted = next(s for s in eu_sources if s["id"] == "ted_eu")
        assert ted["priority_decrement"] == -1


class TestTenderFeedbackService:
    """W2b: services.record_tender_feedback/list_tender_feedback are thin pass-throughs to
    eoa.tenders.feedback -- verified here as a request-shaped contract test (real logic is
    exercised in tests/unit/test_tender_feedback_round4.py)."""

    def test_record_tender_feedback_delegates_to_feedback_module(self):
        with patch(
            "eoa.tenders.feedback.record_feedback", return_value={"id": 1, "verdict": "relevant"}
        ) as mock_record:
            result = services.record_tender_feedback(42, "relevant", "good notice")
        mock_record.assert_called_once_with(42, "relevant", "good notice")
        assert result == {"id": 1, "verdict": "relevant"}

    def test_record_tender_feedback_returns_none_for_unknown_tender(self):
        with patch("eoa.tenders.feedback.record_feedback", return_value=None):
            assert services.record_tender_feedback(999, "relevant", None) is None

    def test_list_tender_feedback_delegates_to_feedback_module(self):
        rows = [{"id": 1, "verdict": "relevant"}]
        with patch("eoa.tenders.feedback.list_feedback_for_tender", return_value=rows) as mock_list:
            result = services.list_tender_feedback(42)
        mock_list.assert_called_once_with(42)
        assert result == rows
