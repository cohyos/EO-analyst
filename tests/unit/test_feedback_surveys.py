"""Unit tests for `eoa.feedback.surveys` (FR-11: question bank, rotation, `parse_free_text`,
answer ingestion).

No DB: this module's own tiny DB boundary (`_fetchone`/`_execute`) and
`add_lesson` (imported from `eoa.memory.relational`) are monkeypatched at
the `eoa.feedback.surveys` module level, mirroring
`tests/unit/test_obsidian_export.py`'s stubbing style.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_feedback_surveys.py -q``
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

from eoa.feedback import surveys

# --------------------------------------------------------------------------
# question bank shape
# --------------------------------------------------------------------------


class TestQuestionBank:
    def test_twelve_questions(self) -> None:
        assert len(surveys.QUESTION_BANK) == 12

    def test_roughly_70_30_closed_open_split(self) -> None:
        closed = sum(1 for q in surveys.QUESTION_BANK if q["type"] in ("choice", "scale"))
        open_ = sum(1 for q in surveys.QUESTION_BANK if q["type"] == "open")
        assert closed + open_ == 12
        assert closed >= 7  # ~70%
        assert open_ >= 3  # ~30%

    def test_all_ids_unique(self) -> None:
        ids = [q["id"] for q in surveys.QUESTION_BANK]
        assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------
# rotation determinism
# --------------------------------------------------------------------------


class TestRotatingSubset:
    def test_same_seed_same_subset(self) -> None:
        a = surveys.rotating_subset(42)
        b = surveys.rotating_subset(42)
        assert [q["id"] for q in a] == [q["id"] for q in b]

    def test_default_k_is_6(self) -> None:
        assert len(surveys.rotating_subset(0)) == 6

    def test_custom_k(self) -> None:
        assert len(surveys.rotating_subset(0, k=4)) == 4

    def test_wraps_around_bank(self) -> None:
        n = len(surveys.QUESTION_BANK)
        ids = [q["id"] for q in surveys.rotating_subset(n - 1, k=3)]
        assert ids[0] == surveys.QUESTION_BANK[n - 1]["id"]
        assert ids[1] == surveys.QUESTION_BANK[0]["id"]  # wrapped around

    def test_different_seeds_can_yield_different_subsets(self) -> None:
        a = [q["id"] for q in surveys.rotating_subset(0)]
        b = [q["id"] for q in surveys.rotating_subset(3)]
        assert a != b


# --------------------------------------------------------------------------
# parse_free_text
# --------------------------------------------------------------------------


class TestParseFreeText:
    def test_shorter_request(self) -> None:
        assert surveys.parse_free_text("אני מעדיף דוחות יותר קצר בבקשה") == {"length": "shorter"}

    def test_longer_request(self) -> None:
        assert surveys.parse_free_text("תעשו את זה יותר ארוך ומפורט") == {"length": "longer"}

    def test_add_watchlist(self) -> None:
        result = surveys.parse_free_text("אשמח להוסיף מעקב אחרי Anduril, זה חשוב.")
        assert result["add_watchlist"] == "Anduril"

    def test_not_relevant(self) -> None:
        result = surveys.parse_free_text("לא רלוונטי: מאמרים אקדמיים כלליים")
        assert result["not_relevant"] == "מאמרים אקדמיים כלליים"

    def test_empty_text_returns_empty_dict(self) -> None:
        assert surveys.parse_free_text("") == {}
        assert surveys.parse_free_text("   ") == {}

    def test_unrecognized_text_returns_empty_dict(self) -> None:
        assert surveys.parse_free_text("הדוח היה מעניין, תודה") == {}

    def test_multiple_signals_in_one_text(self) -> None:
        result = surveys.parse_free_text("יותר קצר בבקשה. גם להוסיף מעקב אחרי Epirus, תודה.")
        assert result["length"] == "shorter"
        assert result["add_watchlist"] == "Epirus"


# --------------------------------------------------------------------------
# create_for_report
# --------------------------------------------------------------------------


class TestCreateForReport:
    def test_returns_existing_survey_without_creating(self, monkeypatch: pytest.MonkeyPatch) -> None:
        existing = {"id": 1, "report_id": 7, "questions": surveys.rotating_subset(7), "answers": {}}
        monkeypatch.setattr(surveys, "_fetchone", lambda q, p=None: existing)

        result = surveys.create_for_report(7)

        assert result == {"id": 1, "report_id": 7, "questions": existing["questions"], "answers": {}}

    def test_creates_new_survey_when_none_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"n": 0}

        def fake_fetchone(query, params=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # no existing survey for this report
            return {"id": 2, "report_id": 7, "questions": surveys.rotating_subset(7), "answers": {}}

        monkeypatch.setattr(surveys, "_fetchone", fake_fetchone)

        result = surveys.create_for_report(7)

        assert result["id"] == 2
        assert result["report_id"] == 7
        assert len(result["questions"]) == 6


# --------------------------------------------------------------------------
# ingest_answers
# --------------------------------------------------------------------------


def _survey_row(question_ids: list[str]) -> dict:
    by_id = {q["id"]: q for q in surveys.QUESTION_BANK}
    return {"id": 10, "report_id": 5, "questions": [by_id[qid] for qid in question_ids], "answers": {}}


class TestIngestAnswers:
    def test_missing_survey_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(surveys, "_fetchone", lambda q, p=None: None)
        assert surveys.ingest_answers(999, {}) is None

    def test_low_clarity_score_creates_style_lesson(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = _survey_row(["q6"])
        monkeypatch.setattr(surveys, "_fetchone", lambda q, p=None: row)
        monkeypatch.setattr(surveys, "_execute", lambda q, p=None: None)
        lessons: list[tuple] = []
        monkeypatch.setattr(
            surveys,
            "add_lesson",
            lambda kind, text, source_ref=None: lessons.append((kind, text, source_ref)),
        )

        surveys.ingest_answers(10, {"q6": "1"})

        assert len(lessons) == 1
        assert lessons[0][0] == "style"
        assert "1/5" in lessons[0][1]
        assert lessons[0][2] == "survey:10:q6"

    def test_high_clarity_score_no_lesson(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = _survey_row(["q6"])
        monkeypatch.setattr(surveys, "_fetchone", lambda q, p=None: row)
        monkeypatch.setattr(surveys, "_execute", lambda q, p=None: None)
        lessons: list = []
        monkeypatch.setattr(surveys, "add_lesson", lambda *a, **k: lessons.append(a))

        surveys.ingest_answers(10, {"q6": "5"})

        assert lessons == []

    def test_frequency_too_high_creates_style_lesson(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = _survey_row(["q7"])
        monkeypatch.setattr(surveys, "_fetchone", lambda q, p=None: row)
        monkeypatch.setattr(surveys, "_execute", lambda q, p=None: None)
        lessons: list[tuple] = []
        monkeypatch.setattr(
            surveys, "add_lesson", lambda kind, text, source_ref=None: lessons.append((kind, text))
        )

        surveys.ingest_answers(10, {"q7": "יותר מדי"})

        assert len(lessons) == 1
        assert lessons[0][0] == "style"

    def test_frequency_fits_no_lesson(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = _survey_row(["q7"])
        monkeypatch.setattr(surveys, "_fetchone", lambda q, p=None: row)
        monkeypatch.setattr(surveys, "_execute", lambda q, p=None: None)
        lessons: list = []
        monkeypatch.setattr(surveys, "add_lesson", lambda *a, **k: lessons.append(a))

        surveys.ingest_answers(10, {"q7": "מתאימה"})

        assert lessons == []

    def test_open_watchlist_request_creates_lesson_and_proposal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = _survey_row(["q9"])
        monkeypatch.setattr(surveys, "_fetchone", lambda q, p=None: row)
        monkeypatch.setattr(surveys, "_execute", lambda q, p=None: None)
        lessons: list[tuple] = []
        monkeypatch.setattr(
            surveys,
            "add_lesson",
            lambda kind, text, source_ref=None: lessons.append((kind, text, source_ref)),
        )
        proposals: list[str] = []
        monkeypatch.setattr(
            surveys, "_propose_watchlist_addition", lambda target, source_ref: proposals.append(target)
        )

        surveys.ingest_answers(10, {"q9": "אשמח להוסיף מעקב אחרי Epirus, תודה."})

        assert any(kind == "watchlist" for kind, _text, _ref in lessons)
        assert proposals == ["Epirus"]

    def test_open_not_relevant_creates_decision_lesson(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = _survey_row(["q10"])
        monkeypatch.setattr(surveys, "_fetchone", lambda q, p=None: row)
        monkeypatch.setattr(surveys, "_execute", lambda q, p=None: None)
        lessons: list[tuple] = []
        monkeypatch.setattr(
            surveys, "add_lesson", lambda kind, text, source_ref=None: lessons.append((kind, text))
        )

        surveys.ingest_answers(10, {"q10": "לא רלוונטי: פוסט שיווקי"})

        assert lessons == [("decision", "המשתמש סימן כלא רלוונטי: פוסט שיווקי")]

    def test_unrecognized_open_text_preserved_as_decision_lesson(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = _survey_row(["q11"])
        monkeypatch.setattr(surveys, "_fetchone", lambda q, p=None: row)
        monkeypatch.setattr(surveys, "_execute", lambda q, p=None: None)
        lessons: list[tuple] = []
        monkeypatch.setattr(
            surveys, "add_lesson", lambda kind, text, source_ref=None: lessons.append((kind, text))
        )

        surveys.ingest_answers(10, {"q11": "הפרק על C-UAS היה הכי מעניין"})

        assert lessons == [("decision", "הפרק על C-UAS היה הכי מעניין")]

    def test_blank_open_answer_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = _survey_row(["q11"])
        monkeypatch.setattr(surveys, "_fetchone", lambda q, p=None: row)
        monkeypatch.setattr(surveys, "_execute", lambda q, p=None: None)
        lessons: list = []
        monkeypatch.setattr(surveys, "add_lesson", lambda *a, **k: lessons.append(a))

        surveys.ingest_answers(10, {"q11": "   "})

        assert lessons == []

    def test_unknown_question_id_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = _survey_row(["q11"])
        monkeypatch.setattr(surveys, "_fetchone", lambda q, p=None: row)
        monkeypatch.setattr(surveys, "_execute", lambda q, p=None: None)
        lessons: list = []
        monkeypatch.setattr(surveys, "add_lesson", lambda *a, **k: lessons.append(a))

        surveys.ingest_answers(10, {"not_a_real_qid": "value"})

        assert lessons == []
