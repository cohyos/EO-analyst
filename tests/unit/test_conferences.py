"""Tests for eoa.conferences (FR-12) -- pure logic only, no DB/LLM.

Every DB- or network-touching function is monkeypatched at the module level (mirroring
tests/unit/test_triage_levels.py's stubbing style): tests exercise the occurrence math, status
transition, name-similarity dedupe, reminder due-date, and iCal-building logic directly.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

from icalendar import Calendar

from eoa.conferences.ical import build_ical
from eoa.conferences.reminders import due_reminders, send_reminders
from eoa.conferences.tracker import (
    _apply_conference_update,
    _is_near_duplicate,
    _is_past,
    _jsonable,
    _occurs_in_year,
    _parse_date,
    _relevance_score,
    _transition_past,
    _year_from_name,
    _years_in_horizon,
    conference_card,
    roll_horizon,
)
from eoa.llm.schemas.conferences import ConferenceCandidate

# --------------------------------------------------------------------------
# roll_horizon occurrence math (annual / biennial_odd / biennial_even)
# --------------------------------------------------------------------------


class TestOccursInYear:
    def test_annual_always_true(self):
        for year in (2025, 2026, 2027, 2030):
            assert _occurs_in_year("annual", year) is True

    def test_missing_cadence_defaults_annual(self):
        assert _occurs_in_year(None, 2026) is True
        assert _occurs_in_year("", 2027) is True

    def test_biennial_odd(self):
        assert _occurs_in_year("biennial_odd", 2025) is True
        assert _occurs_in_year("biennial_odd", 2026) is False
        assert _occurs_in_year("biennial_odd", 2027) is True

    def test_biennial_even(self):
        assert _occurs_in_year("biennial_even", 2026) is True
        assert _occurs_in_year("biennial_even", 2027) is False
        assert _occurs_in_year("biennial_even", 2028) is True

    def test_plain_biennial_defaults_to_even(self):
        """ISDEF-style seed entries (cadence: biennial, no parity) default to even years."""
        assert _occurs_in_year("biennial", 2026) is True
        assert _occurs_in_year("biennial", 2027) is False

    def test_case_insensitive(self):
        assert _occurs_in_year("BIENNIAL_ODD", 2025) is True

    def test_unknown_cadence_never_dropped(self):
        assert _occurs_in_year("quarterly", 2026) is True


class TestYearsInHorizon:
    def test_annual_across_two_year_boundary(self):
        """24-month horizon from a September start date covers this year's and next October,
        but not the one just past the horizon end (Oct 2028 is after the Sep 2028 cutoff)."""
        today = dt.date(2026, 9, 4)
        horizon_end = dt.date(2028, 9, 4)
        years = _years_in_horizon("annual", 10, today, horizon_end)
        assert years == [2026, 2027]

    def test_month_already_passed_this_year_is_skipped(self):
        """If today is after the seed month this year, this year's occurrence is not re-added."""
        today = dt.date(2026, 9, 4)
        horizon_end = dt.date(2028, 9, 4)
        years = _years_in_horizon("annual", 6, today, horizon_end)  # June < September
        assert years == [2027, 2028]

    def test_month_not_yet_passed_this_year_is_included(self):
        today = dt.date(2026, 9, 4)
        horizon_end = dt.date(2028, 9, 4)
        years = _years_in_horizon("annual", 10, today, horizon_end)  # October > September
        assert 2026 in years

    def test_biennial_odd_across_horizon(self):
        today = dt.date(2026, 1, 1)
        horizon_end = dt.date(2030, 1, 1)
        years = _years_in_horizon("biennial_odd", 9, today, horizon_end)
        assert years == [2027, 2029]

    def test_biennial_even_across_horizon(self):
        """The 2030 occurrence (June) falls after the Jan-2030 horizon cutoff, so only 2026/2028
        are included -- the exact cadence-parity + cutoff interaction the occurrence math must get right."""
        today = dt.date(2026, 1, 1)
        horizon_end = dt.date(2030, 1, 1)
        years = _years_in_horizon("biennial_even", 6, today, horizon_end)
        assert years == [2026, 2028]

    def test_candidate_beyond_horizon_end_excluded(self):
        today = dt.date(2026, 1, 1)
        horizon_end = dt.date(2026, 6, 1)
        years = _years_in_horizon("annual", 10, today, horizon_end)  # October is after June
        assert years == []

    def test_empty_when_nothing_matches(self):
        today = dt.date(2026, 1, 1)
        horizon_end = dt.date(2026, 3, 1)
        assert _years_in_horizon("biennial_odd", 6, today, horizon_end) == []


