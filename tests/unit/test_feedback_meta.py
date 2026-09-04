"""Unit tests for `eoa.feedback.meta` (FR-11.4 weekly "what changed" transparency summary).

No DB, no ntfy network: `_lessons_in_period` and `eoa.notify.ntfy.status`
are monkeypatched at the `eoa.feedback.meta` module level.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_feedback_meta.py -q``
"""

from __future__ import annotations

import sys
import types

import pytest

if "eoa.db" not in sys.modules:
    try:
        import eoa.db  # noqa: F401
    except ImportError:
        fake_db = types.ModuleType("eoa.db")
        fake_db.connection = lambda: None  # type: ignore[attr-defined]
        fake_db.get_pool = lambda: None  # type: ignore[attr-defined]
        sys.modules["eoa.db"] = fake_db

from eoa.feedback import meta


class TestWeeklyMetaSummary:
    def test_no_lessons_returns_no_changes_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(meta, "_lessons_in_period", lambda days: [])

        summary = meta.weekly_meta_summary(7)

        assert summary.lesson_count == 0
        assert summary.period_days == 7
        assert "לא נוצרו לקחים חדשים" in summary.text_he

    def test_groups_by_kind_with_hebrew_labels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        lessons = [
            {
                "kind": "calibration",
                "text": "בתחום c_uas המשתמש מוריד דירוג בממוצע ב-1 רמה — היה שמרני יותר",
                "source_ref": "calib:domain:c_uas",
            },
            {
                "kind": "watchlist",
                "text": "המשתמש ביקש להוסיף מעקב אחרי: Epirus",
                "source_ref": "survey:1:q9",
            },
        ]
        monkeypatch.setattr(meta, "_lessons_in_period", lambda days: lessons)

        summary = meta.weekly_meta_summary(7)

        assert summary.lesson_count == 2
        assert summary.by_kind == {"calibration": 1, "watchlist": 1}
        assert "כיול דירוג חשיבות" in summary.text_he
        assert "עדכון רשימת מעקב" in summary.text_he
        assert "בתחום c_uas המשתמש מוריד דירוג" in summary.text_he
        assert "Epirus" in summary.text_he

    def test_caps_lines_per_kind_and_notes_overflow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        lessons = [{"kind": "style", "text": f"lesson {i}", "source_ref": None} for i in range(7)]
        monkeypatch.setattr(meta, "_lessons_in_period", lambda days: lessons)

        summary = meta.weekly_meta_summary(7)

        assert summary.text_he.count("- lesson ") == meta._MAX_LINES_PER_KIND
        assert "ועוד 2 לקחים נוספים" in summary.text_he

    def test_period_days_propagated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(meta, "_lessons_in_period", lambda days: [])

        summary = meta.weekly_meta_summary(14)

        assert summary.period_days == 14


class TestPostWeeklyMeta:
    def test_posts_via_ntfy_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(meta, "_lessons_in_period", lambda days: [])
        sent: list[str] = []
        monkeypatch.setattr(meta.ntfy, "status", lambda text, priority="low": sent.append(text))

        summary = meta.post_weekly_meta(7)

        assert sent == [summary.text_he]
