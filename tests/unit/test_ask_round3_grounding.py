"""Round 3 D5 grounding guards (docs/qa/loop/round_2_judge.md's D5 new-findings section).

Round 2's judge, re-sampling the same 8 golden questions a second time, found two NEW severe
fabrications that slipped past every round-2 guard (citation presence, topic anchor) because the
fabricated text kept a real `[n]` marker and the question's own topic word:

- Q2 (Iron Beam): a Rafael "Iron Beam" contract narrative attributed to a source that is actually
  AeroVironment's own, unrelated laser programme -- a cross-source conflation.
- Q4 (DROIC): an invented professor, university and project name, conflating two unrelated
  retrieved arXiv papers into one narrative -- an ungrounded entity.
- Q3 (Greece/LORA): a literal, unsubstituted `[n=5]` template token leaked into a rendered
  heading.

Covers `eoa.api.ask_grounding` (the new module implementing the grounded-entity check and the
cross-source conflation guard, plus the template-leak sanitiser) both as pure functions and
end-to-end through `POST /api/ask`'s SSE stream, plus the strengthened topic-anchor guard in
`eoa.api.routes.ask` (gap statement first, substitute content demoted into its own section).

Run with: ``PYTHONPATH=agent .venv\\Scripts\\python -m pytest tests/unit/test_ask_round3_grounding.py -q``
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from eoa.api import ask_grounding, services
from eoa.api.routes import ask as ask_route

# ---------------------------------------------------------------------------------------------
# 1. sanitize_citation_markers -- template-leak sanitiser
# ---------------------------------------------------------------------------------------------


class TestSanitizeCitationMarkers:
    def test_empty_text_is_a_no_op(self) -> None:
        assert ask_grounding.sanitize_citation_markers("") == ("", 0)

    def test_no_leak_returns_text_unchanged_and_zero_count(self) -> None:
        text = "תשובה תקינה עם ציטוט [1] אמיתי."
        assert ask_grounding.sanitize_citation_markers(text) == (text, 0)

    def test_strips_bare_n_placeholder(self) -> None:
        cleaned, count = ask_grounding.sanitize_citation_markers("עובדה כלשהי [n].")
        assert "[n]" not in cleaned
        assert count == 1

    def test_live_repro_n_equals_5_leak_in_a_heading(self) -> None:
        """Live-verified 2026-09-06 (docs/qa/loop/round_2_judge.md, D5 Q3): a literal `[n=5]`
        token leaked into a rendered `### עובדות מרכזיות` heading."""
        cleaned, count = ask_grounding.sanitize_citation_markers("### עובדות מרכזיות [n=5]\n- עובדה [1].")
        assert "[n=5]" not in cleaned
        assert count == 1
        assert "[1]" in cleaned  # a real citation elsewhere must survive

    def test_strips_curly_brace_n_placeholder(self) -> None:
        cleaned, count = ask_grounding.sanitize_citation_markers("תוצאה {n} לא ברורה.")
        assert "{n}" not in cleaned
        assert count == 1

    def test_case_insensitive_and_spaced_variants_all_stripped(self) -> None:
        for leaked in ["[N=12]", "[ n = 3 ]", "[N]", "{N}"]:
            cleaned, count = ask_grounding.sanitize_citation_markers(f"טקסט {leaked} בהמשך.")
            assert leaked not in cleaned
            assert count == 1

    def test_valid_numeric_citations_of_any_length_are_never_touched(self) -> None:
        text = "עובדה אחת [1] ועובדה שנייה [23] ועובדה שלישית [104]."
        assert ask_grounding.sanitize_citation_markers(text) == (text, 0)

    def test_multiple_leaks_are_all_removed_and_counted(self) -> None:
        cleaned, count = ask_grounding.sanitize_citation_markers("א [n=1] ב [n] ג [1] ד {n}.")
        assert count == 3
        assert "[1]" in cleaned
        assert "[n" not in cleaned and "{n}" not in cleaned


# ---------------------------------------------------------------------------------------------
# 2. ground_and_filter_answer -- grounded-entity check + cross-source conflation guard
# ---------------------------------------------------------------------------------------------


def _src(id: int, title: str, text: str, **kw: Any) -> dict[str, Any]:
    base = {
        "id": id,
        "title": title,
        "url": f"https://example.test/{id}",
        "clean_text": text,
        "summary_he": "",
        "level": "yellow",
        "source_name": "מקור",
        "report_kind": None,
        "_is_context": False,
    }
    base.update(kw)
    return base


class TestGroundAndFilterAnswerNoOp:
    def test_no_retrieved_sources_is_a_no_op(self) -> None:
        text = "תשובה כלשהי המבוססת על ידע כללי, לא מהמאגר."
        assert ask_grounding.ground_and_filter_answer(text, "שאלה", []) == (text, 0)

    def test_blank_answer_is_a_no_op(self) -> None:
        rows = [_src(1, "כותרת", "טקסט")]
        assert ask_grounding.ground_and_filter_answer("   ", "שאלה", rows) == ("   ", 0)

    def test_fully_grounded_answer_is_unchanged(self) -> None:
        rows = [_src(1, "XM30 program update", "Lynx XM30 is the GDLS-built Bradley replacement.")]
        text = "### עובדות מרכזיות\n- תוכנית ה-XM30 מבוססת על GDLS Lynx [1]."
        assert ask_grounding.ground_and_filter_answer(text, "מה קורה עם XM30?", rows) == (text, 0)


class TestGroundedEntityCheck:
    """The Q4 pattern: an invented multi-word proper noun (person/university/project) not present
    anywhere in the question, the retrieved sources, or the canonical watchlist."""

    def test_live_repro_invented_professor_university_project_sentence_is_removed(self) -> None:
        rows = [
            _src(1, "SAR super-resolution via deep learning", "מאמר על שיפור רזולוציית תמונות SAR."),
            _src(2, "Thai scene-text OCR benchmark", "מאמר על זיהוי תווים אופטי (OCR) בטקסט תאי."),
        ]
        text = (
            "### עובדות מרכזיות\n"
            "- המחקר בוצע בהובלת Kunat Pipatanakul מ-Ratchaburi Institute of Technology, "
            "במסגרת פרויקט בשם Wayu-Paxa-OCR-Zero [1][2].\n"
            "- שני המאמרים עוסקים בשיפור דיוק זיהוי תחת תנאי רעש [1][2]."
        )
        new_text, removed = ask_grounding.ground_and_filter_answer(
            text, "מהי המגמה הטכנולוגית האחרונה ב-DROIC?", rows
        )
        assert removed == 1
        assert "Kunat Pipatanakul" not in new_text
        assert "Ratchaburi Institute of Technology" not in new_text
        assert "Wayu-Paxa-OCR-Zero" not in new_text
        # the genuinely-grounded second bullet must survive untouched
        assert "שני המאמרים עוסקים בשיפור דיוק זיהוי תחת תנאי רעש [1][2]." in new_text

    def test_invented_entity_present_only_in_the_direct_answer_gets_a_gap_sentence(self) -> None:
        """The fabricated claim sentence itself must be gone -- but the gap sentence the brief
        specifies (``"המקורות שנשלפו אינם מזכירים X — לא ניתן לאשר"``) *names* the missing entity
        by design, so the entity string legitimately reappears there, just no longer asserted as
        fact."""
        rows = [_src(1, "Thai scene-text OCR benchmark", "מאמר על זיהוי תווים אופטי בטקסט תאי.")]
        text = "המחקר החדש ביותר בתחום בוצע על ידי Kunat Pipatanakul מאוניברסיטת Ratchaburi Institute."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "מהי המגמה האחרונה?", rows)
        assert removed >= 1
        assert text not in new_text  # the original fabricated claim sentence is gone
        assert "לא ניתן לאשר" in new_text
        assert "המקורות שנשלפו אינם מזכירים" in new_text

    def test_entity_present_in_the_question_itself_is_grounded(self) -> None:
        rows = [_src(1, "Bradley replacement program", "פרטים על התוכנית.")]
        text = "### עובדות מרכזיות\n- תוכנית ה-XM30 (Bradley הבא) נמצאת בשלב פיתוח מתקדם [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(
            text, "מה קורה עם XM30 (Bradley הבא)?", rows
        )
        assert removed == 0
        assert new_text == text

    def test_entity_resolving_to_the_canonical_watchlist_is_grounded_even_if_absent_from_sources(
        self,
    ) -> None:
        """A real, known watchlist entity (Elbit) mentioned without a matching literal source
        string must not be treated as an invented multi-word proper noun. Uncited on purpose --
        an *attributed* (cited) mention of a watchlist entity is the separate cross-source
        conflation guard's concern (tested below), not this one's."""
        rows = [_src(1, "Generic C-UAS market overview", "סקירה כללית של שוק ה-C-UAS.")]
        text = "### הערכת האנליסט\nElbit Systems ממשיכה לפתח פתרונות C-UAS מתקדמים בתחום."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה כלשהי", rows)
        assert removed == 0
        assert new_text == text

    def test_invented_money_figure_not_present_anywhere_is_removed(self) -> None:
        rows = [_src(1, "Iron Beam program update", "רפאל ממשיכה בפיתוח מגן אור.")]
        text = "### עובדות מרכזיות\n- החוזה מוערך בכ-940 מיליון דולר [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה על מגן אור", rows)
        assert removed == 1
        assert "940" not in new_text

    def test_live_repro_short_domain_acronym_compound_is_never_flagged(self) -> None:
        """Live-verified 2026-09-06 (throwaway 8766, real golden Q1 XM30 answer, agy provider):
        a real bullet asking whether the XM30 has "an integrated C-UAS system with a dedicated
        EO/IR component" was dropped entirely because "C-UAS" (a standard domain acronym named in
        system_analyst.md's own domain description, not an invented entity) parsed as a two-segment
        candidate ("C" + "UAS") absent from this question's specific retrieval. Neither segment of
        a real acronym-style compound like this is a genuine multi-character "word", so it must
        never reach the grounded-entity check as a candidate at all."""
        rows = [_src(1, "XM30 program update", "Lynx XM30 prototype delivered to the US Army.")]
        text = "### פערים / מה לא ידוע\n- האם קיימת מערכת אינטגרלית ליירוט רחפנים (C-UAS) ברכב."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "מה קורה עם XM30?", rows)
        assert removed == 0
        assert new_text == text

    def test_live_repro_analyst_assessment_prose_is_exempt_from_the_grounded_entity_check(
        self,
    ) -> None:
        """Live-verified 2026-09-06 (throwaway 8766, real golden Q1 XM30 answer, agy provider): a
        reasonable analyst-speculation sentence using standard domain vocabulary ("Edge AI",
        "Sensor Fusion") was removed because those exact bigrams happened not to appear in this
        specific retrieval's own source text -- but `ask_answer_format.md` rule 3 explicitly
        exempts the "### הערכת האנליסט" section from any sourcing requirement at all ("זו דעה
        מבוססת, לא ציטוט"), so this section must never be gutted by a grounding check."""
        rows = [_src(1, "XM30 program update", "Rheinmetall and GDLS delivered XM30 prototypes.")]
        text = (
            "### הערכת האנליסט\n"
            "נוכחותה של Anduril בצוות מרמזת על כיוון של שילוב בינה חזותית מבוססת Edge AI "
            "לעיבוד נתונים בזמן אמת, לרבות היתוך מידע (Sensor Fusion) בין חיישנים שונים."
        )
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "מה קורה עם XM30?", rows)
        assert removed == 0
        assert new_text == text

    def test_money_figure_present_in_a_source_is_grounded(self) -> None:
        rows = [_src(1, "Iron Beam contract", "רפאל חתמה על חוזה בשווי 500 מיליון דולר.")]
        text = "### עובדות מרכזיות\n- החוזה מוערך בכ-500 מיליון דולר [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה על מגן אור", rows)
        assert removed == 0
        assert new_text == text


