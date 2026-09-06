"""Round 6 chat fixes (docs/qa/loop/round_5_judge.md D5, worst-list items 2/3, and D2's so_what
leak / D5's malformed-citation-marker finding). The round-5 judge scored chat 35/100 on five live
findings, all closed here:

1. **Fabricated "key facts" attributed to a real source (Q1/XM30):** a "HEL laser missile-
   interception system" / "ATR/GPS-denied computer-vision navigation" claim, cited to item 257,
   whose real text is only about vehicle deliveries -- no guard fired because "HEL" is a common
   defense acronym (`_COMMON_DEFENSE_ACRONYMS`) and "ATR"/"GPS" are each independently real
   *somewhere* in the wider retrieval corpus. Closed by `ask_grounding.filter_claim_grounding`
   (round-6 item 1): a per-bullet check, scoped to "עובדות מרכזיות"/the direct-answer paragraph,
   that grounds a claim's distinctive tokens against *that bullet's own cited source(s)*, with no
   allowlist exemption.
2. **The anchor-miss "demoted" fallback section leaks fabrications (Q2/Iron Beam):** a Rafael/
   SPECTRO/"AMPS NG" narrative survived inside the "### הקשר קרוב (לא התשובה)" section.
   Root-caused to `eoa.api.routes.ask`'s zero-citation corrective pass (`_run_citation_repair`):
   its rewritten `answer_text` previously became the new answer completely unguarded, then flowed
   straight into the anchor-miss demotion. Closed via `_run_removal_guards` (factored out, now
   re-applied after the repair rewrite *and* again on the fully-assembled demoted text).
3. **Template so_what phrase leaks into chat (D2/Q8):** closed by
   `ask_grounding.strip_template_phrases`, reusing (not copying) `eoa.report.qa_citations`'s
   `strip_so_what_phrases` and `eoa.report.style`'s `strip_filler_phrases`.
4. **Malformed citation markers `[9]]`/`[6]]` (Q5):** `sanitize_citation_markers` now also
   collapses a stray extra bracket glued onto an otherwise-valid `[n]`.
5. **A `###` heading glued to the preceding line (iPhone Safari e2e):** closed by the final,
   order-independent `ask_grounding.ensure_headings_on_own_line` pass.

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 .venv\\Scripts\\python -m pytest tests/unit/test_ask_round6_grounding.py -q``
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
# Shared fixtures
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


# ---------------------------------------------------------------------------------------------
# 1. filter_claim_grounding -- round-6 item 1, live Q1/XM30 repro
# ---------------------------------------------------------------------------------------------


class TestFilterClaimGrounding:
    def test_live_repro_hel_atr_gps_fabrication_is_removed(self) -> None:
        """Live-verified shape (docs/qa/loop/round_5_judge.md D5 worst-list #3): item 257's real
        text is only about vehicle deliveries/program value/supplier roster -- "HEL"/"ATR"/"GPS"
        never appear in it. None of the bullet's distinctive tokens are grounded in its own [1]
        citation, so the whole bullet is dropped."""
        rows = [
            _src(
                257,
                "XM30 program deliveries",
                "GDLS delivered the first XM30 prototype vehicles to the US Army under a "
                "$1.53 billion contract, with a supplier roster including several subcontractors.",
            )
        ]
        text = (
            "### עובדות מרכזיות\n"
            "- המערכת כוללת יכולת יירוט לייזר HEL וניווט ATR מבוסס GPS ללא תלות ב-GPS [1]."
        )
        new_text, removed = ask_grounding.filter_claim_grounding(
            text, "מה המצב העדכני של תוכנית ה-XM30?", rows
        )
        assert removed == 1
        assert "HEL" not in new_text
        assert "GPS" not in new_text

    def test_grounded_entity_check_alone_does_not_catch_the_same_fabrication(self) -> None:
        """Documents *why* round 6 was needed: the pre-existing corpus-wide guard waves this
        exact bullet through, since "HEL" is a common-acronym allowlist entry and "GPS"/"ATR" are
        each real somewhere in the corpus -- only the new per-citation check catches it."""
        rows = [
            _src(
                257,
                "XM30 program deliveries",
                "GDLS delivered the first XM30 prototype vehicles to the US Army.",
            ),
            _src(
                99,
                "Unrelated GPS/ATR overview",
                "Modern ATR systems increasingly rely on GPS-denied navigation research.",
            ),
        ]
        text = "### עובדות מרכזיות\n- המערכת כוללת ניווט ATR מבוסס GPS [1]."
        _, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0  # the pre-existing guard alone does not catch it

    def test_one_grounded_token_saves_the_whole_bullet(self) -> None:
        """Deliberately weak bar (module docstring): a bullet with at least one real, correctly-
        cited token is kept even though it also carries a fabricated one -- XM30 grounds this
        bullet even though "HEL" does not."""
        rows = [_src(1, "XM30 program", "The XM30 program is progressing on schedule.")]
        text = "### עובדות מרכזיות\n- ה-XM30 כולל גם יכולת HEL חדשנית [1]."
        new_text, removed = ask_grounding.filter_claim_grounding(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_direct_answer_paragraph_is_checked_too(self) -> None:
        rows = [_src(1, "Real topic", "מידע כללי בלבד ללא כל קשר לפריט המצוטט.")]
        text = "תשובה ישירה עם עובדה מומצאת לחלוטין בשם Zorblatt [1]."
        new_text, removed = ask_grounding.filter_claim_grounding(text, "שאלה", rows)
        assert removed == 1
        assert "Zorblatt" not in new_text

    def test_uncited_unit_is_left_alone(self) -> None:
        rows = [_src(1, "Real topic", "מידע לא קשור.")]
        text = "### עובדות מרכזיות\n- עובדה כלשהי ללא כל ציטוט בסוף."
        new_text, removed = ask_grounding.filter_claim_grounding(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_gaps_section_is_out_of_scope(self) -> None:
        rows = [_src(1, "Real topic", "מידע לא קשור בכלל.")]
        text = "### פערים / מה לא ידוע\n- לא ידוע דבר על Zorblatt [1]."
        new_text, removed = ask_grounding.filter_claim_grounding(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_analyst_assessment_section_is_out_of_scope(self) -> None:
        rows = [_src(1, "Real topic", "מידע לא קשור בכלל.")]
        text = "### הערכת האנליסט\nלדעתי מדובר במגמה חשובה הקשורה ל-Zorblatt."
        new_text, removed = ask_grounding.filter_claim_grounding(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_question_terms_ground_a_token(self) -> None:
        rows = [_src(1, "Unrelated", "טקסט לא קשור.")]
        text = "### עובדות מרכזיות\n- הפרויקט הידוע כ-Zorblatt נמצא בשלב מתקדם [1]."
        new_text, removed = ask_grounding.filter_claim_grounding(text, "מה קורה עם פרויקט Zorblatt?", rows)
        assert removed == 0
        assert new_text == text

    def test_digit_token_grounded_via_cited_source(self) -> None:
        rows = [_src(1, "Contract value", "The contract was signed in 2024 for a fixed term.")]
        text = "### עובדות מרכזיות\n- החוזה נחתם בשנת 2024 [1]."
        new_text, removed = ask_grounding.filter_claim_grounding(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_no_op_on_empty_retrieved(self) -> None:
        text = "### עובדות מרכזיות\n- עובדה כלשהי [1]."
        assert ask_grounding.filter_claim_grounding(text, "שאלה", []) == (text, 0)

    def test_no_op_on_blank_answer(self) -> None:
        rows = [_src(1, "x", "y")]
        assert ask_grounding.filter_claim_grounding("   ", "שאלה", rows) == ("   ", 0)


# ---------------------------------------------------------------------------------------------
# 2. sanitize_citation_markers -- round-6 item 4, malformed real citation markers (live Q5)
# ---------------------------------------------------------------------------------------------


class TestMalformedCitationMarkers:
    def test_double_trailing_bracket_collapsed(self) -> None:
        cleaned, count = ask_grounding.sanitize_citation_markers("עובדה כלשהי [9]] בהמשך.")
        assert "[9]]" not in cleaned
        assert "[9]" in cleaned
        assert count == 1

    def test_double_leading_bracket_collapsed(self) -> None:
        cleaned, count = ask_grounding.sanitize_citation_markers("עובדה כלשהי [[6] בהמשך.")
        assert "[[6]" not in cleaned
        assert "[6]" in cleaned
        assert count == 1

    def test_triple_trailing_bracket_collapsed(self) -> None:
        cleaned, count = ask_grounding.sanitize_citation_markers("עובדה [9]]] בהמשך.")
        assert cleaned.count("]") == 1
        assert "[9]" in cleaned
        assert count == 1

    def test_adjacent_valid_citations_are_never_merged_or_touched(self) -> None:
        text = "עובדה ראשונה [1][2] ועובדה שנייה."
        assert ask_grounding.sanitize_citation_markers(text) == (text, 0)

    def test_well_formed_single_citation_is_still_a_true_no_op(self) -> None:
        """Regression guard: the new malformed-bracket regex also matches an already-well-formed
        `[n]` (it must, to detect the malformed case) -- it must not inflate the count or rewrite
        anything when there is nothing to fix."""
        text = "תשובה תקינה עם ציטוט [1] אמיתי, ועוד [23] אחד."
        assert ask_grounding.sanitize_citation_markers(text) == (text, 0)

    def test_malformed_marker_and_template_leak_together_both_counted(self) -> None:
        cleaned, count = ask_grounding.sanitize_citation_markers("א [n] ב [9]] ג [1] תקין.")
        assert count == 2
        assert "[n]" not in cleaned
        assert "[9]]" not in cleaned
        assert "[1]" in cleaned


# ---------------------------------------------------------------------------------------------
# 3. strip_template_phrases -- round-6 item 3, so_what/filler leak (live D2/Q8)
# ---------------------------------------------------------------------------------------------


class TestStripTemplatePhrases:
    def test_so_what_only_bullet_is_dropped_outright(self) -> None:
        text = "### פערים\n- מהווה צעד משמעותי."
        new_text, count = ask_grounding.strip_template_phrases(text)
        assert count == 1
        assert "מהווה צעד משמעותי" not in new_text
        assert "- מהווה" not in new_text

    def test_so_what_phrase_with_other_content_keeps_the_rest(self) -> None:
        text = "### הערכת האנליסט\nהמהלך מחזק את מעמדה בשוק המקומי."
        new_text, count = ask_grounding.strip_template_phrases(text)
        assert count == 1
        assert "מחזק את מעמדה" not in new_text
        assert "בשוק המקומי" in new_text

    def test_filler_phrase_deleted_but_sentence_content_survives(self) -> None:
        text = "יש לציין כי החברה הודיעה על חוזה חדש [1]."
        new_text, count = ask_grounding.strip_template_phrases(text)
        assert count == 1
        assert "יש לציין" not in new_text
        assert "החברה הודיעה על חוזה חדש" in new_text
        assert "[1]" in new_text

    def test_no_banned_phrase_is_a_true_no_op(self) -> None:
        text = "### עובדות מרכזיות\n- עובדה תקינה לחלוטין [1]."
        assert ask_grounding.strip_template_phrases(text) == (text, 0)

    def test_multiple_phrases_across_units_are_all_counted(self) -> None:
        text = "המערכת חשובה. יש לציין כי היא מחזקת את מעמדה בשוק. עוד משפט תקין [1]."
        new_text, count = ask_grounding.strip_template_phrases(text)
        assert count == 2
        assert "יש לציין" not in new_text
        assert "מחזקת את מעמדה" not in new_text
        # the previous and following sentences must not get glued together
        assert "חשובה. היא בשוק." in new_text or "חשובה.היא" not in new_text
        assert "עוד משפט תקין [1]." in new_text

    def test_no_op_on_blank_answer(self) -> None:
        assert ask_grounding.strip_template_phrases("") == ("", 0)
        assert ask_grounding.strip_template_phrases("   ") == ("   ", 0)


# ---------------------------------------------------------------------------------------------
# 4. ensure_headings_on_own_line -- round-6 item 5, live iPhone Safari e2e finding
# ---------------------------------------------------------------------------------------------


class TestEnsureHeadingsOwnLine:
    def test_glued_heading_is_split_onto_its_own_line(self) -> None:
        text = "תשובה כלשהי.### עובדות מרכזיות\n- פרט [1]."
        fixed = ask_grounding.ensure_headings_on_own_line(text)
        assert "תשובה כלשהי." in fixed.splitlines()
        assert any(line.startswith("### עובדות מרכזיות") for line in fixed.splitlines())

    def test_heading_already_on_its_own_line_is_untouched(self) -> None:
        text = "תשובה כלשהי.\n\n### עובדות מרכזיות\n- פרט [1]."
        assert ask_grounding.ensure_headings_on_own_line(text) == text

    def test_no_hash_character_is_a_fast_no_op(self) -> None:
        text = "תשובה רגילה לגמרי ללא כותרות כלשהן."
        assert ask_grounding.ensure_headings_on_own_line(text) == text

    def test_single_hash_is_not_treated_as_a_heading(self) -> None:
        """This project's format only ever uses `###` -- a single stray `#` (e.g. inside ordinary
        prose) must not be split into its own line."""
        text = "המחיר # יחידה נותר ללא שינוי."
        assert ask_grounding.ensure_headings_on_own_line(text) == text

    def test_empty_text_is_a_no_op(self) -> None:
        assert ask_grounding.ensure_headings_on_own_line("") == ""


# ---------------------------------------------------------------------------------------------
# 5. End-to-end SSE wiring -- round-6 item 2, live Q2/Iron Beam demoted-section leak
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


class _FakeChatResult:
    def __init__(self, content: str = "") -> None:
        self.content = content


def _mock_ask_full(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    chunks: list[str],
    *,
    question: str,
    repair_content: str = "",
) -> str:
    """Mocks retrieval with *real* rows (unlike round 2's `_mock_ask`, which always returns `[]`)
    so the grounding guards -- gated on a non-empty `retrieved` -- actually run, plus the
    citation-repair pass (`ollama_client.chat`) with a caller-supplied rewrite."""
    from eoa.llm import ollama_client

    monkeypatch.setattr(services, "ask_retrieve", lambda *a, **k: rows)
    monkeypatch.setattr(ollama_client, "resolve_provider_info", lambda provider: ("ollama", "resident"))
    monkeypatch.setattr(ollama_client, "chat_stream", lambda *a, **k: iter(chunks))
    monkeypatch.setattr(ollama_client, "chat", lambda *a, **k: _FakeChatResult(repair_content))
    return question


class TestAnchorMissDemotedSectionEndToEnd:
    def test_citation_repair_rewrite_is_reguarded_before_reaching_the_demoted_section(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reproduces the live Q2/Iron Beam finding (docs/qa/loop/round_5_judge.md D5 #2): the
        question asks about a Rafael Iron Beam contract; retrieval only returns an unrelated Elbit
        item (so the anchor-miss guard fires -- "Iron Beam" never appears anywhere). The model's
        *first* answer has no `[n]` at all (triggering the citation-repair pass); the repair pass's
        rewrite attaches `[1]` to the same fabricated sentence, citing the Elbit-only source. Before
        round 6, `_run_citation_repair`'s rewrite was adopted as the new `answer_text` completely
        unguarded, so the fabricated "AMPSNG" jargon (grounded nowhere) survived, unexamined, all
        the way into the demoted "### הקשר קרוב (לא התשובה)" section. It must not survive now."""
        rows = [
            _row(
                93,
                title="Elbit SPECTRO ISR contract",
                clean_text="Elbit signed a $270 million contract for its SPECTRO ISR system.",
                entities_mentioned=["Elbit"],
            )
        ]
        fabricated = "רפאל זכתה בחוזה חדש בתחום מגן אור, הכולל את מערכת ה-AMPSNG המתקדמת בהיקף ניכר."
        question = _mock_ask_full(
            monkeypatch,
            rows,
            [fabricated],
            question="מהם פרטי חוזה מגן אור (Iron Beam) העדכני ביותר של רפאל?",
            repair_content=fabricated + " [1].",
        )
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        finals = [e for e in events if e["type"] == "answer_final"]
        assert finals, "expected at least one answer_final event"
        final_text = finals[-1]["text"]
        assert final_text.startswith(ask_route._OFF_TOPIC_PREFIX)
        assert "AMPSNG" not in final_text
        assert "### הקשר קרוב" in final_text  # the anchor-miss guard did demote it
        assert events[-1]["type"] == "done"

    def test_demoted_section_conflation_is_scrubbed_even_when_the_original_answer_already_cited(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same live shape, but the fabrication already carries `[1]` from the very first
        generation (no citation-repair pass involved) -- belt-and-suspenders: the guards must have
        already caught this on the first pass, and the second pass over the demoted text must not
        reintroduce or resurrect it either."""
        rows = [
            _row(
                93,
                title="Elbit SPECTRO ISR contract",
                clean_text="Elbit signed a $270 million contract for its SPECTRO ISR system.",
                entities_mentioned=["Elbit"],
            )
        ]
        text = "### עובדות מרכזיות\n- רפאל זכתה בחוזה חדש בתחום מגן אור, הכולל את מערכת ה-AMPSNG המתקדמת [1]."
        question = _mock_ask_full(
            monkeypatch,
            rows,
            [text],
            question="מהם פרטי חוזה מגן אור (Iron Beam) העדכני ביותר של רפאל?",
        )
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        finals = [e for e in events if e["type"] == "answer_final"]
        assert finals
        final_text = finals[-1]["text"]
        assert "AMPSNG" not in final_text
        assert "### הקשר קרוב" in final_text

    def test_clean_on_topic_answer_is_never_demoted_or_altered(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [
            _row(
                1,
                title="Iron Beam contract update",
                clean_text="Rafael's Iron Beam laser air-defense system entered a new production phase.",
                entities_mentioned=["Rafael"],
            )
        ]
        question = _mock_ask_full(
            monkeypatch,
            rows,
            ["### עובדות מרכזיות\n- מגן אור (Iron Beam) של רפאל נכנס לשלב ייצור חדש [1]."],
            question="מהם פרטי חוזה מגן אור (Iron Beam) העדכני ביותר של רפאל?",
        )
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        assert not any(
            e["type"] == "answer_final" and e["text"].startswith(ask_route._OFF_TOPIC_PREFIX) for e in events
        )
        assert events[-1]["type"] == "done"