class TestRollHorizonSeeds:
    def test_reads_watchlist_conferences_seed_and_inserts_per_occurrence(self):
        """roll_horizon() reads watchlist.conferences_seed, computes occurrences via
        _years_in_horizon, and calls the DB insert once per (name, year); duplicates
        (ON CONFLICT DO NOTHING, simulated by returning None the 2nd time) are counted as skipped."""
        mock_settings = MagicMock()
        mock_settings.watchlist = {
            "conferences_seed": [
                {"name": "AUSA", "month": 10, "city": "Washington", "relevance": 4},
            ]
        }
        today = dt.date(2026, 9, 4)
        expected_years = _years_in_horizon("annual", 10, today, today + _relativedelta_24mo())
        seen_names: set[str] = set()

        def fake_fetchone(query, params=None):
            if "INSERT INTO conferences" in query:
                if params["name"] in seen_names:
                    return None
                seen_names.add(params["name"])
                return {"id": len(seen_names)}
            return None

        with (
            patch("eoa.conferences.tracker.settings", return_value=mock_settings),
            patch("eoa.conferences.tracker._fetchone", side_effect=fake_fetchone),
            patch("eoa.conferences.tracker._fetchall", return_value=[]),
            patch("eoa.conferences.tracker.dt") as mock_dt,
        ):
            mock_dt.date.today.return_value = today
            mock_dt.date.side_effect = lambda *a, **kw: dt.date(*a, **kw)
            mock_dt.timedelta = dt.timedelta

            result = roll_horizon(months=24)

        assert result["created"] == len(expected_years)
        assert seen_names == {f"AUSA {y}" for y in expected_years}

    def test_missing_name_or_month_skipped(self):
        mock_settings = MagicMock()
        mock_settings.watchlist = {"conferences_seed": [{"city": "Nowhere"}]}
        with (
            patch("eoa.conferences.tracker.settings", return_value=mock_settings),
            patch("eoa.conferences.tracker._fetchone") as mock_insert,
            patch("eoa.conferences.tracker._fetchall", return_value=[]),
        ):
            result = roll_horizon(months=24)
        mock_insert.assert_not_called()
        assert result["created"] == 0


def _relativedelta_24mo() -> dt.timedelta:
    # Rough same-order-of-magnitude offset only used to compute the *expected* year set in the
    # test above via the real _years_in_horizon; relativedelta itself is exercised by roll_horizon.
    from dateutil.relativedelta import relativedelta

    return relativedelta(months=24)


# --------------------------------------------------------------------------
# status transitions
# --------------------------------------------------------------------------


class TestIsPast:
    def test_past_end_date(self):
        row = {"start_date": dt.date(2025, 1, 1), "end_date": dt.date(2025, 1, 5)}
        assert _is_past(row, dt.date(2026, 1, 1)) is True

    def test_future_end_date(self):
        row = {"start_date": dt.date(2027, 1, 1), "end_date": dt.date(2027, 1, 5)}
        assert _is_past(row, dt.date(2026, 1, 1)) is False

    def test_falls_back_to_start_date_when_no_end_date(self):
        row = {"start_date": dt.date(2025, 1, 1), "end_date": None}
        assert _is_past(row, dt.date(2026, 1, 1)) is True

    def test_no_dates_never_past(self):
        row = {"start_date": None, "end_date": None}
        assert _is_past(row, dt.date(2026, 1, 1)) is False


class TestTransitionPast:
    def test_transitions_only_past_rows(self):
        today = dt.date(2026, 9, 4)
        rows = [
            {"id": 1, "start_date": dt.date(2025, 1, 1), "end_date": dt.date(2025, 1, 5)},  # past
            {"id": 2, "start_date": dt.date(2027, 1, 1), "end_date": dt.date(2027, 1, 5)},  # future
            {"id": 3, "start_date": dt.date(2025, 6, 1), "end_date": None},  # past, no end_date
        ]
        with patch("eoa.conferences.tracker._execute") as mock_execute:
            count = _transition_past(today, rows=rows)
        assert count == 2
        mock_execute.assert_called_once()
        args, _ = mock_execute.call_args
        assert sorted(args[1][0]) == [1, 3]

    def test_no_rows_no_db_call(self):
        with patch("eoa.conferences.tracker._execute") as mock_execute:
            count = _transition_past(dt.date(2026, 1, 1), rows=[])
        assert count == 0
        mock_execute.assert_not_called()