class TestCrossSourceConflationGuard:
    """The Q2 pattern: a real, watchlist-recognised entity attributed to a source that does not
    actually mention it."""

    def test_live_repro_rafael_attributed_to_an_aerovironment_only_source_is_removed(self) -> None:
        rows = [
            _src(
                1,
                "AeroVironment unveils new laser interception program",
                "AeroVironment הכריזה על תוכנית לייזר חדשה בשווי כ-465 מיליון דולר להגנה אווירית.",
                entities_mentioned=["AeroVironment"],
            )
        ]
        text = "### עובדות מרכזיות\n- רפאל חתמה על חוזה מגן אור (Iron Beam) בשווי 465 מיליון דולר [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(
            text, "מהם פרטי חוזה מגן אור העדכני ביותר של רפאל?", rows
        )
        assert removed == 1
        assert "רפאל" not in new_text
        assert "Iron Beam" not in new_text

    def test_entity_correctly_attributed_to_its_own_source_is_kept(self) -> None:
        rows = [
            _src(
                1,
                "AeroVironment unveils new laser interception program",
                "AeroVironment הכריזה על תוכנית לייזר חדשה בשווי כ-465 מיליון דולר.",
                entities_mentioned=["AeroVironment"],
            )
        ]
        text = "### עובדות מרכזיות\n- AeroVironment חתמה על חוזה לייזר חדש בשווי 465 מיליון דולר [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_entity_attributed_to_a_source_that_does_mention_it_among_several_citations_is_kept(
        self,
    ) -> None:
        rows = [
            _src(1, "AeroVironment laser program", "AeroVironment laser announcement."),
            _src(2, "Rafael Iron Beam deployment", "רפאל פרסה את מערכת מגן אור (Iron Beam) בשדה."),
        ]
        text = "### עובדות מרכזיות\n- רפאל פרסה את מגן אור (Iron Beam) בשטח [1][2]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_no_citation_in_the_unit_skips_the_conflation_check_entirely(self) -> None:
        """An uncited sentence (e.g. the analyst-assessment prose section, which the format rules
        explicitly exempt from [n]) must not be flagged just for naming a watchlist entity."""
        rows = [_src(1, "AeroVironment laser program", "AeroVironment laser announcement.")]
        text = "### הערכת האנליסט\nרפאל ואלביט הן שתי הספקיות המובילות בתחום ה-EO/IR הישראלי."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text


