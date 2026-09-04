"""Unit tests for `eoa.feedback.calibration` (FR-3.3 / FR-11.3).

No DB: every function that would otherwise touch `eoa.db.connection()` or
persist a lesson via `eoa.memory.relational.add_lesson` is monkeypatched at
the `eoa.feedback.calibration` module level, mirroring
`tests/unit/test_obsidian_export.py`'s stubbing style.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_feedback_calibration.py -q``
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

from eoa.feedback import calibration


def _row(user_level, agent_level, domain=None, source_kind=None):
    return {
        "user_level": user_level,
        "agent_level": agent_level,
        "domain": domain,
        "source_kind": source_kind,
    }


def _fail(*_a, **_k):
    raise AssertionError("this DB-write function should not have been called")


# --------------------------------------------------------------------------
# pure grouping logic
# --------------------------------------------------------------------------


class TestGroupDeltas:
    def test_basic_grouping(self) -> None:
        rows = [
            _row("yellow", "orange", domain="c_uas", source_kind="rss"),
            _row("archive", "red", domain="c_uas", source_kind="rss"),
        ]
        by_domain, by_source_kind, counted = calibration._group_deltas(rows)
        assert counted == 2
        assert by_domain["c_uas"] == [-1.0, -3.0]
        assert by_source_kind["rss"] == [-1.0, -3.0]

    def test_skips_rows_with_unmapped_levels(self) -> None:
        rows = [_row("yellow", None), _row(None, "red"), _row("bogus", "red")]
        by_domain, by_source_kind, counted = calibration._group_deltas(rows)
        assert counted == 0
        assert by_domain == {}
        assert by_source_kind == {}

    def test_rows_without_domain_or_source_kind_still_counted(self) -> None:
        by_domain, by_source_kind, counted = calibration._group_deltas([_row("yellow", "red")])
        assert counted == 1
        assert by_domain == {}
        assert by_source_kind == {}


# --------------------------------------------------------------------------
# bias detection thresholds
# --------------------------------------------------------------------------


class TestDetectBiases:
    def test_below_min_samples_not_flagged(self) -> None:
        assert calibration._detect_biases({"c_uas": [-1.0, -1.0]}, {}) == []

    def test_below_magnitude_threshold_not_flagged(self) -> None:
        assert calibration._detect_biases({"c_uas": [0.0, 0.0, 0.0]}, {}) == []

    def test_negative_bias_flagged(self) -> None:
        biases = calibration._detect_biases({"c_uas": [-1.0, -1.0, -1.0]}, {})
        assert len(biases) == 1
        b = biases[0]
        assert (b.scope, b.key, b.n, b.mean_delta) == ("domain", "c_uas", 3, -1.0)
        assert b.source_ref == "calib:domain:c_uas"

    def test_positive_bias_flagged_for_source_kind(self) -> None:
        biases = calibration._detect_biases({}, {"rss": [1.0, 1.0, 0.5]})
        assert len(biases) == 1
        assert biases[0].scope == "source_kind"
        assert biases[0].key == "rss"

    def test_exactly_at_threshold_is_flagged(self) -> None:
        # |mean| >= 0.5 is inclusive.
        biases = calibration._detect_biases({"x": [0.5, 0.5, 0.5]}, {})
        assert len(biases) == 1


# --------------------------------------------------------------------------
# Hebrew lesson text
# --------------------------------------------------------------------------


class TestBiasText:
    def test_negative_domain_bias_matches_spec_example(self) -> None:
        bias = calibration.DomainBias(scope="domain", key="c_uas", n=5, mean_delta=-1.0)
        assert (
            calibration._bias_text(bias) == "בתחום c_uas המשתמש מוריד דירוג בממוצע ב-1 רמה — היה שמרני יותר"
        )

    def test_positive_domain_bias(self) -> None:
        bias = calibration.DomainBias(scope="domain", key="airborne_pods", n=4, mean_delta=1.3)
        text = calibration._bias_text(bias)
        assert "airborne_pods" in text
        assert "מעלה דירוג" in text
        assert "1.3" in text

    def test_source_kind_bias_wording(self) -> None:
        bias = calibration.DomainBias(scope="source_kind", key="rss", n=4, mean_delta=-0.7)
        text = calibration._bias_text(bias)
        assert "במקורות מסוג rss" in text
        assert "0.7" in text


# --------------------------------------------------------------------------
# calibrate(): create / update / dedupe / deactivate
# --------------------------------------------------------------------------


class TestCalibrateIntegration:
    def test_creates_new_calibration_lesson(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [_row("yellow", "orange", domain="c_uas") for _ in range(3)] + [
            _row("archive", "orange", domain="c_uas")
        ]
        monkeypatch.setattr(calibration, "_feedback_rows", lambda days: rows)
        monkeypatch.setattr(calibration, "_active_calibration_lessons", lambda: {})

        created: list[tuple] = []
        monkeypatch.setattr(
            calibration,
            "add_lesson",
            lambda kind, text, source_ref=None: created.append((kind, text, source_ref)) or 1,
        )
        monkeypatch.setattr(calibration, "_update_lesson_text", _fail)
        monkeypatch.setattr(calibration, "_deactivate_lessons", lambda ids: 0)

        summary = calibration.calibrate(days=30)

        assert summary.feedback_n == 4
        assert len(summary.biases) == 1
        assert summary.lessons_created == 1
        assert summary.lessons_updated == 0
        assert summary.lessons_deactivated == 0
        assert len(created) == 1
        assert created[0][0] == "calibration"
        assert created[0][2] == "calib:domain:c_uas"

    def test_updates_existing_lesson_when_text_changed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [_row("archive", "red", domain="c_uas") for _ in range(3)]  # delta -3 each
        monkeypatch.setattr(calibration, "_feedback_rows", lambda days: rows)
        monkeypatch.setattr(
            calibration,
            "_active_calibration_lessons",
            lambda: {
                "calib:domain:c_uas": {"id": 99, "text": "old stale text", "source_ref": "calib:domain:c_uas"}
            },
        )
        monkeypatch.setattr(calibration, "add_lesson", _fail)
        updated: list[tuple] = []
        monkeypatch.setattr(calibration, "_update_lesson_text", lambda lid, text: updated.append((lid, text)))
        monkeypatch.setattr(calibration, "_deactivate_lessons", lambda ids: 0)

        summary = calibration.calibrate(days=30)

        assert summary.lessons_updated == 1
        assert summary.lessons_created == 0
        assert updated[0][0] == 99

    def test_no_change_when_text_identical_dedupe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [_row("archive", "red", domain="c_uas") for _ in range(3)]
        monkeypatch.setattr(calibration, "_feedback_rows", lambda days: rows)
        text = calibration._bias_text(
            calibration.DomainBias(scope="domain", key="c_uas", n=3, mean_delta=-3.0)
        )
        monkeypatch.setattr(
            calibration,
            "_active_calibration_lessons",
            lambda: {"calib:domain:c_uas": {"id": 99, "text": text, "source_ref": "calib:domain:c_uas"}},
        )
        # Neither create nor update should be attempted -- the text is unchanged.
        monkeypatch.setattr(calibration, "add_lesson", _fail)
        monkeypatch.setattr(calibration, "_update_lesson_text", _fail)
        monkeypatch.setattr(calibration, "_deactivate_lessons", lambda ids: 0)

        summary = calibration.calibrate(days=30)
        assert summary.lessons_created == 0
        assert summary.lessons_updated == 0

    def test_deactivates_stale_lesson_no_longer_biased(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(calibration, "_feedback_rows", lambda days: [])
        monkeypatch.setattr(
            calibration,
            "_active_calibration_lessons",
            lambda: {"calib:domain:c_uas": {"id": 5, "text": "stale", "source_ref": "calib:domain:c_uas"}},
        )
        monkeypatch.setattr(calibration, "add_lesson", _fail)
        monkeypatch.setattr(calibration, "_update_lesson_text", _fail)
        deactivated_ids: list[int] = []
        monkeypatch.setattr(
            calibration, "_deactivate_lessons", lambda ids: deactivated_ids.extend(ids) or len(ids)
        )

        summary = calibration.calibrate(days=30)
        assert summary.lessons_deactivated == 1
        assert deactivated_ids == [5]

    def test_no_feedback_rows_no_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(calibration, "_feedback_rows", lambda days: [])
        monkeypatch.setattr(calibration, "_active_calibration_lessons", lambda: {})
        monkeypatch.setattr(calibration, "_deactivate_lessons", lambda ids: 0)

        summary = calibration.calibrate(days=30)
        assert summary.feedback_n == 0
        assert summary.biases == []
        assert summary.lessons_created == 0
