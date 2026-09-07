"""R7-tenders-b (follow-up to R7-tenders, docs/qa/loop/round_7_fixes.md "### R7-tenders status")
-- pure logic + parsing tests, no DB/network. Every HTTP call is monkeypatched, mirroring
tests/unit/test_tenders_round7.py's own stubbing style.

Covers:
  - finding 1 (TED returns its archive, not recent notices): the live-probed `PD>={since_date}`
    date clause / `SORT BY PD DESC` / `{page}` substitution in ``_fetch_api_json``, its per-source
    ``max_pages`` loop and early-stop-on-empty-page behaviour, and ``_parse_ted_notices``'
    new ``deadline-receipt-request`` extraction feeding ``_within_window``'s new
    deadline-already-passed exclusion;
  - finding 2 (UK Contracts Finder / Find a Tender 429 rate limiting): ``_is_rate_limited_error``,
    ``_call_with_rate_limit_backoff``'s exponential-backoff retry, and ``_collect_source_notices``'
    new inter-keyword ``pace_seconds`` pacing;
  - backward compatibility: every existing call site (``_fetch_api_json(src, keyword)`` with
    exactly two positional arguments, ``_collect_source_notices(src, deny_domains)`` with exactly
    two positional arguments) is unchanged, so tests/unit/test_tenders_scan.py's own
    ``TestCollectSourceNoticesApiQueryKeywordsOverride`` (which monkeypatches ``_fetch_api_json``
    with a bare ``(src_arg, keyword)`` stub) keeps working unmodified.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import httpx
import pytest

from eoa.tenders.scan import (
    NoticeRaw,
    TenderSource,
    _call_with_rate_limit_backoff,
    _collect_source_notices,
    _fetch_api_json,
    _is_rate_limited_error,
    _parse_ted_notices,
    _within_window,
)

DOMAIN_KEYWORDS = ["electro-optical", "infrared", "targeting pod", "seeker", "ATR"]


def _ted_source(**overrides) -> TenderSource:
    base = dict(
        id="ted_eu",
        name="TED",
        kind="api_json",
        country="EU",
        url="https://api.ted.europa.eu/v3/notices/search",
        method="POST",
        query_template=(
            '{"query":"FT ~ \\"{keyword}\\" AND PD>={since_date} SORT BY PD DESC",'
            '"fields":["ND","TI","PD","deadline-receipt-request"],"limit":20,"page":{page}}'
        ),
        keywords=DOMAIN_KEYWORDS,
        verified=True,
    )
    base.update(overrides)
    return TenderSource(**base)


def _cf_source(**overrides) -> TenderSource:
    base = dict(
        id="uk_contracts_finder",
        name="UK Contracts Finder",
        kind="api_json",
        country="UK",
        url="https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search",
        query_params={"keyword": "{keyword}"},
        keywords=DOMAIN_KEYWORDS,
        verified=True,
    )
    base.update(overrides)
    return TenderSource(**base)


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"{status_code} error", request=request, response=response)


# --------------------------------------------------------------------------
# TenderSource defaults (backward compatibility)
# --------------------------------------------------------------------------


class TestTenderSourceNewFieldDefaults:
    def test_max_pages_defaults_to_one(self):
        assert _ted_source().max_pages == 1

    def test_pace_seconds_defaults_to_zero(self):
        assert _ted_source().pace_seconds == 0.0

    def test_query_lookback_days_defaults_to_zero(self):
        assert _ted_source().query_lookback_days == 0


# --------------------------------------------------------------------------
# _is_rate_limited_error
# --------------------------------------------------------------------------


class TestIsRateLimitedError:
    def test_httpx_429_is_rate_limited(self):
        assert _is_rate_limited_error(_http_status_error(429)) is True

    def test_httpx_503_is_rate_limited(self):
        assert _is_rate_limited_error(_http_status_error(503)) is True

    def test_httpx_404_is_not_rate_limited(self):
        assert _is_rate_limited_error(_http_status_error(404)) is False

    def test_httpx_400_is_not_rate_limited(self):
        assert _is_rate_limited_error(_http_status_error(400)) is False

    def test_plain_exception_string_mentioning_429_is_rate_limited(self):
        """The isolated `agent` role surfaces a fetcher-job failure as a plain exception whose
        string still names the numeric status code (eoa.fetch.remote._wait_job) -- not an
        httpx.HTTPStatusError at all."""
        assert _is_rate_limited_error(Exception("fetcher job 7 failed: HTTP 429 Too Many Requests")) is True

    def test_plain_exception_string_mentioning_503_is_rate_limited(self):
        assert _is_rate_limited_error(Exception("fetcher job 8 failed: 503 Service Unavailable")) is True

    def test_unrelated_number_containing_429_substring_not_matched(self):
        """A standalone-number match, not a bare substring -- "44290" must not false-positive."""
        assert _is_rate_limited_error(Exception("connection to host 44290 refused")) is False

    def test_dns_failure_is_not_rate_limited(self):
        assert _is_rate_limited_error(Exception("dns failure for example.test: [Errno -3]")) is False


# --------------------------------------------------------------------------
# _call_with_rate_limit_backoff
# --------------------------------------------------------------------------


class TestCallWithRateLimitBackoff:
    def test_succeeds_first_try_no_sleep(self):
        src = _ted_source()
        calls = {"n": 0}

        def do_request():
            calls["n"] += 1
            return {"json": {"notices": []}}

        with patch("eoa.tenders.scan.time.sleep") as mock_sleep:
            result = _call_with_rate_limit_backoff(src, do_request)
        assert result == {"json": {"notices": []}}
        assert calls["n"] == 1
        mock_sleep.assert_not_called()

    def test_retries_once_on_429_then_succeeds(self):
        src = _ted_source(pace_seconds=1.0)
        attempts = {"n": 0}

        def do_request():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise _http_status_error(429)
            return {"json": {"notices": []}}

        with patch("eoa.tenders.scan.time.sleep") as mock_sleep:
            result = _call_with_rate_limit_backoff(src, do_request)
        assert result == {"json": {"notices": []}}
        assert attempts["n"] == 2
        mock_sleep.assert_called_once_with(1.0)  # pace_seconds * 2**0

    def test_exponential_backoff_sleep_values(self):
        src = _ted_source(pace_seconds=2.0)
        attempts = {"n": 0}

        def do_request():
            attempts["n"] += 1
            raise _http_status_error(503)

        with patch("eoa.tenders.scan.time.sleep") as mock_sleep, pytest.raises(httpx.HTTPStatusError):
            _call_with_rate_limit_backoff(src, do_request)
        # 3 attempts total -> 2 sleeps (before attempt 2 and attempt 3), doubling each time.
        assert mock_sleep.call_args_list == [((2.0,),), ((4.0,),)]
        assert attempts["n"] == 3

    def test_uses_default_base_sleep_when_pace_seconds_zero(self):
        src = _ted_source(pace_seconds=0.0)
        attempts = {"n": 0}

        def do_request():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise _http_status_error(429)
            return {"json": {}}

        with patch("eoa.tenders.scan.time.sleep") as mock_sleep:
            _call_with_rate_limit_backoff(src, do_request)
        mock_sleep.assert_called_once_with(2.0)  # _RATE_LIMIT_DEFAULT_BASE_SLEEP_S * 2**0

    def test_non_rate_limited_error_raises_immediately_no_retry(self):
        src = _ted_source()
        attempts = {"n": 0}

        def do_request():
            attempts["n"] += 1
            raise _http_status_error(400)

        with patch("eoa.tenders.scan.time.sleep") as mock_sleep, pytest.raises(httpx.HTTPStatusError):
            _call_with_rate_limit_backoff(src, do_request)
        assert attempts["n"] == 1
        mock_sleep.assert_not_called()

    def test_exhausts_all_attempts_then_raises(self):
        src = _ted_source()
        attempts = {"n": 0}

        def do_request():
            attempts["n"] += 1
            raise _http_status_error(429)

        with patch("eoa.tenders.scan.time.sleep"), pytest.raises(httpx.HTTPStatusError):
            _call_with_rate_limit_backoff(src, do_request)
        assert attempts["n"] == 3


# --------------------------------------------------------------------------
# _fetch_api_json: since_date/page substitution, pagination, backward compatibility
# --------------------------------------------------------------------------


class TestFetchApiJsonPagination:
    def test_single_page_default_calls_fetch_once(self):
        """Backward compatibility: a source with no max_pages override (the pydantic default, 1)
        must still call fetch_raw_remote exactly once, exactly like every pre-round-7-tenders-b
        _fetch_api_json call."""
        src = _ted_source()
        with patch("eoa.tenders.scan.fetch_raw_remote") as mock_fetch:
            mock_fetch.return_value = {"json": {"notices": []}}
            _fetch_api_json(src, "infrared")
        assert mock_fetch.call_count == 1

    def test_since_date_and_page_substituted_into_query_template(self):
        src = _ted_source(query_lookback_days=30)
        expected_since_date = (dt.date.today() - dt.timedelta(days=30)).strftime("%Y%m%d")
        with patch("eoa.tenders.scan.fetch_raw_remote") as mock_fetch:
            mock_fetch.return_value = {"json": {"notices": []}}
            _fetch_api_json(src, "infrared")
        body = mock_fetch.call_args.kwargs["json_body"]
        assert f"PD>={expected_since_date}" in body["query"]
        assert body["page"] == 1

    def test_since_date_empty_when_lookback_days_zero(self):
        src = _ted_source(query_lookback_days=0)
        with patch("eoa.tenders.scan.fetch_raw_remote") as mock_fetch:
            mock_fetch.return_value = {"json": {"notices": []}}
            _fetch_api_json(src, "infrared")
        body = mock_fetch.call_args.kwargs["json_body"]
        assert "PD>=" in body["query"]
        assert "PD>= SORT" in body["query"]  # substituted with an empty string, not omitted

    def test_pages_up_to_max_pages_and_concatenates(self):
        src = _ted_source(max_pages=3, pace_seconds=0.0)
        responses = [
            {"json": {"notices": [{"ND": "1-2026", "TI": {"eng": "a"}, "PD": "2026-09-01"}]}},
            {"json": {"notices": [{"ND": "2-2026", "TI": {"eng": "b"}, "PD": "2026-08-01"}]}},
            {"json": {"notices": [{"ND": "3-2026", "TI": {"eng": "c"}, "PD": "2026-07-01"}]}},
        ]
        with patch("eoa.tenders.scan.fetch_raw_remote", side_effect=responses) as mock_fetch:
            notices = _fetch_api_json(src, "infrared")
        assert mock_fetch.call_count == 3
        assert [n.external_ref for n in notices] == ["ted_eu:1-2026", "ted_eu:2-2026", "ted_eu:3-2026"]
        pages_requested = [mock_fetch.call_args_list[i].kwargs["json_body"]["page"] for i in range(3)]
        assert pages_requested == [1, 2, 3]

    def test_stops_early_on_empty_page(self):
        src = _ted_source(max_pages=5, pace_seconds=0.0)
        responses = [
            {"json": {"notices": [{"ND": "1-2026", "TI": {"eng": "a"}, "PD": "2026-09-01"}]}},
            {"json": {"notices": []}},
        ]
        with patch("eoa.tenders.scan.fetch_raw_remote", side_effect=responses) as mock_fetch:
            notices = _fetch_api_json(src, "infrared")
        assert mock_fetch.call_count == 2  # never reaches page 3, 4, 5
        assert len(notices) == 1

    def test_pace_seconds_sleeps_between_pages_not_before_first(self):
        src = _ted_source(max_pages=2, pace_seconds=3.0)
        responses = [
            {"json": {"notices": [{"ND": "1-2026", "TI": {"eng": "a"}, "PD": "2026-09-01"}]}},
            {"json": {"notices": []}},
        ]
        with (
            patch("eoa.tenders.scan.fetch_raw_remote", side_effect=responses),
            patch("eoa.tenders.scan.time.sleep") as mock_sleep,
        ):
            _fetch_api_json(src, "infrared")
        mock_sleep.assert_called_once_with(3.0)

    def test_since_date_and_page_substituted_into_query_params(self):
        src = _cf_source(query_params={"keyword": "{keyword}", "from": "{since_date}", "page": "{page}"})
        with patch("eoa.tenders.scan.fetch_raw_remote") as mock_fetch:
            mock_fetch.return_value = {"json": {"releases": []}}
            _fetch_api_json(src, "infrared")
        called_url = mock_fetch.call_args[0][0]
        assert "page=1" in called_url


# --------------------------------------------------------------------------
# _collect_source_notices: inter-keyword pacing (finding 2)
# --------------------------------------------------------------------------


class TestCollectSourceNoticesPacing:
    def test_no_sleep_between_keywords_when_pace_seconds_zero(self):
        src = _cf_source(pace_seconds=0.0)
        with (
            patch("eoa.tenders.scan._fetch_api_json", return_value=[]),
            patch("eoa.tenders.scan.time.sleep") as mock_sleep,
        ):
            _collect_source_notices(src, [])
        mock_sleep.assert_not_called()

    def test_sleeps_between_keywords_when_pace_seconds_set(self):
        src = _cf_source(pace_seconds=2.0)  # DOMAIN_KEYWORDS has 5 entries -> 4 gaps
        with (
            patch("eoa.tenders.scan._fetch_api_json", return_value=[]),
            patch("eoa.tenders.scan.time.sleep") as mock_sleep,
        ):
            _collect_source_notices(src, [])
        assert mock_sleep.call_count == 4
        mock_sleep.assert_called_with(2.0)

    def test_two_positional_args_call_still_works(self):
        """Backward compatibility: the exact call shape used by
        tests/unit/test_tenders_scan.py's own TestCollectSourceNoticesApiQueryKeywordsOverride."""
        src = _ted_source()
        seen_keywords: list[str] = []

        def fake_fetch(src_arg, keyword):
            seen_keywords.append(keyword)
            return []

        with patch("eoa.tenders.scan._fetch_api_json", side_effect=fake_fetch):
            _collect_source_notices(src, [])
        assert seen_keywords == DOMAIN_KEYWORDS


