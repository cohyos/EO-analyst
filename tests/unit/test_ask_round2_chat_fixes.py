"""Round 2 D5 chat fixes (docs/qa/loop/round_2_chat_fixes.md, round_1_judge.md).

Covers, per the task brief:
  1. the streaming repetition-loop guard (`eoa.api.routes.ask._repetition_detected`,
     `_truncate_at_sentence`) with synthetic loops, and an end-to-end SSE test that a looping
     stream is actually cut short;
  2. the wall-clock guard;
  3. the zero-citation corrective pass (mocked LLM);
  4. the topic-anchor (off-topic) guard;
  5. the per-source `report_kind` labels + canonical-entity injection in
     `eoa.api.services.ask_build_messages`.

Run with: ``PYTHONPATH=agent .venv\\Scripts\\python -m pytest tests/unit/test_ask_round2_chat_fixes.py -q``
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from eoa.api import services
from eoa.api.routes import ask as ask_route

# ---------------------------------------------------------------------------------------------
# 1. Repetition detector -- pure-function, synthetic loops
# ---------------------------------------------------------------------------------------------


class TestRepetitionDetected:
    def test_no_repetition_in_normal_prose(self) -> None:
        tail = "ניתוח מפורט של מגמות בתעשייה הביטחונית, עם דגש על מערכות אלקטרואופטיות מתקדמות."
        assert ask_route._repetition_detected(tail) is False

    def test_empty_tail_is_never_a_repetition(self) -> None:
        assert ask_route._repetition_detected("") is False

    def test_repeated_line_triggers(self) -> None:
        """The exact round-1 failure mode: a bullet line repeated verbatim."""
        line = "- פער טכנולוגי לא ידוע במפרטי המערכת"
        tail = "\n".join([line] * 3)
        assert ask_route._repetition_detected(tail) is True

    def test_two_repeats_of_a_line_do_not_trigger(self) -> None:
        line = "- פער טכנולוגי לא ידוע במפרטי המערכת"
        tail = "\n".join([line] * 2)
        assert ask_route._repetition_detected(tail) is False

    def test_repeated_40plus_char_window_triggers_even_without_newlines(self) -> None:
        pattern = "מודל-דיגום-חוזר-על-עצמו-שוב-ושוב-בלי-הפסקה "  # >= 40 chars
        assert len(pattern) >= 40
        tail = pattern * 3
        assert ask_route._repetition_detected(tail) is True

    def test_natural_short_word_repeated_a_few_times_does_not_trigger(self) -> None:
        """A word like 'מאוד' recurring naturally in short prose (no repeated full line, no
        repeated 40+ char window, and well under the 120-char floor the window check needs) must
        not be flagged -- only an actual multi-times-repeated *window* is a loop signal."""
        tail = "המחיר עלה מאוד השנה. גם הביקוש עלה מאוד. וגם התחרות גדלה מאוד באזור."
        assert len(tail) < 120
        assert ask_route._repetition_detected(tail) is False

    def test_a_short_unit_repeated_enough_to_fill_a_40_char_window_does_trigger(self) -> None:
        """Documents the flip side of the above: once a short repeating unit runs long enough
        that its own trailing 40-char window recurs 3x, it IS flagged -- by design this detector
        cares about the repeated *window*, not the period of whatever underlies it."""
        tail = "קצר " * 30  # period-4 unit, but 120 chars long -- the window check now applies
        assert ask_route._repetition_detected(tail) is True

    def test_growing_combinatorial_bullets_that_never_repeat_verbatim_do_not_trigger(self) -> None:
        """Not every degenerate pattern is caught (documented limitation) -- a bullet list where
        every line is *distinct* text must not false-positive."""
        lines = [f"- פער מספר {i}: מידע לא זמין על תת-מערכת {i}" for i in range(6)]
        tail = "\n".join(lines)
        assert ask_route._repetition_detected(tail) is False


class TestTruncateAtSentence:
    def test_cuts_trailing_partial_sentence(self) -> None:
        text = "המשפט הראשון הושלם. המשפט השני נקטע באמצע המ"
        assert ask_route._truncate_at_sentence(text) == "המשפט הראשון הושלם."

    def test_no_sentence_boundary_returns_right_trimmed_text(self) -> None:
        text = "  טקסט בלי סימן פיסוק כלל  "
        assert ask_route._truncate_at_sentence(text) == text.rstrip()

    def test_keeps_question_and_exclamation_marks(self) -> None:
        assert ask_route._truncate_at_sentence("שאלה כלשהי? המשך שנקטע") == "שאלה כלשהי?"


# ---------------------------------------------------------------------------------------------
# End-to-end SSE tests
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


class _FakeChatResult:
    def __init__(self, content: str = "") -> None:
        self.content = content


CITATIONS = [
    {"n": 1, "item_id": 101, "title": "מקור א", "url": "https://a.test", "level": "red", "source_name": "Globes"},
]


def _mock_ask(
    monkeypatch: pytest.MonkeyPatch,
    chunks: list[str],
    *,
    citations: list[dict] | None = None,
    repair_content: str = "",
    question: str = "מה קורה עם XM30?",
) -> None:
    from eoa.llm import ollama_client

    monkeypatch.setattr(services, "ask_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(
        services, "ask_build_messages", lambda *a, **k: ([], citations if citations is not None else CITATIONS)
    )
    monkeypatch.setattr(ollama_client, "resolve_provider_info", lambda provider: ("ollama", "resident"))
    monkeypatch.setattr(ollama_client, "chat_stream", lambda *a, **k: iter(chunks))
    monkeypatch.setattr(ollama_client, "chat", lambda *a, **k: _FakeChatResult(repair_content))
    return question


class TestRepetitionAbortEndToEnd:
    def test_looping_stream_is_cut_short_with_a_system_note(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        line = "- פער טכנולוגי לא ידוע [1]"
        # 20 repeats of the same bullet -- the real round-1 failure pattern; the guard must stop
        # well before all 20 are consumed.
        chunks = [f"{line}\n"] * 20
        question = _mock_ask(monkeypatch, chunks, question="שאלה על XM30")
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        token_text = "".join(e["text"] for e in events if e["type"] == "token")
        # far fewer than 20 repeats made it through
        assert token_text.count("פער טכנולוגי לא ידוע") < 10
        assert "לולאת חזרה" in token_text
        assert events[-1]["type"] == "done"
        assert any(e["type"] == "sources" for e in events)

    def test_wallclock_ceiling_cuts_a_slow_stream(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time as real_time

        def slow_chunks() -> Iterator[str]:
            yield "חלק ראשון של התשובה [1]. "
            real_time.sleep(0.25)  # real wall-clock delay -- exceeds the lowered ceiling below
            yield "חלק שני שלעולם לא אמור להגיע. "

        question = _mock_ask(monkeypatch, [], question="שאלה על XM30")
        from eoa.llm import ollama_client

        monkeypatch.setattr(ollama_client, "chat_stream", lambda *a, **k: slow_chunks())
        monkeypatch.setattr(ask_route, "_MAX_ANSWER_SECONDS", 0.05)

        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        token_text = "".join(e["text"] for e in events if e["type"] == "token")
        assert "חלק ראשון" in token_text
        assert "חלק שני" not in token_text
        assert "חריגה ממגבלת הזמן" in token_text


class TestCitationCorrectivePass:
    def test_uncited_answer_gets_repaired_and_replaced(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        question = _mock_ask(
            monkeypatch,
            ["תשובה עם עובדות אך בלי שום ציטוט."],
            repair_content="תשובה עם עובדות [1] ומקור מצוין.",
            question="שאלה על XM30",
        )
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        finals = [e for e in events if e["type"] == "answer_final"]
        assert finals, "expected a citation-repair answer_final event"
        assert "[1]" in finals[0]["text"]

    def test_repair_pass_failure_falls_back_to_visible_prefix(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from eoa.llm import ollama_client

        # mentions the question's anchor (XM30) so only the citation guard is under test here --
        # the topic-anchor guard would otherwise also fire and stack its own prefix on top.
        question = _mock_ask(
            monkeypatch, ["תשובה בלי ציטוט בכלל, לגבי XM30."], question="שאלה על XM30"
        )

        def raising_chat(*a: Any, **k: Any) -> Any:
            raise RuntimeError("ollama unreachable")

        monkeypatch.setattr(ollama_client, "chat", raising_chat)
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        finals = [e for e in events if e["type"] == "answer_final"]
        assert len(finals) == 1
        assert finals[-1]["text"].startswith(ask_route._NO_CITATION_PREFIX)

    def test_no_citations_available_skips_the_repair_pass_entirely(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No sources retrieved at all -- an uncited answer is expected and must not be flagged."""
        question = _mock_ask(
            monkeypatch, ["אין מידע רלוונטי במאגר."], citations=[], question="שאלה על XM30"
        )
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        assert not [e for e in events if e["type"] == "answer_final" and "ציטוט" in e["text"]]


