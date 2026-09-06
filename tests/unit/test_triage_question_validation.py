"""Tests for the deep-search question repair added to `eoa.pipeline.triage` (U11/F17/F18,
docs/REVIEW_2026-09-05.md).

Repro: 12 of ~20 investigations ended with "לא נמצא מידע מספק במסגרת התקציב", and inspection showed
some auto-generated questions were malformed -- e.g. investigation #46's question was "האם הכתבה
מספקת את כל המידע הנדרש" with no article/entities attached, which is not a searchable research
question at all. `_ensure_valid_investigation_question` deterministically repairs (rather than
rejects) any question that fails validation, so a bad LLM output can never reach the search budget.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_triage_question_validation.py -q``
"""

from __future__ import annotations

from eoa.pipeline import triage


def _item(**overrides):
    base = {
        "id": 46,
        "title": "Rheinmetall and GDLS deliver first XM30 prototypes to US Army",
        "entities_mentioned": ["Rheinmetall", "GDLS"],
        "summary_he": 'ריינמטל ו-GDLS מסרו את אבי-הטיפוס הראשונים של ה-XM30 לצבא ארה"ב',
    }
    base.update(overrides)
    return base


class TestQuestionIsValid:
    def test_rejects_meta_phrase_question(self) -> None:
        assert not triage._question_is_valid("האם הכתבה מספקת את כל המידע הנדרש", _item())

    def test_rejects_too_short_question(self) -> None:
        assert not triage._question_is_valid("מה קרה שם בדיוק?", _item())

    def test_rejects_bare_article_reference_without_context(self) -> None:
        question = "האם יש עוד פרטים חשובים שהכתבה הזאת לא מזכירה כלל ולגמרי משמיטה מהקורא התמים"
        assert not triage._question_is_valid(question, _item())

    def test_accepts_self_contained_question_with_entities(self) -> None:
        question = (
            'מהו המחיר ליחידה של אב-הטיפוס XM30 שמסרו Rheinmetall ו-GDLS לצבא ארה"ב, '
            "ומי היו המציעים המפסידים במכרז?"
        )
        assert triage._question_is_valid(question, _item())

    def test_bare_article_reference_ok_if_entities_also_present(self) -> None:
        question = (
            "מעבר למה שהכתבה על Rheinmetall ו-GDLS מוסרת, מהו לוח הזמנים המלא של תוכנית XM30 "
            "ומה התקציב הכולל שאושר לה על ידי הפנטגון?"
        )
        assert triage._question_is_valid(question, _item())


class TestEnsureValidInvestigationQuestion:
    def test_valid_question_passed_through_unchanged(self) -> None:
        good_q = (
            'מהו המחיר ליחידה של אב-הטיפוס XM30 שמסרו Rheinmetall ו-GDLS לצבא ארה"ב, '
            "ומי היו המציעים המפסידים במכרז?"
        )
        q, seed = triage._ensure_valid_investigation_question(
            _item(), good_q, "Rheinmetall GDLS XM30 unit price"
        )
        assert q == good_q
        assert seed == "Rheinmetall GDLS XM30 unit price"

    def test_malformed_question_repaired_with_title_and_entities(self) -> None:
        bad_q = "האם הכתבה מספקת את כל המידע הנדרש"
        q, seed = triage._ensure_valid_investigation_question(_item(), bad_q, "")
        assert len(q.split()) >= triage._MIN_QUESTION_WORDS
        assert "XM30" in q or "Rheinmetall" in q or "GDLS" in q
        assert triage._question_is_valid(q, _item())
        assert seed  # non-empty fallback seed produced
        assert len(seed.split()) >= 2

    def test_empty_question_repaired(self) -> None:
        q, seed = triage._ensure_valid_investigation_question(_item(), "", "")
        assert triage._question_is_valid(q, _item())
        assert seed

    def test_short_seed_replaced_with_entity_based_fallback(self) -> None:
        good_q = (
            'מהו המחיר ליחידה של אב-הטיפוס XM30 שמסרו Rheinmetall ו-GDLS לצבא ארה"ב, '
            "ומי היו המציעים המפסידים במכרז?"
        )
        _, seed = triage._ensure_valid_investigation_question(_item(), good_q, "XM30")
        assert seed != "XM30"
        assert "Rheinmetall" in seed or "GDLS" in seed


class TestEnqueueDeepSearchNeverEnqueuesMalformedQuestion:
    def test_enqueue_repairs_bad_question_and_carries_context(self, monkeypatch) -> None:
        captured = {}

        def fake_enqueue_job(kind, payload, priority=3):
            captured["kind"] = kind
            captured["payload"] = payload
            captured["priority"] = priority
            return 123

        monkeypatch.setattr("eoa.memory.relational.enqueue_job", fake_enqueue_job)

        from eoa.llm.schemas.analysis import TriageOut

        out = TriageOut(
            score=9,
            level="red",
            novelty=5,
            magnitude=5,
            core_relevance=5,
            reason_he="חשוב",
            needs_deep_search=True,
            deep_search_question="האם הכתבה מספקת את כל המידע הנדרש",
            deep_search_seed_en="",
        )
        triage._enqueue_deep_search(_item(), out)

        assert captured["kind"] == "deep_search"
        payload = captured["payload"]
        assert len(payload["question"].split()) >= triage._MIN_QUESTION_WORDS
        assert "context_he" in payload and "XM30" in payload["context_he"]
        assert payload["level"] == "red"
        assert captured["priority"] == 1
