"""F34 (SOL-AUDIT-2026-09-24 / SOL-REVIEW-2026-09-24 review): rebuilding an explicit historical
period must put THAT period's date in the draft prompt's `{date_he}` header, never the date the
rebuild happens to run on.

`tests/unit/test_report_daily.py` covers `eoa.report.daily.draft_report`/`_corrective_retry`. This
covers the weekly and monthly siblings (`weekly.py:826`, `monthly.py:579` in the review's own
evidence), which take the identical `period_end: dt.date | None = None` parameter and the same
`hebrew_date_str(period_end or _today_jerusalem())` wiring.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_f34_historical_period_prompt.py -q``
"""

from __future__ import annotations

import datetime as dt

from eoa.llm.schemas.reports import MonthlyReportDraft, WeeklyReportDraft
from eoa.report import monthly, weekly

_TODAY = dt.date(2026, 9, 24)
_HISTORICAL = dt.date(2026, 3, 15)

_ITEMS = [
    {
        "id": 1,
        "n": 1,
        "title": "IAI wins naval radar deal",
        "domain": "naval_surveillance",
        "source_name": "Naval News",
        "url": "https://example.com/1",
        "published_at": dt.date(2026, 3, 10),
        "level": "red",
        "summary_he": "תקציר.",
        "so_what_he": "משמעות.",
    }
]


def test_draft_weekly_uses_the_historical_period_end_not_today(monkeypatch) -> None:
    captured = []

    def fake_chat_structured(role, schema, messages, **kw):
        captured.append(messages)
        return WeeklyReportDraft(
            exec_summary=[], trends=[], sections=[], outlook=[], open_points_he=[],
        )

    monkeypatch.setattr(weekly, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(weekly, "_today_jerusalem", lambda: _TODAY)

    weekly.draft_weekly(_ITEMS, [], "", period_end=_HISTORICAL)

    prompt = captured[0][1]["content"]
    assert weekly.hebrew_date_str(_HISTORICAL) in prompt
    assert weekly.hebrew_date_str(_TODAY) not in prompt


def test_draft_monthly_uses_the_historical_period_end_not_today(monkeypatch) -> None:
    captured = []

    def fake_chat_structured(role, schema, messages, **kw):
        captured.append(messages)
        return MonthlyReportDraft(
            exec_summary=[], trends=[], sections=[], outlook=[], open_points_he=[],
        )

    monkeypatch.setattr(monthly, "chat_structured", fake_chat_structured)
    monkeypatch.setattr(monthly, "_today_jerusalem", lambda: _TODAY)

    monthly.draft_monthly(_ITEMS, [], "", period_end=_HISTORICAL)

    prompt = captured[0][1]["content"]
    assert monthly.hebrew_date_str(_HISTORICAL) in prompt
    assert monthly.hebrew_date_str(_TODAY) not in prompt
