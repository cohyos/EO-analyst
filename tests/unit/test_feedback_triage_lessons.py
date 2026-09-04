"""Unit tests for `eoa.pipeline.triage._lessons_text()`'s calibration-lesson feed (FR-3.3).

`feedback.calibration.calibrate()` writes `lessons(kind='calibration')`
rows; `_lessons_text()` is the reader side that feeds them back into the
triage prompt. This verifies the existing contract it must uphold: most
recent first, capped at 12 lines -- `eoa.memory.relational.get_lessons()`
already orders by `created_at DESC`, and `_lessons_text()` slices `[:12]`.

Mirrors `tests/unit/test_triage_levels.py`'s `unittest.mock.patch` style
(this module already imports cleanly without a DB in that test file, so no
`eoa.db` stub is needed here either).
"""

from __future__ import annotations

from unittest.mock import patch

from eoa.pipeline.triage import _lessons_text


def _lesson(i: int) -> dict:
    return {"id": i, "kind": "calibration", "text": f"לקח כיול מספר {i}", "active": True}


class TestLessonsTextCalibrationFeed:
    def test_includes_calibration_lessons(self) -> None:
        lessons = [_lesson(i) for i in range(3)]
        with (
            patch("eoa.pipeline.triage.get_lessons", return_value=lessons),
            patch("eoa.pipeline.triage.recent_feedback", return_value=[]),
        ):
            text = _lessons_text()
        for ls in lessons:
            assert ls["text"] in text

    def test_capped_at_12_lines(self) -> None:
        lessons = [_lesson(i) for i in range(20)]
        with (
            patch("eoa.pipeline.triage.get_lessons", return_value=lessons),
            patch("eoa.pipeline.triage.recent_feedback", return_value=[]),
        ):
            text = _lessons_text()
        assert all(ls["text"] in text for ls in lessons[:12])
        assert not any(ls["text"] in text for ls in lessons[12:])

    def test_most_recent_first_order_preserved(self) -> None:
        # get_lessons() itself owns the DESC ordering; this only confirms
        # _lessons_text() doesn't re-sort or reverse what it's handed.
        lessons = [_lesson(i) for i in range(3)]  # already "most recent first" order
        with (
            patch("eoa.pipeline.triage.get_lessons", return_value=lessons),
            patch("eoa.pipeline.triage.recent_feedback", return_value=[]),
        ):
            text = _lessons_text()
        positions = [text.index(ls["text"]) for ls in lessons]
        assert positions == sorted(positions)

    def test_no_lessons_returns_placeholder(self) -> None:
        with (
            patch("eoa.pipeline.triage.get_lessons", return_value=[]),
            patch("eoa.pipeline.triage.recent_feedback", return_value=[]),
        ):
            text = _lessons_text()
        assert text == "אין לקחים קודמים."

    def test_get_lessons_called_with_calibration_kind(self) -> None:
        with (
            patch("eoa.pipeline.triage.get_lessons", return_value=[]) as mock_get,
            patch("eoa.pipeline.triage.recent_feedback", return_value=[]),
        ):
            _lessons_text()
        mock_get.assert_called_once_with("calibration")
