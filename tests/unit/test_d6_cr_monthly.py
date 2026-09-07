"""D6 checks added for CR-monthly.md (2026-09-08 user feedback on monthly_2026-09-30.md):
"unsupported intensifier" (must be 0) and "trend section cites non-member item" (must be 0).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_d6_cr_monthly.py -q``
"""

from __future__ import annotations

from pathlib import Path

from eoa.qa import d6_daily_report as d6

_GOOD_WEEKLY_MD = """# דוח שבועי

## תקציר מנהלים

חברת Rafael חתמה על חוזה בסך 10 מיליון דולר עם משרד הביטחון [1].

## נספח מקורות

| # | כותרת | קישור |
|---|---|---|
| <a id="src-1"></a>1 | כתבה לדוגמה | [https://example.com/a](https://example.com/a) |
"""

_BAD_WEEKLY_MD = """# דוח שבועי

## תקציר מנהלים

החודש ניכרת התעצמות דרמטית ברכש מערכות הגנה אווירית [1].

## נספח מקורות

| # | כותרת | קישור |
|---|---|---|
| <a id="src-1"></a>1 | כתבה לדוגמה | [https://example.com/a](https://example.com/a) |
"""


def _check(score: d6.DomainScore, name: str) -> d6.Check:
    return next(c for c in score.checks if c.name == name)


def test_unsupported_intensifier_check_passes_on_clean_weekly(tmp_path: Path) -> None:
    path = tmp_path / "weekly_2026-09-06.md"
    path.write_text(_GOOD_WEEKLY_MD, encoding="utf-8")
    result = d6.score_D6(path, run_link_check=False)
    assert _check(result, "unsupported_intensifier_count_zero").passed is True


def test_unsupported_intensifier_check_fails_on_unsupported_claim(tmp_path: Path) -> None:
    path = tmp_path / "weekly_2026-09-07.md"
    path.write_text(_BAD_WEEKLY_MD, encoding="utf-8")
    result = d6.score_D6(path, run_link_check=False)
    check = _check(result, "unsupported_intensifier_count_zero")
    assert check.passed is False
    assert "התעצמות" in check.evidence or "ניכרת" in check.evidence


def test_unsupported_intensifier_check_not_added_for_daily_report(tmp_path: Path) -> None:
    """CR-monthly.md items 3/5 are scoped to monthly/weekly only -- a daily report never gets this
    check appended at all (it was never in scope for the feedback this fixes)."""
    path = tmp_path / "daily_2026-09-07.md"
    path.write_text(_BAD_WEEKLY_MD, encoding="utf-8")
    result = d6.score_D6(path, run_link_check=False)
    assert all(c.name != "unsupported_intensifier_count_zero" for c in result.checks)


def test_unsupported_intensifier_check_runs_on_monthly_file_too(tmp_path: Path) -> None:
    daily = tmp_path / "daily_2026-09-07.md"
    daily.write_text(_GOOD_WEEKLY_MD, encoding="utf-8")
    monthly = tmp_path / "monthly_2026-09-30.md"
    monthly.write_text(_BAD_WEEKLY_MD, encoding="utf-8")
    result = d6.score_D6(daily, run_link_check=False, monthly_path=monthly)
    check = _check(result, "monthly_unsupported_intensifier_count_zero")
    assert check.passed is False


def test_trend_membership_check_not_checked_when_no_qa_report_given(tmp_path: Path) -> None:
    path = tmp_path / "weekly_2026-09-06.md"
    path.write_text(_GOOD_WEEKLY_MD, encoding="utf-8")
    result = d6.score_D6(path, run_link_check=False)
    check = _check(result, "weekly_trend_sections_cite_only_member_items")
    assert check.passed is True
    assert "not checked" in check.evidence


def test_trend_membership_check_passes_when_zero_dropped(tmp_path: Path) -> None:
    path = tmp_path / "weekly_2026-09-06.md"
    path.write_text(_GOOD_WEEKLY_MD, encoding="utf-8")
    result = d6.score_D6(path, run_link_check=False, weekly_qa_report={"trend_sentences_out_of_scope": 0})
    check = _check(result, "weekly_trend_sections_cite_only_member_items")
    assert check.passed is True


def test_trend_membership_check_fails_when_sentences_had_to_be_stripped(tmp_path: Path) -> None:
    path = tmp_path / "weekly_2026-09-06.md"
    path.write_text(_GOOD_WEEKLY_MD, encoding="utf-8")
    result = d6.score_D6(path, run_link_check=False, weekly_qa_report={"trend_sentences_out_of_scope": 2})
    check = _check(result, "weekly_trend_sections_cite_only_member_items")
    assert check.passed is False
    assert "2 trend sentence" in check.evidence


def test_trend_membership_check_for_monthly(tmp_path: Path) -> None:
    daily = tmp_path / "daily_2026-09-07.md"
    daily.write_text(_GOOD_WEEKLY_MD, encoding="utf-8")
    monthly = tmp_path / "monthly_2026-09-30.md"
    monthly.write_text(_GOOD_WEEKLY_MD, encoding="utf-8")
    result = d6.score_D6(
        daily,
        run_link_check=False,
        monthly_path=monthly,
        monthly_qa_report={"trend_sentences_out_of_scope": 1},
    )
    check = _check(result, "monthly_trend_sections_cite_only_member_items")
    assert check.passed is False