# --------------------------------------------------------------------------
# verify_conference helpers: date parsing, year extraction, snapshot diffing
# --------------------------------------------------------------------------


class TestParseDate:
    def test_iso_date(self):
        assert _parse_date("2026-10-12") == dt.date(2026, 10, 12)

    def test_iso_datetime_truncated(self):
        assert _parse_date("2026-10-12T00:00:00Z") == dt.date(2026, 10, 12)

    def test_free_text_date(self):
        assert _parse_date("October 12, 2026") == dt.date(2026, 10, 12)

    def test_none_input(self):
        assert _parse_date(None) is None

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_garbage_returns_none(self):
        assert _parse_date("not a date at all !!") is None


class TestYearFromName:
    def test_trailing_year(self):
        assert _year_from_name("AUSA 2026") == 2026

    def test_no_year(self):
        assert _year_from_name("AUSA") is None

    def test_year_not_at_end_ignored(self):
        assert _year_from_name("2026 kickoff event") is None


class TestJsonable:
    def test_date_to_isoformat(self):
        assert _jsonable(dt.date(2026, 10, 12)) == "2026-10-12"

    def test_datetime_to_isoformat(self):
        assert _jsonable(dt.datetime(2026, 10, 12, 9, 30)) == "2026-10-12T09:30:00"

    def test_passthrough_for_plain_values(self):
        assert _jsonable("Paris") == "Paris"
        assert _jsonable(None) is None
        assert _jsonable(5) == 5


class TestApplyConferenceUpdate:
    def test_builds_update_with_fields_and_snapshot(self):
        with patch("eoa.conferences.tracker._execute") as mock_execute:
            _apply_conference_update(
                7, {"city": "Paris", "venue": "Le Bourget"}, {"city": "Nowhere"}, "confirmed"
            )
        mock_execute.assert_called_once()
        query, params = mock_execute.call_args[0]
        assert "city = %(city)s" in query
        assert "venue = %(venue)s" in query
        assert "prev_snapshot = %(prev_snapshot)s" in query
        assert "status = %(status)s" in query
        assert params["city"] == "Paris"
        assert params["status"] == "confirmed"
        assert params["id"] == 7

    def test_no_field_updates_still_writes_snapshot_and_status(self):
        with patch("eoa.conferences.tracker._execute") as mock_execute:
            _apply_conference_update(3, {}, {}, "cancelled")
        query, params = mock_execute.call_args[0]
        assert query.strip().startswith("UPDATE conferences SET prev_snapshot")
        assert params["status"] == "cancelled"


# --------------------------------------------------------------------------
# discover_new: dedupe by name similarity + relevance rubric
# --------------------------------------------------------------------------


class TestIsNearDuplicate:
    def test_exact_match(self):
        assert _is_near_duplicate("DSEI 2027", ["DSEI 2027"]) is True

    def test_case_and_whitespace_insensitive(self):
        assert _is_near_duplicate("  dsei 2027 ", ["DSEI 2027"]) is True

    def test_near_miss_above_threshold(self):
        assert (
            _is_near_duplicate(
                "AUSA Annual Meeting & Exposition 2026", ["AUSA Annual Meeting and Exposition 2026"]
            )
            is True
        )

    def test_distinct_names_not_duplicate(self):
        assert _is_near_duplicate("Xponential 2027", ["DSEI 2027"]) is False

    def test_empty_existing_list(self):
        assert _is_near_duplicate("Any Conference 2027", []) is False


class TestRelevanceScore:
    def test_base_score_with_no_keyword_hits(self):
        cand = ConferenceCandidate(name="Generic Trade Fair", rationale_he="תערוכה כללית")
        assert _relevance_score(cand, []) == 1

    def test_score_capped_at_five(self):
        cand = ConferenceCandidate(
            name="Defense Electro-Optic Infrared C-UAS Air Defense Naval Imaging Surveillance Targeting Expo",
            rationale_he="",
        )
        assert _relevance_score(cand, []) == 5

    def test_extra_keywords_count(self):
        cand = ConferenceCandidate(name="Robotics Expo", rationale_he="ta on robotics theme")
        assert _relevance_score(cand, ["robotics"]) == 2

    def test_score_never_below_one(self):
        cand = ConferenceCandidate(name="", rationale_he="")
        assert _relevance_score(cand, []) == 1


# --------------------------------------------------------------------------
# conference_card: legacy field compatibility + changes-vs-prev_snapshot
# --------------------------------------------------------------------------