# ---------------------------------------------------------------------------------------------
# 3. End-to-end SSE -- full route wiring, real ask_build_messages, mocked LLM
# ---------------------------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from eoa import db

    monkeypatch.setattr(db, "get_pool", lambda: object())
    monkeypatch.setattr(db, "close_pool", lambda: None)

    from eoa.api.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _sse_events(body: str) -> list[dict]:
    events = []
    for chunk in body.split("\n\n"):
        line = next((ln for ln in chunk.split("\n") if ln.startswith("data:")), None)
        if not line:
            continue
        events.append(json.loads(line[len("data:") :].strip()))
    return events


def _row(id: int, **kw: Any) -> dict[str, Any]:
    base = {
        "id": id,
        "title": "כותרת",
        "url": "https://example.com",
        "clean_text": "טקסט",
        "summary_he": "תקציר",
        "key_facts": [],
        "level": "yellow",
        "source_name": "מקור",
        "report_kind": None,
        "entities_mentioned": [],
        "_is_context": False,
    }
    base.update(kw)
    return base


def _mock_ask_with_real_sources(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    chunks: list[str],
    *,
    question: str,
) -> None:
    """Unlike round 2's `_mock_ask` (which stubs `ask_build_messages` to return canned citations
    detached from any real row), this keeps the real `services.ask_build_messages` so the [n]
    numbering the grounding guard relies on is the exact same numbering the model's citations refer
    to -- only retrieval and the LLM calls themselves are mocked."""
    from eoa.llm import ollama_client

    monkeypatch.setattr(services, "ask_retrieve", lambda *a, **k: rows)
    monkeypatch.setattr(ollama_client, "resolve_provider_info", lambda provider: ("ollama", "resident"))
    monkeypatch.setattr(ollama_client, "chat_stream", lambda *a, **k: iter(chunks))
    monkeypatch.setattr(ollama_client, "chat", lambda *a, **k: type("R", (), {"content": ""})())
    return question


