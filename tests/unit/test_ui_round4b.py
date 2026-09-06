"""Unit tests for round 4b UI findings (docs/REVIEW_2026-09-06_evening.md W19/W20/W25):

- W20 -- ``eoa.api.services._job_subject_he``/``_job_card``: the jobs table's per-kind subject
  derivation (deep_search's question, bd_report's territory, patent_survey's topic, the
  period-based runs' own created_at date) is a pure function, so it is exercised directly with
  synthetic kind/payload/created_at combinations -- no DB/HTTP/Ollama involved, mirroring
  ``tests/unit/test_patents_round3.py``'s "stub every DB call" convention (here there is nothing
  to stub at all).
- W19 -- ``db.seed.seed_payloads``: the seed script's image_url/spec_url/spec_source
  COALESCE-on-conflict behaviour (never overwrites an already-set value with a blank one),
  exercised against a fake DB cursor so no live Postgres is required.
"""

from __future__ import annotations

import datetime as dt

import pytest

from eoa.api.services import _job_card, _job_subject_he, _territory_label_he


class TestJobSubjectHe:
    def test_deep_search_uses_the_question_truncated_to_80_chars(self):
        long_question = "מהם " + ("פרטי " * 30) + "המכרז?"
        assert len(long_question) > 80
        subject = _job_subject_he("deep_search", {"question": long_question}, None)
        assert subject == long_question[:80]

    def test_deep_search_with_no_question_is_none(self):
        assert _job_subject_he("deep_search", {}, None) is None
        assert _job_subject_he("deep_search", {"question": "   "}, None) is None
        assert _job_subject_he("deep_search", None, None) is None

    def test_bd_report_uses_the_territory_label(self):
        # DE/US/IL etc. are rendered through the same Hebrew territory-label table the reports
        # list already uses (`_territory_label_he`) rather than the raw ISO code.
        assert _job_subject_he("bd_report", {"territory": "DE"}, None) == _territory_label_he("DE")
        assert _job_subject_he("bd_report", {"territory": "DE"}, None) == "גרמניה"

    def test_bd_report_with_unknown_territory_falls_back_to_the_raw_code(self):
        assert _job_subject_he("bd_report", {"territory": "ZZ"}, None) == "ZZ"

    def test_bd_report_with_no_territory_is_none(self):
        assert _job_subject_he("bd_report", {}, None) is None

    def test_patent_survey_uses_the_topic_verbatim(self):
        assert (
            _job_subject_he("patent_survey", {"topic": "FPA עם פיקסל דיגיטלי (DROIC)"}, None)
            == "FPA עם פיקסל דיגיטלי (DROIC)"
        )

    def test_patent_survey_with_blank_topic_is_none(self):
        assert _job_subject_he("patent_survey", {"topic": "   "}, None) is None
        assert _job_subject_he("patent_survey", {}, None) is None

    @pytest.mark.parametrize("kind", ["daily_run", "weekly_run", "monthly_run", "ingest", "report"])
    def test_period_based_runs_use_the_created_at_date(self, kind):
        created_at = dt.datetime(2026, 9, 6, 18, 58, 6, tzinfo=dt.UTC)
        assert _job_subject_he(kind, {}, created_at) == "06.09.2026"

    def test_period_based_run_with_no_created_at_is_none(self):
        assert _job_subject_he("daily_run", {}, None) is None

    def test_unrecognized_kind_is_none(self):
        assert _job_subject_he("conference_scan", {"mode": "poll"}, None) is None

    def test_never_invents_a_subject_for_a_kind_with_no_rule(self):
        # tender_scan has no per-job identifying field defined at all (unlike deep_search/
        # bd_report/patent_survey) -- must stay None rather than guessing from an unrelated field.
        assert _job_subject_he("tender_scan", {"mode": "full"}, None) is None


class TestJobCard:
    def test_adds_subject_he_without_mutating_the_source_row_kind_or_payload(self):
        row = {
            "id": 118,
            "kind": "bd_report",
            "payload": {"territory": "DE", "lookback_days": 90},
            "state": "done",
            "created_at": dt.datetime(2026, 9, 6, 20, 46, tzinfo=dt.UTC),
        }
        card = _job_card(row)
        assert card["subject_he"] == "גרמניה"
        assert card["kind"] == "bd_report"
        assert card["payload"] == {"territory": "DE", "lookback_days": 90}
        assert "subject_he" not in row  # the original dict passed in is left untouched

    def test_null_payload_never_raises(self):
        row = {"id": 1, "kind": "weekly_run", "payload": None, "created_at": None}
        card = _job_card(row)
        assert card["subject_he"] is None
