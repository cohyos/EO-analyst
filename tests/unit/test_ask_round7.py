"""Round 7 (package R7-chat) chat-grounding fixes (docs/qa/loop/round_6_judge.md D5, score 45).

The round-6 judge's live 8-question sample found the fabrication class round 6 was built to close
recurring in a new shape, plus a distinct weak-citation-confidence failure, and asked for an
optional (chat-only) light-model entailment check on top of the deterministic floor:

1. **Golden Q1 (XM30) fabrication recurs:** the answer invented an "MWIR/SWIR/VIS EO" capability
   attributed to item 257, whose text contains none of it. Round-6 `filter_claim_grounding` let it
   through because a `[n]`-cited unit passed when *any* distinctive token was grounded (e.g. the
   unit also happened to name the genuinely grounded "XM30"). Tightened in place (same public
   entry point): a unit now survives only when *either* every "technical" token it carries is
   grounded in its own citation, *or* at least half of *all* its distinctive tokens are -- see
   `eoa.api.ask_grounding.filter_claim_grounding`'s own docstring for the full live-repro
   rationale and why the round-6 "one grounded token saves the bullet" test still passes unchanged
   (its scenario sits exactly at the 50% line).
2. **Golden Q5 (Skyranger) weak-citation confidence:** confident claims with almost no citations,
   then a footer admission that most retrieved sources are unrelated. Three new, additive
   functions: `filter_uncited_factual_claims` (drops an uncited factual claim sitting alongside a
   cited sibling in the same lead-paragraph/key-facts scope), `relocate_source_admission_caveat`
   (moves the model's own "N of M sources unrelated" admission to the top as the leading caveat),
   `low_citation_caveat` (a generic one-time caveat when fewer than 2 factual sentences in the
   whole answer end up actually cited).
3. **Optional light-model entailment check:** `entailment_filter` (new, config-gated via
   `ask.entailment_check`/`ask.entailment_max_claims`, chat-only) asks the `light` role one
   structured yes/no/partial question per up to N cited claims against their own cited source
   excerpt, with a hard 20s timeout and a silent no-op on any error -- see `AskCfg`'s own docstring
   in `agent/eoa/config.py` for why this package ships it **disabled by default**
   (`entailment_check: false`): every existing chat e2e test in this shared suite (rounds 2/3/5/6,
   none owned by this package) hits the same `if citations:` branch this check hangs off of, none
   of them mock `chat_structured`, and this package's own standing rule for this suite is "no
   network calls from tests."

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 .venv\\Scripts\\python -m pytest tests/unit/test_ask_round7.py -q``
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from eoa.api import ask_grounding, services
from eoa.config import settings


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
# 1. filter_claim_grounding -- round-7 tightening (live Q1/XM30 recurrence)
# ---------------------------------------------------------------------------------------------


class TestTightenedClaimGrounding:
    def test_live_repro_mwir_swir_vis_with_no_other_grounded_token_is_removed(self) -> None:
        """Live-verified shape (docs/qa/loop/round_6_judge.md D5 worst-list #1): item 257's real
        text is only about vehicle deliveries/program value -- none of MWIR/SWIR/VIS appear in it,
        and none of them are the question's own subject either, so every technical token in the
        unit is ungrounded (0% overall) -- removed under both (a) and (b)."""
        rows = [
            _src(
                257,
                "XM30 program deliveries",
                "GDLS delivered the first XM30 prototype vehicles to the US Army under a "
                "$1.53 billion contract.",
            )
        ]
        text = "### עובדות מרכזיות\n- הרכב כולל טכנולוגיית EO רב-מודלית: MWIR, SWIR ו-VIS לניווט [1]."
        new_text, removed = ask_grounding.filter_claim_grounding(
            text, "מה המצב העדכני של תוכנית ה-XM30?", rows
        )
        assert removed == 1
        assert "MWIR" not in new_text
        assert "SWIR" not in new_text

    def test_round6_one_grounded_token_case_still_survives_at_the_50_percent_line(self) -> None:
        """Regression guard: round 6's own "deliberately weak bar" test (a grounded XM30 alongside
        an ungrounded HEL, in the same 2-token unit) sits exactly at the round-7 50% threshold and
        must still be kept -- this is the scenario round 7's brief explicitly asked to be measured
        against before finalising the threshold."""
        rows = [_src(1, "XM30 program", "The XM30 program is progressing on schedule.")]
        text = "### עובדות מרכזיות\n- ה-XM30 כולל גם יכולת HEL חדשנית [1]."
        new_text, removed = ask_grounding.filter_claim_grounding(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_below_half_threshold_with_one_grounded_technical_token_is_now_removed(self) -> None:
        """The actual round-7 tightening beyond the 2-token case above: 1 of 3 technical tokens
        grounded (33%, below half) and not all technical tokens grounded either -- round 6's "any
        token grounded" bar would have kept this; round 7 removes it. Uses "ATR" (no embedded
        digits) rather than "XM30" for the grounded token, so the digit-run tokenizer does not
        also independently match the "30" substring and inflate the grounded count."""
        rows = [_src(1, "Some item", "Uses ATR only, nothing else technical.")]
        text = "### עובדות מרכזיות\n- המערכת כוללת ATR וגם MWIR ו-SWIR חדשניים [1]."
        new_text, removed = ask_grounding.filter_claim_grounding(text, "שאלה", rows)
        assert removed == 1
        assert "MWIR" not in new_text

    def test_all_technical_tokens_grounded_keeps_unit_even_below_half_overall(self) -> None:
        """The (a)-branch: a unit whose only technical token is genuinely grounded is kept even
        when several ordinary (non-technical) proper-noun words alongside it are not -- the
        module's existing precision-first trade-off (a real, correctly-cited technical fact
        saves the unit; round 7 only tightens what happens when a technical token is NOT
        grounded, never adds a new way to fail a unit whose technical claims are all sound)."""
        rows = [_src(1, "Item", "XM30 detail exists here, nothing else mentioned.")]
        text = "### עובדות מרכזיות\n- XM30 Program Team כולל את Textron Systems ו-Raytheon Group [1]."
        new_text, removed = ask_grounding.filter_claim_grounding(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_uncited_technical_token_ungrounded_anywhere_is_removed(self) -> None:
        """Round-7 new scope: an in-scope unit with *no* citation at all is now also checked --
        against the whole retrieved corpus, since there is no specific citation to hold it to."""
        rows = [_src(1, "Real topic", "מידע כללי בלבד שאינו קשור לנושא הטכני.")]
        text = "תשובה ישירה עם טכנולוגיית XPQR חדשנית ללא ציטוט כלל."
        new_text, removed = ask_grounding.filter_claim_grounding(text, "שאלה", rows)
        assert removed == 1
        assert "XPQR" not in new_text

    def test_uncited_technical_token_grounded_in_the_wider_corpus_is_kept(self) -> None:
        rows = [_src(1, "XPQR system", "This describes the real XPQR sensor package in detail.")]
        text = "תשובה ישירה שמזכירה טכנולוגיית XPQR ללא ציטוט מפורש."
        new_text, removed = ask_grounding.filter_claim_grounding(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_uncited_non_technical_word_is_left_alone(self) -> None:
        rows = [_src(1, "Topic", "לא קשור בכלל.")]
        text = "תשובה ישירה עם המילה Program ללא כל תוכן טכני."
        new_text, removed = ask_grounding.filter_claim_grounding(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_uncited_technical_token_outside_scope_is_left_alone(self) -> None:
        rows = [_src(1, "Topic", "לא קשור בכלל.")]
        text = "### פערים / מה לא ידוע\n- לא ידוע דבר על XPQR."
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
# 2. filter_uncited_factual_claims -- round-7 item 2, live Q5/Skyranger
# ---------------------------------------------------------------------------------------------


class TestFilterUncitedFactualClaims:
    def test_uncited_factual_bullet_removed_when_section_has_a_citation(self) -> None:
        rows = [_src(1, "Contract", "Rheinmetall Group signed a new deal.")]
        text = (
            "### עובדות מרכזיות\n"
            "- Rheinmetall חתמה חוזה חדש בסך 5 מיליון דולר [1].\n"
            "- Rheinmetall סיפקה גם מערכת נוספת לצבא."
        )
        new_text, removed = ask_grounding.filter_uncited_factual_claims(text, rows)
        assert removed == 1
        assert "סיפקה גם מערכת" not in new_text
        assert "5 מיליון דולר" in new_text

    def test_uncited_bullets_kept_when_whole_section_has_zero_citations(self) -> None:
        rows = [_src(1, "x", "y")]
        text = "### עובדות מרכזיות\n- Rheinmetall סיפקה מערכת חדשה.\n- החברה גם התרחבה לשווקים נוספים."
        new_text, removed = ask_grounding.filter_uncited_factual_claims(text, rows)
        assert removed == 0
        assert new_text == text

    def test_markdown_table_rows_are_never_touched(self) -> None:
        """Live-verified 2026-09-07 (offline replay against golden Q5/Skyranger's actual live
        answer): the model rendered its comparison as a markdown table, not a bullet list --
        `_iter_units`'s plain sentence-splitter chopped each row into several citation-less
        fragments, and this guard (unlike the module's more conservative checks) deleted most of
        the table. Any unit containing a literal `|` is now skipped outright."""
        rows = [_src(1, "x", "y")]
        text = (
            "### עובדות מרכזיות\n"
            "- Rheinmetall חתמה חוזה חדש [1].\n\n"
            "| שדה | Skyranger | David's Sling |\n"
            "|:---:|:---:|:---:|\n"
            "| סוג | הגנה ניידת [1]. תומך בשכבתיות. | הגנה קרקעית קבועה. |\n"
        )
        new_text, removed = ask_grounding.filter_uncited_factual_claims(text, rows)
        assert removed == 0
        assert new_text == text

    def test_non_factual_uncited_sentence_is_never_removed(self) -> None:
        rows = [_src(1, "x", "y")]
        text = "### עובדות מרכזיות\n- החברה חתמה חוזה חדש [1].\n- זהו נושא מעניין להמשך מעקב."
        new_text, removed = ask_grounding.filter_uncited_factual_claims(text, rows)
        assert removed == 0
        assert new_text == text

    def test_lead_paragraph_scope_uncited_factual_sentence_is_removed(self) -> None:
        rows = [_src(1, "x", "y")]
        text = (
            "החברה חתמה חוזה בסך 5 מיליון דולר [1]. Rheinmetall סיפקה גם מוצר נוסף.\n\n"
            "### עובדות מרכזיות\n- פרט נוסף [1]."
        )
        new_text, removed = ask_grounding.filter_uncited_factual_claims(text, rows)
        assert removed == 1
        assert "Rheinmetall סיפקה גם מוצר נוסף" not in new_text
        assert "5 מיליון דולר" in new_text

    def test_analyst_assessment_section_is_exempt(self) -> None:
        rows = [_src(1, "x", "y")]
        text = "### הערכת האנליסט\nRheinmetall סיפקה גם מוצר חדש ללא כל ציטוט."
        new_text, removed = ask_grounding.filter_uncited_factual_claims(text, rows)
        assert removed == 0
        assert new_text == text

    def test_no_op_on_empty_retrieved(self) -> None:
        text = "### עובדות מרכזיות\n- עובדה [1].\n- עובדה נוספת ללא ציטוט Rheinmetall."
        assert ask_grounding.filter_uncited_factual_claims(text, []) == (text, 0)

    def test_no_op_on_blank_answer(self) -> None:
        rows = [_src(1, "x", "y")]
        assert ask_grounding.filter_uncited_factual_claims("   ", rows) == ("   ", 0)


# ---------------------------------------------------------------------------------------------
# 3. relocate_source_admission_caveat -- round-7 item 2, live Q5 footer admission
# ---------------------------------------------------------------------------------------------


class TestRelocateSourceAdmissionCaveat:
    def test_admission_sentence_relocated_to_top(self) -> None:
        text = (
            "### עובדות מרכזיות\n- פרט ראשון [1].\n\nלמעשה, 6 מתוך 8 המקורות שאותרו אינם קשורים ישירות לנושא."
        )
        new_text, moved = ask_grounding.relocate_source_admission_caveat(text)
        assert moved is True
        assert new_text.startswith("> ⚠️")
        assert "אינם קשורים" in new_text.splitlines()[0]
        assert "פרט ראשון" in new_text

    def test_multiple_admission_sentences_are_joined_and_moved(self) -> None:
        text = "### עובדות מרכזיות\n- פרט [1].\n\nחלק מהמקורות לא רלוונטיים לנושא. מקור אחר אינו קשור כלל."
        new_text, moved = ask_grounding.relocate_source_admission_caveat(text)
        assert moved is True
        leading = new_text.splitlines()[0]
        assert "לא רלוונטיים" in leading
        assert "אינו קשור" in leading

    def test_no_admission_is_a_no_op(self) -> None:
        text = "### עובדות מרכזיות\n- פרט תקין לחלוטין [1]."
        assert ask_grounding.relocate_source_admission_caveat(text) == (text, False)

    def test_idempotent_on_already_relocated_text(self) -> None:
        text = "### עובדות מרכזיות\n- פרט ראשון [1].\n\nהמקורות אינם קשורים ישירות לנושא הנשאל."
        once, moved_once = ask_grounding.relocate_source_admission_caveat(text)
        assert moved_once is True
        twice, moved_twice = ask_grounding.relocate_source_admission_caveat(once)
        assert moved_twice is False
        assert twice == once

    def test_blank_text_is_a_no_op(self) -> None:
        assert ask_grounding.relocate_source_admission_caveat("   ") == ("   ", False)
        assert ask_grounding.relocate_source_admission_caveat("") == ("", False)


# ---------------------------------------------------------------------------------------------
# 4. low_citation_caveat -- round-7 item 2, generic fallback caveat
# ---------------------------------------------------------------------------------------------


class TestLowCitationCaveat:
    def test_caveat_added_when_fewer_than_two_cited_out_of_several_factual_sentences(self) -> None:
        """The live Q5/Skyranger shape: several confident factual claims, almost none cited.
        Requires >= 3 total factual sentences (see `_LOW_CITATION_MIN_FACTUAL_UNITS`) so a short,
        single-fact, correctly-cited answer is never flagged (see the next test's counter-case)."""
        text = "החברה חתמה חוזה חדש [1]. Rheinmetall סיפקה גם מוצר נוסף. חברה שלישית רכשה נכס משמעותי."
        new_text, added = ask_grounding.low_citation_caveat(text)
        assert added == 1
        assert new_text.startswith(ask_grounding._LOW_CITATION_CAVEAT)
        assert "החברה חתמה חוזה חדש [1]." in new_text

    def test_no_caveat_when_two_or_more_cited_factual_sentences(self) -> None:
        text = (
            "החברה חתמה חוזה חדש בסך 5 מיליון דולר [1]. Rheinmetall סיפקה גם מוצר נוסף [2]. "
            "חברה שלישית רכשה נכס משמעותי."
        )
        new_text, added = ask_grounding.low_citation_caveat(text)
        assert added == 0
        assert new_text == text

    def test_no_caveat_below_the_minimum_factual_units_gate(self) -> None:
        """A short, single-fact, correctly-cited answer -- e.g. one key-facts bullet -- must never
        be flagged just because it has fewer than 2 citations; the round-3/round-5 e2e fixtures
        this guards against regressing are exactly this shape."""
        text = "### עובדות מרכזיות\n- תוכנית ה-XM30 מבוססת על GDLS Lynx [1]."
        new_text, added = ask_grounding.low_citation_caveat(text)
        assert added == 0
        assert new_text == text

    def test_caveat_not_duplicated_when_already_present(self) -> None:
        text = ask_grounding._LOW_CITATION_CAVEAT + "\n\nתוכן קצר [1]."
        new_text, added = ask_grounding.low_citation_caveat(text)
        assert added == 0
        assert new_text == text

    def test_blank_text_is_a_no_op(self) -> None:
        assert ask_grounding.low_citation_caveat("   ") == ("   ", 0)
        assert ask_grounding.low_citation_caveat("") == ("", 0)


# ---------------------------------------------------------------------------------------------
# 5. entailment_filter -- round-7 item 3, mocked light-model call
# ---------------------------------------------------------------------------------------------


class TestEntailmentFilter:
    def test_claim_answered_no_is_removed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Some source text about a loosely related topic.")]
        text = "### עובדות מרכזיות\n- טענה ראשונה שגויה [1]."

        def _fake(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            return ask_grounding._EntailmentResponse(
                verdicts=[ask_grounding._ClaimVerdict(index=1, verdict="no")]
            )

        monkeypatch.setattr(ollama_client, "chat_structured", _fake)
        new_text, removed = ask_grounding.entailment_filter(text, rows, max_claims=6, timeout_s=5.0)
        assert removed == 1
        assert "טענה ראשונה שגויה" not in new_text

    def test_claims_answered_yes_or_partial_are_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Supporting source text.")]
        text = "### עובדות מרכזיות\n- טענה תקינה [1].\n- טענה חלקית [1]."

        def _fake(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            return ask_grounding._EntailmentResponse(
                verdicts=[
                    ask_grounding._ClaimVerdict(index=1, verdict="yes"),
                    ask_grounding._ClaimVerdict(index=2, verdict="partial"),
                ]
            )

        monkeypatch.setattr(ollama_client, "chat_structured", _fake)
        new_text, removed = ask_grounding.entailment_filter(text, rows, max_claims=6, timeout_s=5.0)
        assert removed == 0
        assert new_text == text

    def test_timeout_is_a_graceful_no_op(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import time

        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Source text.")]
        text = "### עובדות מרכזיות\n- טענה כלשהי [1]."

        def _slow(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            time.sleep(0.3)
            return ask_grounding._EntailmentResponse(
                verdicts=[ask_grounding._ClaimVerdict(index=1, verdict="no")]
            )

        monkeypatch.setattr(ollama_client, "chat_structured", _slow)
        new_text, removed = ask_grounding.entailment_filter(text, rows, max_claims=6, timeout_s=0.05)
        assert removed == 0
        assert new_text == text

    def test_exception_is_a_graceful_no_op(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Source text.")]
        text = "### עובדות מרכזיות\n- טענה כלשהי [1]."

        def _raise(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            raise RuntimeError("boom")

        monkeypatch.setattr(ollama_client, "chat_structured", _raise)
        new_text, removed = ask_grounding.entailment_filter(text, rows, max_claims=6, timeout_s=5.0)
        assert removed == 0
        assert new_text == text

    def test_no_candidates_skips_the_llm_call_entirely(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Source text.")]
        text = "### עובדות מרכזיות\n- משפט ללא ציטוט כלל."
        calls: list[int] = []

        def _fake(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            calls.append(1)
            raise AssertionError("chat_structured must not be called with no in-scope candidates")

        monkeypatch.setattr(ollama_client, "chat_structured", _fake)
        new_text, removed = ask_grounding.entailment_filter(text, rows, max_claims=6, timeout_s=5.0)
        assert removed == 0
        assert new_text == text
        assert calls == []

    def test_max_claims_caps_how_many_candidates_are_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Source text.")]
        bullets = "\n".join(f"- טענה מספר {i} [1]." for i in range(1, 9))
        text = f"### עובדות מרכזיות\n{bullets}"
        captured: dict[str, Any] = {}

        def _fake(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            captured["messages"] = messages
            return ask_grounding._EntailmentResponse(verdicts=[])

        monkeypatch.setattr(ollama_client, "chat_structured", _fake)
        ask_grounding.entailment_filter(text, rows, max_claims=3, timeout_s=5.0)
        user_content = captured["messages"][1]["content"]
        assert (
            user_content.count("טענה 1:") + user_content.count("טענה 2:") + user_content.count("טענה 3:") == 3
        )
        assert "טענה 4:" not in user_content

    def test_no_op_on_empty_retrieved(self) -> None:
        text = "### עובדות מרכזיות\n- טענה [1]."
        assert ask_grounding.entailment_filter(text, []) == (text, 0)

    def test_no_op_on_blank_answer(self) -> None:
        rows = [_src(1, "x", "y")]
        assert ask_grounding.entailment_filter("   ", rows) == ("   ", 0)


# ---------------------------------------------------------------------------------------------
# 6. AskCfg config defaults
# ---------------------------------------------------------------------------------------------


class TestAskConfig:
    def test_default_config_values(self) -> None:
        # The pydantic default is off; the shipped config turns it ON (user decision 2026-09-07),
        # and tests/conftest.py forces it off inside the test process so no LLM call ever runs here.
        from pathlib import Path

        import yaml

        from eoa.config import AskCfg

        assert AskCfg().entailment_check is False
        shipped = yaml.safe_load(Path("config/config.yaml").read_text(encoding="utf-8"))["ask"]
        assert shipped["entailment_check"] is True
        cfg = settings().ask
        assert cfg.entailment_check is False  # forced off under pytest
        assert cfg.entailment_max_claims == 6


# ---------------------------------------------------------------------------------------------
# 7. End-to-end SSE wiring
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
    from eoa.llm import ollama_client

    monkeypatch.setattr(services, "ask_retrieve", lambda *a, **k: rows)
    monkeypatch.setattr(ollama_client, "resolve_provider_info", lambda provider: ("ollama", "resident"))
    monkeypatch.setattr(ollama_client, "chat_stream", lambda *a, **k: iter(chunks))
    monkeypatch.setattr(ollama_client, "chat", lambda *a, **k: _FakeChatResult(repair_content))
    return question


class TestEndToEndWiring:
    def test_uncited_factual_claim_removed_end_to_end(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [
            _row(
                1,
                title="Rheinmetall deal",
                clean_text="Rheinmetall Group signed a new deal.",
                entities_mentioned=["Rheinmetall"],
            )
        ]
        chunk = (
            "### עובדות מרכזיות\n"
            "- Rheinmetall חתמה חוזה חדש [1].\n"
            "- Rheinmetall סיפקה גם מערכת חדשה בנוסף שלא צוינה במקור."
        )
        question = _mock_ask_full(monkeypatch, rows, [chunk], question="מה קורה עם Rheinmetall?")
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        finals = [e for e in events if e["type"] == "answer_final"]
        assert finals
        final = finals[-1]
        assert final["removed_by_guard"].get("uncited_factual_claim") == 1
        assert "סיפקה גם מערכת חדשה בנוסף" not in final["text"]
        assert "Rheinmetall חתמה חוזה חדש" in final["text"]
        assert events[-1]["type"] == "done"

    def test_admission_caveat_relocated_end_to_end(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [
            _row(
                1,
                title="Rheinmetall deal",
                clean_text="Rheinmetall Group signed a new deal.",
                entities_mentioned=["Rheinmetall"],
            )
        ]
        chunk = (
            "### עובדות מרכזיות\n"
            "- Rheinmetall חתמה חוזה חדש [1].\n\n"
            "### הערכת האנליסט\n"
            "למעשה, 6 מתוך 8 המקורות שאותרו אינם קשורים ישירות לנושא."
        )
        question = _mock_ask_full(monkeypatch, rows, [chunk], question="מה קורה עם Rheinmetall?")
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        finals = [e for e in events if e["type"] == "answer_final"]
        assert finals
        final_text = finals[-1]["text"]
        assert final_text.lstrip().startswith("> ⚠️")
        assert final_text.count("אינם קשורים") == 1
        assert "Rheinmetall חתמה חוזה חדש" in final_text

    def test_entailment_check_disabled_by_default_makes_no_chat_structured_call(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guards this package's own safety deviation (AskCfg.entailment_check defaults False):
        a full round-trip through the real route must never touch `chat_structured` unless a
        caller explicitly opts in."""
        from eoa.llm import ollama_client

        def _fail(*a: Any, **kw: Any) -> Any:
            raise AssertionError("chat_structured must not be called with entailment_check=False")

        monkeypatch.setattr(ollama_client, "chat_structured", _fail)
        rows = [
            _row(
                1,
                title="Rheinmetall deal",
                clean_text="Rheinmetall Group signed a new deal.",
                entities_mentioned=["Rheinmetall"],
            )
        ]
        chunk = "### עובדות מרכזיות\n- Rheinmetall חתמה חוזה חדש [1]."
        question = _mock_ask_full(monkeypatch, rows, [chunk], question="מה קורה עם Rheinmetall?")
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        assert events[-1]["type"] == "done"
        assert settings().ask.entailment_check is False