class TestConferenceCard:
    def test_legacy_fields_present(self):
        row = {
            "id": 1,
            "name": "AUSA 2026",
            "city": "Washington",
            "venue": None,
            "start_date": dt.date(2026, 10, 12),
            "end_date": dt.date(2026, 10, 14),
            "registration_url": "https://ausa.org",
            "relevance": 4,
            "status": "estimated",
        }
        card = conference_card(row)
        assert card["location"] == "Washington"
        assert card["starts_at"] == "2026-10-12"
        assert card["ends_at"] == "2026-10-14"
        assert card["url"] == "https://ausa.org"
        assert card["relevance_he"] == "גבוהה (4)"

    def test_unknown_relevance_is_none_he(self):
        row = {"id": 1, "name": "X", "relevance": None}
        assert conference_card(row)["relevance_he"] is None

    def test_changes_computed_from_prev_snapshot(self):
        row = {
            "id": 2,
            "name": "DSEI 2027",
            "city": "London",
            "prev_snapshot": {"city": "TBD", "venue": None},
            "venue": "ExCeL",
        }
        card = conference_card(row)
        assert card["changes"]["city"] == {"from": "TBD", "to": "London"}
        assert card["changes"]["venue"] == {"from": None, "to": "ExCeL"}

    def test_no_changes_when_snapshot_matches(self):
        row = {"id": 3, "name": "X", "city": "Paris", "prev_snapshot": {"city": "Paris"}}
        assert conference_card(row)["changes"] == {}

    def test_no_prev_snapshot_no_changes(self):
        row = {"id": 4, "name": "X"}
        assert conference_card(row)["changes"] == {}


# --------------------------------------------------------------------------
# reminders: due dates
# --------------------------------------------------------------------------


class TestDueReminders:
    def test_registration_opens_only_for_relevance_4_plus(self):
        today = dt.date(2026, 6, 1)
        rows = [
            {"id": 1, "name": "A", "relevance": 4, "registration_opens": today},
            {"id": 2, "name": "B", "relevance": 3, "registration_opens": today},
        ]
        due = due_reminders(today, rows=rows)
        kinds_by_id = {(r["id"], k) for r, k in due}
        assert (1, "registration_opens") in kinds_by_id
        assert (2, "registration_opens") not in kinds_by_id

    def test_early_bird_fires_14_days_ahead(self):
        today = dt.date(2026, 6, 1)
        rows = [{"id": 1, "name": "A", "relevance": 2, "early_bird_deadline": today + dt.timedelta(days=14)}]
        due = due_reminders(today, rows=rows)
        assert (rows[0], "early_bird") in due

    def test_early_bird_not_due_off_by_one_day(self):
        today = dt.date(2026, 6, 1)
        rows = [{"id": 1, "name": "A", "relevance": 2, "early_bird_deadline": today + dt.timedelta(days=13)}]
        assert due_reminders(today, rows=rows) == []

    def test_cfp_fires_14_days_ahead(self):
        today = dt.date(2026, 6, 1)
        rows = [{"id": 1, "name": "A", "relevance": 1, "cfp_deadline": today + dt.timedelta(days=14)}]
        due = due_reminders(today, rows=rows)
        assert due == [(rows[0], "cfp")]

    def test_major_conference_only_relevance_5_30_days_ahead(self):
        today = dt.date(2026, 6, 1)
        rows = [
            {"id": 1, "name": "A", "relevance": 5, "start_date": today + dt.timedelta(days=30)},
            {"id": 2, "name": "B", "relevance": 4, "start_date": today + dt.timedelta(days=30)},
        ]
        due = due_reminders(today, rows=rows)
        ids = {r["id"] for r, _ in due}
        assert ids == {1}

    def test_string_dates_accepted(self):
        today = dt.date(2026, 6, 1)
        rows = [{"id": 1, "name": "A", "relevance": 4, "registration_opens": "2026-06-01"}]
        assert due_reminders(today, rows=rows) == [(rows[0], "registration_opens")]

    def test_multiple_kinds_for_one_row(self):
        today = dt.date(2026, 6, 1)
        row = {
            "id": 1,
            "name": "A",
            "relevance": 5,
            "registration_opens": today,
            "start_date": today + dt.timedelta(days=30),
        }
        due = due_reminders(today, rows=[row])
        kinds = {k for _, k in due}
        assert kinds == {"registration_opens", "major_conference"}

    def test_no_matches_empty_list(self):
        today = dt.date(2026, 6, 1)
        rows = [{"id": 1, "name": "A", "relevance": 3}]
        assert due_reminders(today, rows=rows) == []

    def test_cancelled_rows_excluded_when_reading_from_db(self):
        """(DB-path smoke test) the base query filters out cancelled/past rows."""
        with patch("eoa.conferences.reminders._fetchall", return_value=[]) as mock_fetchall:
            due_reminders(dt.date(2026, 1, 1))
        query = mock_fetchall.call_args[0][0]
        assert "cancelled" in query and "past" in query