# --------------------------------------------------------------------------
# _parse_ted_notices: deadline-receipt-request extraction
# --------------------------------------------------------------------------


class TestParseTedDeadlineExtraction:
    def _src(self):
        return _ted_source()

    def test_extracts_first_deadline_from_list(self):
        payload = {
            "notices": [
                {
                    "ND": "1-2026",
                    "TI": {"eng": "x"},
                    "PD": "2026-09-01",
                    "deadline-receipt-request": ["2026-09-30T10:00:00+02:00", "2026-10-01T10:00:00+02:00"],
                }
            ]
        }
        notices = _parse_ted_notices(payload, self._src())
        assert notices[0].deadline == dt.date(2026, 9, 30)

    def test_missing_deadline_defaults_none(self):
        payload = {"notices": [{"ND": "1-2026", "TI": {"eng": "x"}, "PD": "2026-09-01"}]}
        notices = _parse_ted_notices(payload, self._src())
        assert notices[0].deadline is None

    def test_empty_deadline_list_defaults_none(self):
        payload = {
            "notices": [
                {"ND": "1-2026", "TI": {"eng": "x"}, "PD": "2026-09-01", "deadline-receipt-request": []}
            ]
        }
        notices = _parse_ted_notices(payload, self._src())
        assert notices[0].deadline is None

    def test_scalar_deadline_value_also_handled(self):
        """Defensive -- TED's own documented shape is always a list, but a bare string should
        still parse rather than crash if a future API revision ever returns one directly."""
        payload = {
            "notices": [
                {
                    "ND": "1-2026",
                    "TI": {"eng": "x"},
                    "PD": "2026-09-01",
                    "deadline-receipt-request": "2026-09-30T10:00:00+02:00",
                }
            ]
        }
        notices = _parse_ted_notices(payload, self._src())
        assert notices[0].deadline == dt.date(2026, 9, 30)