class TestGroundingGuardEndToEnd:
    def test_conflated_sentence_is_stripped_and_reported_in_answer_final(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [
            _row(
                1,
                title="AeroVironment unveils new laser interception program",
                clean_text="AeroVironment הכריזה על תוכנית לייזר חדשה בשווי כ-465 מיליון דולר.",
                entities_mentioned=["AeroVironment"],
            )
        ]
        question = _mock_ask_with_real_sources(
            monkeypatch,
            rows,
            ["### עובדות מרכזיות\n- רפאל חתמה על חוזה מגן אור (Iron Beam) בשווי 465 מיליון דולר [1]."],
            question="מהם פרטי חוזה מגן אור (Iron Beam) העדכני ביותר של רפאל?",
        )
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        grounding_finals = [e for e in events if e["type"] == "answer_final" and "ungrounded_removed" in e]
        assert grounding_finals, "expected a grounding-guard answer_final event"
        assert grounding_finals[0]["ungrounded_removed"] >= 1
        assert "רפאל" not in grounding_finals[0]["text"] or "לא ניתן לאשר" in grounding_finals[0]["text"]
        assert events[-1]["type"] == "done"

    def test_clean_grounded_answer_never_triggers_the_grounding_guard(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [
            _row(
                1,
                title="XM30 program update",
                clean_text="Lynx XM30 is the GDLS-built Bradley replacement program.",
            )
        ]
        question = _mock_ask_with_real_sources(
            monkeypatch,
            rows,
            ["### עובדות מרכזיות\n- תוכנית ה-XM30 מבוססת על GDLS Lynx [1]."],
            question="מה קורה עם XM30 (Bradley הבא)?",
        )
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        assert not [e for e in events if e["type"] == "answer_final" and "ungrounded_removed" in e]

    def test_template_leak_is_sanitized_end_to_end(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [_row(1, title="XM30", clean_text="Lynx XM30 program details.")]
        question = _mock_ask_with_real_sources(
            monkeypatch,
            rows,
            ["### עובדות מרכזיות [n=5]\n- פרט אחד על XM30 [1]."],
            question="מה קורה עם XM30?",
        )
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        finals = [e for e in events if e["type"] == "answer_final"]
        assert finals, "expected at least one answer_final event"
        assert "[n=5]" not in finals[0]["text"]
        assert "[1]" in finals[0]["text"]


# ---------------------------------------------------------------------------------------------
# 4. Strengthened topic-anchor guard -- gap statement first, substitute content demoted
# ---------------------------------------------------------------------------------------------


class TestStrengthenedAnchorGuard:
    def test_off_topic_answer_gets_gap_statement_and_labelled_section(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from eoa.llm import ollama_client

        monkeypatch.setattr(services, "ask_retrieve", lambda *a, **k: [])
        monkeypatch.setattr(
            services,
            "ask_build_messages",
            lambda *a, **k: (
                [],
                [
                    {
                        "n": 1,
                        "item_id": 1,
                        "title": "מקור",
                        "url": "https://a.test",
                        "level": "yellow",
                        "source_name": "x",
                    }
                ],
            ),
        )
        monkeypatch.setattr(ollama_client, "resolve_provider_info", lambda provider: ("ollama", "resident"))
        monkeypatch.setattr(
            ollama_client,
            "chat_stream",
            lambda *a, **k: iter(
                [
                    "### עובדות מרכזיות\n* יוון חתמה על עסקת נשק ענקית, כולל David's Sling, "
                    "Barak MX ו-Spyder [1]."
                ]
            ),
        )
        monkeypatch.setattr(ollama_client, "chat", lambda *a, **k: type("R", (), {"content": ""})())

        question = "עסקת ה-LORA היוונית (Greece) -- מה המשמעות עבור התעשייה הביטחונית הישראלית?"
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        finals = [e for e in events if e["type"] == "answer_final"]
        off_topic = [e for e in finals if e["text"].startswith(ask_route._OFF_TOPIC_PREFIX)]
        assert off_topic, "expected the off-topic guard to fire"
        text = off_topic[-1]["text"]
        # gap statement names the missing anchor and comes before the substitute content
        gap_idx = text.find("לא ניתן לאשר")
        section_idx = text.find("### הקשר קרוב (לא התשובה)")
        content_idx = text.find("David's Sling")
        assert text.startswith(ask_route._OFF_TOPIC_PREFIX)
        assert -1 < gap_idx < section_idx < content_idx
        assert "LORA" in text  # the missing anchor itself is named in the gap statement