class TestAnchorGuard:
    def test_answer_missing_every_question_anchor_gets_flagged(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # "XM30" is a rare alnum token -> a strong anchor; the answer never mentions it.
        question = _mock_ask(
            monkeypatch,
            ["תשובה [1] שמדברת על נושא אחר לגמרי ולא נוגעת בשאלה המקורית."],
            repair_content="",
            question="מה קורה עם XM30?",
        )
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        finals = [e for e in events if e["type"] == "answer_final"]
        assert any(e["text"].startswith(ask_route._OFF_TOPIC_PREFIX) for e in finals)

    def test_answer_that_mentions_the_anchor_is_not_flagged(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        question = _mock_ask(
            monkeypatch,
            ["מידע מפורט על XM30 [1] ומצב הפיתוח שלו."],
            question="מה קורה עם XM30?",
        )
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        finals = [e for e in events if e["type"] == "answer_final"]
        assert not any(e["text"].startswith(ask_route._OFF_TOPIC_PREFIX) for e in finals)


# ---------------------------------------------------------------------------------------------
# 5. Source-type labels + canonical-entity injection (eoa.api.services.ask_build_messages)
# ---------------------------------------------------------------------------------------------


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


class TestSourceKindLabels:
    def test_academic_source_is_labelled_as_academic_not_rfi(self) -> None:
        row = _row(1, report_kind="academic")
        messages, _ = services.ask_build_messages("שאלה", [], [row])
        user_msg = messages[-1]["content"]
        assert "מאמר אקדמי" in user_msg or "arXiv" in user_msg

    def test_tender_source_is_labelled_as_tender_not_academic(self) -> None:
        row = _row(2, report_kind="tender")
        messages, _ = services.ask_build_messages("שאלה", [], [row])
        user_msg = messages[-1]["content"]
        assert "מכרז" in user_msg or "RFI" in user_msg

    def test_report_kind_is_forwarded_on_the_citation(self) -> None:
        row = _row(3, report_kind="tender")
        _, citations = services.ask_build_messages("שאלה", [], [row])
        assert citations[0]["report_kind"] == "tender"

    def test_missing_report_kind_gets_an_unclassified_label_not_a_crash(self) -> None:
        row = _row(4, report_kind=None)
        messages, _ = services.ask_build_messages("שאלה", [], [row])
        assert "לא מסווג" in messages[-1]["content"]


class TestCanonicalEntityInjection:
    def test_entity_names_from_retrieved_items_appear_in_system_prompt(self) -> None:
        row = _row(5, entities_mentioned=["Iron Beam", "Rafael"])
        messages, _ = services.ask_build_messages("שאלה", [], [row])
        system = messages[0]["content"]
        assert "Iron Beam" in system
        assert "Rafael" in system

    def test_anti_conflation_rule_present_when_entities_exist(self) -> None:
        row = _row(6, entities_mentioned=["Iron Beam"])
        messages, _ = services.ask_build_messages("שאלה", [], [row])
        system = messages[0]["content"]
        assert "מגן אור" in system and "כיפת ברזל" in system

    def test_no_entity_block_added_when_no_items_retrieved(self) -> None:
        messages, _ = services.ask_build_messages("שאלה", [], [])
        system = messages[0]["content"]
        assert "מגן אור" not in system

    def test_entities_deduplicated_case_insensitively(self) -> None:
        row1 = _row(7, entities_mentioned=["Rafael"])
        row2 = _row(8, entities_mentioned=["rafael", "Elbit"])
        messages, _ = services.ask_build_messages("שאלה", [], [row1, row2])
        system = messages[0]["content"]
        assert system.count("Rafael") + system.count("rafael") == 1
        assert "Elbit" in system


class TestAskCitationRepairMessages:
    def test_appends_assistant_answer_and_repair_instruction(self) -> None:
        base_messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]
        out = services.ask_citation_repair_messages(base_messages, "התשובה המקורית")
        assert out[: len(base_messages)] == base_messages
        assert out[-2] == {"role": "assistant", "content": "התשובה המקורית"}
        assert out[-1]["role"] == "user"
        assert "[n]" in out[-1]["content"]

    def test_does_not_mutate_the_original_messages_list(self) -> None:
        base_messages = [{"role": "system", "content": "sys"}]
        original_len = len(base_messages)
        services.ask_citation_repair_messages(base_messages, "x")
        assert len(base_messages) == original_len