# --------------------------------------------------------------------------
# _within_window: deadline-already-passed exclusion
# --------------------------------------------------------------------------


class TestWithinWindowDeadlineExclusion:
    def test_past_deadline_excluded_even_with_recent_published_at(self):
        n = NoticeRaw(
            source_id="ted_eu",
            external_ref="x:1",
            title="t",
            published_at=dt.date(2026, 9, 5),
            deadline=dt.date(2026, 9, 1),
        )
        assert _within_window(n, since_days=14, today=dt.date(2026, 9, 7)) is False

    def test_future_deadline_not_excluded(self):
        n = NoticeRaw(
            source_id="ted_eu",
            external_ref="x:1",
            title="t",
            published_at=dt.date(2026, 9, 5),
            deadline=dt.date(2026, 9, 20),
        )
        assert _within_window(n, since_days=14, today=dt.date(2026, 9, 7)) is True

    def test_deadline_of_exactly_today_not_excluded(self):
        n = NoticeRaw(
            source_id="ted_eu", external_ref="x:1", title="t", published_at=None, deadline=dt.date(2026, 9, 7)
        )
        assert _within_window(n, since_days=14, today=dt.date(2026, 9, 7)) is True

    def test_no_deadline_unaffected_recent_published_at(self):
        n = NoticeRaw(source_id="ted_eu", external_ref="x:1", title="t", published_at=dt.date(2026, 9, 5))
        assert _within_window(n, since_days=14, today=dt.date(2026, 9, 7)) is True

    def test_no_deadline_no_published_at_still_within_window(self):
        n = NoticeRaw(source_id="ted_eu", external_ref="x:1", title="t")
        assert _within_window(n, since_days=14, today=dt.date(2026, 9, 7)) is True

    def test_past_deadline_and_old_published_at_still_excluded_by_deadline_not_just_date(self):
        """Both checks would reject this notice -- confirms the deadline check doesn't accidentally
        short-circuit in a way that hides a real published_at-window failure either."""
        n = NoticeRaw(
            source_id="ted_eu",
            external_ref="x:1",
            title="t",
            published_at=dt.date(2016, 1, 1),
            deadline=dt.date(2016, 2, 1),
        )
        assert _within_window(n, since_days=14, today=dt.date(2026, 9, 7)) is False
