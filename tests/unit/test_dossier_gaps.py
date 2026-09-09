"""Tests for ``eoa.dossier.gaps`` (PD-datasheet, 2026-09-09, LESSONS-1 item 4): reading open gaps
from a previous dossier's own persisted ``data``, building follow-up topics, and computing
closed/open status. Pure functions throughout -- no DB/network.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_dossier_gaps.py -q``
"""

from __future__ import annotations

from eoa.dossier import gaps

# --------------------------------------------------------------------------
# extract_gaps_from_previous
# --------------------------------------------------------------------------


def test_extract_gaps_none_previous_returns_empty() -> None:
    assert gaps.extract_gaps_from_previous(None) == []


def test_extract_gaps_missing_data_key_returns_empty() -> None:
    assert gaps.extract_gaps_from_previous({"id": 1}) == []


def test_extract_gaps_from_risks_and_gaps_he() -> None:
    previous = {"data": {"risks_and_gaps_he": ["לא נמצא מחיר רשמי", "אין מידע על TRL"]}}
    result = gaps.extract_gaps_from_previous(previous)
    assert "לא נמצא מחיר רשמי" in result
    assert "אין מידע על TRL" in result


def test_extract_gaps_from_null_specification_values() -> None:
    previous = {
        "data": {
            "specifications": [
                {"parameter_he": "משקל", "value": None},
                {"parameter_he": "טווח זיהוי", "value": ""},
                {"parameter_he": "משקל גוף", "value": "51 ק\"ג"},
            ]
        }
    }
    result = gaps.extract_gaps_from_previous(previous)
    assert any("משקל" in g and "חסר" in g for g in result)
    assert any("טווח זיהוי" in g for g in result)
    assert not any("משקל גוף" in g for g in result)


def test_extract_gaps_from_risks_and_gaps_he_sentence_shaped_dicts() -> None:
    """risks_and_gaps_he is persisted as list[Sentence] ({"text_he", "cites"} dicts), never plain
    strings -- caught live against a real previous dossier (docs/qa/content_review/LESSONS-1.md,
    2026-09-09): stringifying the whole dict produced an unreadable Python-repr gap text instead of
    the actual Hebrew sentence."""
    previous = {
        "data": {
            "risks_and_gaps_he": [
                {"text_he": "משקל ומידות מלאות לא צוינו בממצאי המפרט.", "cites": [3, 7, 8]},
                {"text_he": "לא נמצאו מוצרים מתחרים בחקירה.", "cites": [9]},
            ]
        }
    }
    result = gaps.extract_gaps_from_previous(previous)
    assert "משקל ומידות מלאות לא צוינו בממצאי המפרט." in result
    assert "לא נמצאו מוצרים מתחרים בחקירה." in result
    assert not any("cites" in g or "text_he" in g for g in result)


def test_extract_gaps_from_meta_gaps_list() -> None:
    previous = {"data": {"meta": {"gaps": [{"gap": "אין נתוני מחיר", "status": "open"}, "פער חופשי"]}}}
    result = gaps.extract_gaps_from_previous(previous)
    assert "אין נתוני מחיר" in result
    assert "פער חופשי" in result


def test_extract_gaps_dedupes_across_sources() -> None:
    previous = {
        "data": {
            "meta": {"gaps": [{"gap": "אין מידע על תמחור"}]},
            "risks_and_gaps_he": ["אין מידע על תמחור"],
        }
    }
    result = gaps.extract_gaps_from_previous(previous)
    assert result.count("אין מידע על תמחור") == 1


def test_extract_gaps_handles_json_string_data() -> None:
    import json

    previous = {"data": json.dumps({"risks_and_gaps_he": ["פער מ-JSON מחרוזת"]})}
    result = gaps.extract_gaps_from_previous(previous)
    assert "פער מ-JSON מחרוזת" in result


def test_extract_gaps_handles_malformed_json_string_gracefully() -> None:
    previous = {"data": "{not valid json"}
    assert gaps.extract_gaps_from_previous(previous) == []


# --------------------------------------------------------------------------
# build_gap_followup_topics
# --------------------------------------------------------------------------


def test_build_gap_followup_topics_caps_at_max_gaps() -> None:
    gap_list = [f"פער {i}" for i in range(10)]
    topics = gaps.build_gap_followup_topics(gap_list, product_name="SPECTRO XR", vendor="Elbit Systems", max_gaps=6)
    assert len(topics) == 6


def test_build_gap_followup_topics_question_names_the_gap() -> None:
    topics = gaps.build_gap_followup_topics(
        ["לא נמצא מחיר רשמי"], product_name="SPECTRO XR", vendor="Elbit Systems"
    )
    assert len(topics) == 1
    assert "לא נמצא מחיר רשמי" in topics[0].question_he
    assert "SPECTRO XR" in topics[0].question_he


def test_build_gap_followup_topics_empty_gaps_yields_no_topics() -> None:
    assert gaps.build_gap_followup_topics([], product_name="SPECTRO XR", vendor=None) == []


def test_build_gap_followup_topics_keys_are_unique() -> None:
    topics = gaps.build_gap_followup_topics(["a", "b", "c"], product_name="X", vendor=None)
    keys = [t.key for t in topics]
    assert len(keys) == len(set(keys))


# --------------------------------------------------------------------------
# gap_status
# --------------------------------------------------------------------------


def test_gap_status_closed_when_found_with_high_confidence() -> None:
    topics = gaps.build_gap_followup_topics(["לא נמצא מחיר"], product_name="X", vendor=None)
    outcomes = {topics[0].key: ("found", 0.8, [3, 4])}
    result = gaps.gap_status(topics, outcomes)
    assert result == [{"gap": "לא נמצא מחיר", "status": "closed", "cites": [3, 4]}]


def test_gap_status_open_when_not_found() -> None:
    topics = gaps.build_gap_followup_topics(["לא נמצא מחיר"], product_name="X", vendor=None)
    outcomes = {topics[0].key: ("not_found", 0.0, [])}
    result = gaps.gap_status(topics, outcomes)
    assert result[0]["status"] == "open"


def test_gap_status_open_when_found_but_low_confidence() -> None:
    topics = gaps.build_gap_followup_topics(["לא נמצא מחיר"], product_name="X", vendor=None)
    outcomes = {topics[0].key: ("found", 0.2, [1])}
    result = gaps.gap_status(topics, outcomes)
    assert result[0]["status"] == "open"


def test_gap_status_missing_outcome_defaults_to_open() -> None:
    topics = gaps.build_gap_followup_topics(["לא נמצא מחיר"], product_name="X", vendor=None)
    result = gaps.gap_status(topics, {})
    assert result[0]["status"] == "open"
    assert result[0]["cites"] == []


# --------------------------------------------------------------------------
# diff_new_gaps
# --------------------------------------------------------------------------


def test_diff_new_gaps_finds_gaps_not_in_previous() -> None:
    result = gaps.diff_new_gaps(previous_gaps=["פער ישן"], current_gaps=["פער ישן", "פער חדש"])
    assert result == [{"gap": "פער חדש", "status": "new", "cites": []}]


def test_diff_new_gaps_empty_when_nothing_new() -> None:
    assert gaps.diff_new_gaps(previous_gaps=["a"], current_gaps=["a"]) == []


def test_diff_new_gaps_dedupes_current() -> None:
    result = gaps.diff_new_gaps(previous_gaps=[], current_gaps=["x", "x"])
    assert len(result) == 1