class TestSendReminders:
    def test_sends_and_records_new_reminder(self):
        today = dt.date(2026, 6, 1)
        row = {"id": 1, "name": "AUSA 2026", "relevance": 4, "registration_opens": today, "city": "DC"}
        with (
            patch("eoa.conferences.reminders.due_reminders", return_value=[(row, "registration_opens")]),
            patch("eoa.conferences.reminders._fetchone", return_value=None),
            patch("eoa.conferences.reminders._execute") as mock_execute,
            patch("eoa.conferences.reminders._notify_for") as mock_notify,
        ):
            result = send_reminders(today)
        mock_notify.assert_called_once_with(row, "registration_opens")
        mock_execute.assert_called_once()
        assert result == {"due": 1, "sent": 1, "skipped_already_sent": 0}

    def test_skips_already_sent_reminder(self):
        today = dt.date(2026, 6, 1)
        row = {"id": 1, "name": "AUSA 2026", "relevance": 4, "registration_opens": today}
        with (
            patch("eoa.conferences.reminders.due_reminders", return_value=[(row, "registration_opens")]),
            patch("eoa.conferences.reminders._fetchone", return_value={"?column?": 1}),
            patch("eoa.conferences.reminders._execute") as mock_execute,
            patch("eoa.conferences.reminders._notify_for") as mock_notify,
        ):
            result = send_reminders(today)
        mock_notify.assert_not_called()
        mock_execute.assert_not_called()
        assert result == {"due": 1, "sent": 0, "skipped_already_sent": 1}


# --------------------------------------------------------------------------
# ical: N VEVENTs, round-trips via icalendar
# --------------------------------------------------------------------------


class TestBuildIcal:
    def test_single_conference_one_vevent(self):
        confs = [{"id": 1, "name": "AUSA 2026", "start_date": "2026-10-12", "end_date": "2026-10-14"}]
        text = build_ical(confs)
        cal = Calendar.from_ical(text)
        events = list(cal.walk("VEVENT"))
        assert len(events) == 1
        assert "כנס: AUSA 2026" in str(events[0].get("summary"))

    def test_reminder_dates_add_extra_vevents(self):
        confs = [
            {
                "id": 1,
                "name": "DSEI 2027",
                "start_date": "2027-09-14",
                "end_date": "2027-09-17",
                "registration_opens": "2027-03-01",
                "early_bird_deadline": "2027-06-01",
                "cfp_deadline": "2027-05-01",
            }
        ]
        cal = Calendar.from_ical(build_ical(confs))
        events = list(cal.walk("VEVENT"))
        assert len(events) == 4  # main span + 3 reminder dates

    def test_conference_without_start_date_skipped(self):
        confs = [{"id": 1, "name": "TBD Conference"}]
        cal = Calendar.from_ical(build_ical(confs))
        assert list(cal.walk("VEVENT")) == []

    def test_multiple_conferences_sum_vevents(self):
        confs = [
            {"id": 1, "name": "A 2026", "start_date": "2026-01-01"},
            {"id": 2, "name": "B 2026", "start_date": "2026-02-01", "registration_opens": "2026-01-15"},
        ]
        cal = Calendar.from_ical(build_ical(confs))
        assert len(list(cal.walk("VEVENT"))) == 3

    def test_output_is_valid_utf8_text_calendar(self):
        confs = [{"id": 1, "name": "כנס עברי", "start_date": "2026-01-01", "city": "תל אביב"}]
        text = build_ical(confs)
        assert isinstance(text, str)
        assert "BEGIN:VCALENDAR" in text
        # round-trips without raising and preserves the Hebrew summary/location
        cal = Calendar.from_ical(text)
        ev = next(iter(cal.walk("VEVENT")))
        assert "כנס עברי" in str(ev.get("summary"))
        assert "תל אביב" in str(ev.get("location"))

    def test_legacy_starts_at_ends_at_fields_accepted(self):
        confs = [{"id": 1, "name": "Legacy", "starts_at": "2026-05-01", "ends_at": "2026-05-03"}]
        cal = Calendar.from_ical(build_ical(confs))
        assert len(list(cal.walk("VEVENT"))) == 1
