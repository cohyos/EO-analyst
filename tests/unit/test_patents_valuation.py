"""Tests for eoa.patents.valuation (A14) -- pure deterministic scoring, no DB/network."""

from __future__ import annotations

import datetime as dt

from eoa.patents.valuation import DISCLAIMER_HE, score_patent


def _row(**overrides):
    base = dict(
        jurisdictions=None,
        forward_citations=None,
        publication_date=None,
        filing_date=None,
        priority_date=None,
        raw=None,
    )
    base.update(overrides)
    return base


class TestScorePatent:
    def test_bare_record_scores_low_but_deterministic(self):
        score, reasons = score_patent(_row(), today=dt.date(2026, 9, 6))
        score2, reasons2 = score_patent(_row(), today=dt.date(2026, 9, 6))
        assert score == score2
        assert reasons == reasons2
        assert 0 <= score <= 100

    def test_score_always_in_range(self):
        score, _ = score_patent(
            _row(
                jurisdictions=["US", "IL", "EU", "CN", "JP", "KR"],
                forward_citations=50,
                publication_date=dt.date(2026, 1, 1),
                filing_date=dt.date(2026, 1, 1),
            ),
            assignee_patent_count=20,
            today=dt.date(2026, 9, 6),
        )
        assert 0 <= score <= 100

    def test_disclaimer_always_present(self):
        _, reasons = score_patent(_row())
        assert reasons[-1] == DISCLAIMER_HE

    def test_more_jurisdictions_scores_higher(self):
        low, _ = score_patent(_row(jurisdictions=["US"]), today=dt.date(2026, 9, 6))
        high, _ = score_patent(_row(jurisdictions=["US", "IL", "EU", "CN", "JP"]), today=dt.date(2026, 9, 6))
        assert high > low

    def test_more_forward_citations_scores_higher(self):
        low, _ = score_patent(
            _row(forward_citations=1, publication_date=dt.date(2025, 1, 1)), today=dt.date(2026, 1, 1)
        )
        high, _ = score_patent(
            _row(forward_citations=20, publication_date=dt.date(2025, 1, 1)), today=dt.date(2026, 1, 1)
        )
        assert high > low

    def test_older_patent_scores_lower_on_remaining_life(self):
        young, _ = score_patent(_row(filing_date=dt.date(2025, 1, 1)), today=dt.date(2026, 1, 1))
        old, _ = score_patent(_row(filing_date=dt.date(2010, 1, 1)), today=dt.date(2026, 1, 1))
        assert young > old

    def test_higher_assignee_velocity_scores_higher(self):
        low, _ = score_patent(_row(), assignee_patent_count=0, today=dt.date(2026, 9, 6))
        high, _ = score_patent(_row(), assignee_patent_count=15, today=dt.date(2026, 9, 6))
        assert high > low

    def test_litigation_flag_adds_score_and_reason(self):
        without, reasons_without = score_patent(_row(raw={}), today=dt.date(2026, 9, 6))
        with_flag, reasons_with = score_patent(_row(raw={"litigation": True}), today=dt.date(2026, 9, 6))
        assert with_flag > without
        assert any("משפטי" in r for r in reasons_with)
        assert not any("משפטי" in r for r in reasons_without)

    def test_no_jurisdiction_data_reason_present(self):
        _, reasons = score_patent(_row(jurisdictions=None), today=dt.date(2026, 9, 6))
        assert any("מדינות הגשה" in r for r in reasons)

    def test_no_citation_data_reason_present(self):
        _, reasons = score_patent(_row(forward_citations=None), today=dt.date(2026, 9, 6))
        assert any("ציטוטים" in r for r in reasons)

    def test_no_filing_date_uses_average_remaining_life(self):
        _, reasons = score_patent(_row(filing_date=None, priority_date=None), today=dt.date(2026, 9, 6))
        assert any("אורך חיים נותר ממוצע" in r for r in reasons)
