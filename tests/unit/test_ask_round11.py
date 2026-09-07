"""Round 11 (package R11-chat, docs/qa/loop/round_10_judge.md, D5 score 85; worst-list #2, #3, #4).

Three real D5 defects still live after round 10:

1. **Q5 (Skyranger) item 1353's spec retrieved but never synthesized (worst #2):** item 1353 is
   genuinely retrieved (round 10's own cap-widening fix worked -- it is cited as `[n]`), but its own
   "Skyranger" mention and rate-of-fire spec sit at characters 13493/7812 of a 15223-char
   `clean_text` -- entirely outside the naive `text[:4000]` prefix `ask_build_messages` sent the
   model for a retrieved (non-context) item. The model never fabricated or mis-cited anything -- it
   correctly reported that the slice of source [n] it actually saw said nothing about Skyranger,
   because that slice genuinely didn't. Fixed by `eoa.api.services._relevant_excerpt`: instead of
   always keeping only a document's head, the excerpt now also keeps a window around any match of
   the question's own rare/salient tokens (`_rare_tokens`), wherever in the document they fall.
2. **Q6 (AUSA 2026) referentially dangling opening recurs, 1/8 (worst #3):** a structurally
   complete, correctly-punctuated opening unit (`" שאר המקורות (...)"`) that opens with a cross-
   reference/continuation token whose antecedent was a preceding sentence some earlier guard
   removed. `enforce_answer_coherence` gains a second, independent leading-unit check for exactly
   this shape (`_CROSS_REF_OPENING_TOKENS`) alongside its existing two.
3. **Entailment coverage only 3/8 (worst #4):** `entailment_filter` gains a smaller probe payload
   (excerpt chars 1500 -> 800), an elapsed-aware chain-attempt timeout (60s instead of 40s when the
   main answer itself already came back in < 90s, via the same wall clock `routes.ask`'s own
   `_MAX_ANSWER_SECONDS` check uses), and a third, explicitly-paid resident-chain (Claude) attempt
   for when the light role's own configured chain (`agy` flash-tier -> `ollama`, no Claude entry at
   all) is unavailable outright rather than merely slow.

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 .venv\\Scripts\\python -m pytest tests/unit/test_ask_round11.py -q``
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from eoa.api import ask_grounding, services


@pytest.fixture(autouse=True)
def _reset_entailment_unavailable_flag() -> Iterator[None]:
    """`_ENTAILMENT_UNAVAILABLE_LOGGED` is process-lifetime, module-level state by design (round
    10's own once-per-process contract, `tests/unit/test_ask_round10.py`'s own fixture of the same
    name) -- reset it around every test in this module so one test's trigger cannot silently
    suppress another's expected log line."""
    ask_grounding._ENTAILMENT_UNAVAILABLE_LOGGED = False
    yield


def _src(id: int, title: str, text: str, **kw: Any) -> dict[str, Any]:
    base = {
        "id": id,
        "title": title,
        "url": f"https://example.test/{id}",
        "clean_text": text,
        "summary_he": "",
        "key_facts": [],
        "level": "yellow",
        "source_name": "מקור",
        "report_kind": None,
        "entities_mentioned": [],
        "_is_context": False,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------------------------
# 1. `_relevant_excerpt` -- finding 1 root cause (Skyranger item 1353)
# ---------------------------------------------------------------------------------------------


class TestRelevantExcerpt:
    def test_empty_text_returns_empty(self) -> None:
        assert services._relevant_excerpt("", ["Skyranger"], 4000) == ""

    def test_no_anchors_falls_back_to_head_prefix_previous_behavior(self) -> None:
        """With no anchors at all, behavior must stay byte-for-byte the old `text[:budget]`
        prefix -- every anchor-less document (the common case) is completely unaffected."""
        text = "א" * 6000
        assert services._relevant_excerpt(text, [], 4000) == text[:4000]

    def test_anchor_never_matching_falls_back_to_head_prefix(self) -> None:
        text = "תוכן כללי ללא שום מונח רלוונטי. " * 200
        out = services._relevant_excerpt(text, ["Skyranger"], 4000)
        assert out == text[:4000]

    def test_late_anchor_match_outside_a_naive_prefix_is_included(self) -> None:
        """The exact Q5/item-1353 repro shape: a long document whose one relevant mention sits
        well past a naive `text[:4000]` prefix cut."""
        head = "U.S. Air Force Seeks Anti-Aircraft Guns To Protect Its Overseas Bases. " * 60
        assert len(head) > 4000
        tail = "The Skyranger-35 system offers a rate of fire of 1000 rounds per minute in tests."
        text = head + tail
        out = services._relevant_excerpt(text, ["Skyranger"], 4000)
        assert "Skyranger-35" in out
        assert "rate of fire" in out

    def test_head_window_is_still_kept_alongside_a_late_anchor_match(self) -> None:
        """The excerpt should not discard the document's own opening context just because an
        anchor match forced a second window in -- both should be present."""
        head = "Program overview and funding background. " * 60
        tail = "Rheinmetall confirmed the Skyranger order this quarter."
        text = head + tail
        out = services._relevant_excerpt(text, ["Rheinmetall"], 4000)
        assert "Program overview" in out
        assert "Rheinmetall confirmed" in out

    def test_multiple_far_apart_anchor_matches_are_both_included(self) -> None:
        pad = "filler text " * 500
        text = f"Skyranger appears here. {pad} C-UAS appears much later here too."
        out = services._relevant_excerpt(text, ["Skyranger", "C-UAS"], 4000)
        assert "Skyranger appears here" in out
        assert "C-UAS appears much later" in out

    def test_output_never_exceeds_the_budget(self) -> None:
        head = "x " * 5000
        tail = "Skyranger " * 2000
        text = head + tail
        out = services._relevant_excerpt(text, ["Skyranger"], 4000)
        assert len(out) <= 4000

    def test_a_rare_late_anchor_is_not_starved_by_a_common_early_one(self) -> None:
        """Regression for the exact live item-1353 shape: a generic, frequently-occurring anchor
        ("UAS", matched early and often) must never crowd a specific, sparsely-occurring one
        ("Rheinmetall"/"Skyranger", matched only once, late in the document) out of the budget --
        a naive document-order fill spends the whole budget on the early, generic matches before
        ever reaching the specific pair that actually matters."""
        early_common = "UAS activity increases near the border. " * 40
        assert len(early_common) > 1200  # past the head window on its own
        filler = "Routine patrol logs continue for several more paragraphs of padding text. " * 40
        late_rare = "Rheinmetall unveiled its Skyranger system at the trade show today."
        text = early_common + filler + late_rare
        out = services._relevant_excerpt(text, ["UAS", "Rheinmetall", "Skyranger"], 2000)
        assert "Rheinmetall unveiled its Skyranger" in out


class TestAskBuildMessagesSurfacesBuriedSpec:
    def test_retrieved_item_body_carries_the_buried_anchor_match(self) -> None:
        """Integration-level regression for the live Q5/Skyranger repro: item 1353's own
        "Skyranger" mention, well past a naive 4000-char prefix, must now reach the model's own
        context block for a retrieved (non-context) item."""
        head = "U.S. Air Force Seeks Anti-Aircraft Guns To Protect Its Overseas Bases. " * 70
        assert len(head) > 4000
        tail = "The Skyranger-35 cannon offers a rate of fire of 1000 rounds per minute."
        row = _src(1353, "U.S. Air Force Seeks Anti-Aircraft Guns", head + tail)

        messages, citations = services.ask_build_messages(
            'כיצד משתווה ה-Skyranger של Rheinmetall למערכות נגד כטב"ם (C-UAS) ישראליות מקבילות?',
            [],
            [row],
        )
        user_msg = messages[-1]["content"]
        assert "Skyranger-35" in user_msg
        assert "rate of fire" in user_msg
        assert citations[0]["item_id"] == 1353

    def test_context_item_body_also_uses_anchor_windowing(self) -> None:
        head = "Background material with no relevant terms at all. " * 60
        assert len(head) > 1500
        tail = "Skyranger technical specification appears only here."
        row = _src(2, "Some item", head + tail, _is_context=True)

        messages, _ = services.ask_build_messages("מה ידוע על Skyranger?", [], [row])
        user_msg = messages[-1]["content"]
        assert "Skyranger technical specification" in user_msg

    def test_anchor_less_question_keeps_previous_prefix_behavior(self) -> None:
        """A short, ordinary question with nothing rare in it must not change any existing item's
        rendered body -- this fix is additive only."""
        row = _src(9, "פריט רגיל", "טקסט רגיל וקצר")
        messages, _ = services.ask_build_messages("שאלה", [], [row])
        assert "טקסט רגיל וקצר" in messages[-1]["content"]


# ---------------------------------------------------------------------------------------------
# 2. `enforce_answer_coherence` cross-reference-opening extension -- finding 2 (Q6)
# ---------------------------------------------------------------------------------------------


class TestCrossReferenceOpeningFragment:
    def test_short_cross_ref_opening_is_dropped_entirely(self) -> None:
        """The literal live Q6 shape: a short fragment opening with a bare cross-reference token
        and (almost) nothing else -- too short to stand as its own opening even once the token is
        stripped, so the whole unit is removed exactly like the existing dangling-fragment case."""
        text = "שאר המקורות (...) \n\n### עובדות מרכזיות\n- עובדה תקינה עם ציטוט מלא [1]."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1
        assert "שאר המקורות" not in new_text
        assert "עובדה תקינה" in new_text

    def test_long_cross_ref_opening_is_stripped_in_place_not_removed(self) -> None:
        """A long, otherwise-grammatical opening loses only its dangling reference token -- the
        rest of the sentence, which reads fine on its own, is kept."""
        text = (
            "שאר המקורות עוסקים בנושאים אחרים לגמרי ואינם רלוונטיים לשאלה שנשאלה [2].\n\n"
            "### עובדות מרכזיות\n- עובדה תקינה עם ציטוט מלא [1]."
        )
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1
        assert not new_text.lstrip().startswith("שאר")
        assert "המקורות עוסקים בנושאים אחרים" in new_text

    def test_however_english_token_is_stripped_when_remainder_is_long_enough(self) -> None:
        text = (
            "However, the program received significant additional funding for expanded testing "
            "this year [1].\n\n### עובדות מרכזיות\n- עובדה שנייה עם ציטוט [1]."
        )
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1
        assert not new_text.lstrip().lower().startswith("however")
        assert "the program received" in new_text

    def test_the_other_english_token_is_stripped_when_remainder_is_long_enough(self) -> None:
        text = (
            "The other sources describe a different, unrelated program entirely without further "
            "detail here [3].\n\n### עובדות מרכזיות\n- עובדה [1]."
        )
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1
        assert not new_text.lstrip().lower().startswith("the other")
        assert "sources describe a different" in new_text

    def test_cross_ref_token_mid_sentence_is_never_flagged(self) -> None:
        """Only the very start of a section's own leading unit is checked -- a cross-reference
        word appearing mid-sentence is completely normal Hebrew prose."""
        text = (
            "התוכנית אושרה השבוע במלואה, ולעומת זאת התקציב נותר ללא שינוי [1].\n\n"
            "### עובדות מרכזיות\n- עובדה [1]."
        )
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 0
        assert new_text == text

    def test_bullet_starting_with_a_cross_ref_token_is_never_flagged(self) -> None:
        text = "### עובדות מרכזיות\n- גם זה נכון לגמרי ומצוטט כראוי [1].\n- עובדה נוספת [1]."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 0
        assert new_text == text

    def test_second_section_heading_cross_ref_opening_is_also_handled(self) -> None:
        """The check applies to every section's own leading unit, not just the unheaded lead."""
        text = (
            "תשובה תקינה עם ציטוט מלא כאן [1].\n\n"
            "### הערכת האנליסט\nלכן המצב דורש מעקב הדוק בהמשך התקופה הקרובה מאוד [1]."
        )
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1
        assert not new_text.split("### הערכת האנליסט")[1].lstrip().startswith("לכן")

    def test_remainder_at_or_above_the_word_threshold_is_kept(self) -> None:
        remainder = "שיפור זה נחשב משמעותי מאוד עבור התוכנית [1]."
        assert ask_grounding._word_count(remainder) == 8
        text = f"אך {remainder}\n\n### עובדות מרכזיות\n- עובדה [1]."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1
        assert "שיפור זה נחשב" in new_text
        assert not new_text.lstrip().startswith("אך")

    def test_remainder_below_the_word_threshold_is_removed_entirely(self) -> None:
        remainder = "שיפור זה נחשב משמעותי מאוד התוכנית [1]."
        assert ask_grounding._word_count(remainder) == 7
        text = f"אך {remainder}\n\n### עובדות מרכזיות\n- עובדה תקינה נוספת עם ציטוט [1]."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1
        assert "שיפור זה נחשב" not in new_text
        assert "עובדה תקינה נוספת" in new_text

    def test_opening_without_any_cross_ref_token_is_completely_unaffected(self) -> None:
        text = "התוכנית אושרה השבוע במלואה [1].\n\n### עובדות מרכזיות\n- עובדה [1]."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 0
        assert new_text == text


# ---------------------------------------------------------------------------------------------
# 3. `entailment_filter` round-11 changes -- finding 3 (coverage 3/8)
# ---------------------------------------------------------------------------------------------


class TestEntailmentSmallerPayload:
    def test_excerpt_char_budget_is_reduced_to_800(self) -> None:
        assert ask_grounding._ENTAILMENT_SOURCE_EXCERPT_CHARS == 800

    def test_candidate_excerpt_is_trimmed_to_the_new_budget(self) -> None:
        rows = [_src(1, "Item", "מ" * 5000)]
        text = "### עובדות מרכזיות\n- טענה תקינה [1]."
        sources_by_n = ask_grounding._sources_by_n(rows)
        candidates = ask_grounding._entailment_scope_candidates(text, sources_by_n, 6)
        assert len(candidates[0][2]) <= 800


class TestEntailmentElapsedAwareChainTimeout:
    def test_fast_chain_timeout_default_is_60_seconds(self) -> None:
        import inspect

        sig = inspect.signature(ask_grounding.entailment_filter)
        assert sig.parameters["fast_chain_timeout_s"].default == 60.0

    def test_elapsed_under_90s_uses_the_fast_chain_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [_src(1, "Item", "Supporting source text.")]
        text = "### עובדות מרכזיות\n- טענה תקינה [1]."
        captured_timeouts: list[float] = []

        def _fake_run_with_timeout(fn: Any, timeout_s: float) -> tuple[Any, str | None]:
            captured_timeouts.append(timeout_s)
            if len(captured_timeouts) == 1:
                return None, "ConnectionError"
            return ask_grounding._EntailmentResponse(verdicts=[]), None

        monkeypatch.setattr(ask_grounding, "_run_with_timeout", _fake_run_with_timeout)
        ask_grounding.entailment_filter(
            text, rows, max_claims=4, chain_fallback=True, chain_timeout_s=40.0, answer_elapsed_s=10.0
        )
        assert captured_timeouts == [30.0, 60.0]

    def test_elapsed_over_90s_uses_the_default_chain_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [_src(1, "Item", "Supporting source text.")]
        text = "### עובדות מרכזיות\n- טענה תקינה [1]."
        captured_timeouts: list[float] = []

        def _fake_run_with_timeout(fn: Any, timeout_s: float) -> tuple[Any, str | None]:
            captured_timeouts.append(timeout_s)
            if len(captured_timeouts) == 1:
                return None, "ConnectionError"
            return ask_grounding._EntailmentResponse(verdicts=[]), None

        monkeypatch.setattr(ask_grounding, "_run_with_timeout", _fake_run_with_timeout)
        ask_grounding.entailment_filter(
            text, rows, max_claims=4, chain_fallback=True, chain_timeout_s=40.0, answer_elapsed_s=95.0
        )
        assert captured_timeouts == [30.0, 40.0]

    def test_no_elapsed_reported_uses_the_default_chain_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [_src(1, "Item", "Supporting source text.")]
        text = "### עובדות מרכזיות\n- טענה תקינה [1]."
        captured_timeouts: list[float] = []

        def _fake_run_with_timeout(fn: Any, timeout_s: float) -> tuple[Any, str | None]:
            captured_timeouts.append(timeout_s)
            if len(captured_timeouts) == 1:
                return None, "ConnectionError"
            return ask_grounding._EntailmentResponse(verdicts=[]), None

        monkeypatch.setattr(ask_grounding, "_run_with_timeout", _fake_run_with_timeout)
        ask_grounding.entailment_filter(text, rows, max_claims=4, chain_fallback=True, chain_timeout_s=40.0)
        assert captured_timeouts == [30.0, 40.0]


class TestEntailmentResidentChainFallback:
    def test_light_chain_unavailable_falls_back_to_resident_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both the direct-ollama and the light-role-chain attempts fail -- a third, explicitly
        paid attempt against the resident role's own chain (which does carry a Claude entry,
        unlike light's agy-then-ollama one) must still recover a usable result."""
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Supporting source text.")]
        text = "### עובדות מרכזיות\n- טענה שגויה [1]."
        calls: list[tuple[str, str | None]] = []

        def _fake(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            provider = kw.get("provider")
            calls.append((role, provider))
            if role == "resident" and provider == "chain":
                return ask_grounding._EntailmentResponse(
                    verdicts=[ask_grounding._ClaimVerdict(index=1, verdict="no")]
                )
            raise ConnectionError("nothing local or light-chain works tonight")

        monkeypatch.setattr(ollama_client, "chat_structured", _fake)
        new_text, removed = ask_grounding.entailment_filter(
            text, rows, max_claims=4, timeout_s=5.0, chain_fallback=True, chain_timeout_s=5.0
        )
        assert calls == [("light", "ollama"), ("light", "chain"), ("resident", "chain")]
        assert removed == 1
        assert "טענה שגויה" not in new_text

    def test_resident_fallback_success_logs_its_own_event(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Supporting source text.")]
        text = "### עובדות מרכזיות\n- טענה כלשהי [1]."

        def _fake(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            if role == "resident":
                return ask_grounding._EntailmentResponse(verdicts=[])
            raise ConnectionError("starved")

        monkeypatch.setattr(ollama_client, "chat_structured", _fake)
        ask_grounding.entailment_filter(
            text, rows, max_claims=4, timeout_s=5.0, chain_fallback=True, chain_timeout_s=5.0
        )
        out = capsys.readouterr().out
        assert "ask.entailment_resident_fallback_used" in out

    def test_disabled_chain_fallback_never_attempts_the_resident_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`chain_fallback=False` (the default) must stay a strict single local-only attempt --
        round 9's/round 10's own pinned contract -- the resident tier must never fire either."""
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Supporting source text.")]
        text = "### עובדות מרכזיות\n- טענה כלשהי [1]."
        calls: list[str] = []

        def _fake(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            calls.append(role)
            raise ConnectionError("starved")

        monkeypatch.setattr(ollama_client, "chat_structured", _fake)
        new_text, removed = ask_grounding.entailment_filter(text, rows, max_claims=4, timeout_s=5.0)
        assert calls == ["light"]
        assert removed == 0
        assert new_text == text

    def test_all_three_tiers_failing_still_reports_a_graceful_skip(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Supporting source text.")]
        text = "### עובדות מרכזיות\n- טענה כלשהי [1]."
        calls: list[str] = []

        def _fake(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            calls.append(role)
            raise ConnectionError("nothing works tonight")

        monkeypatch.setattr(ollama_client, "chat_structured", _fake)
        new_text, removed = ask_grounding.entailment_filter(
            text, rows, max_claims=4, timeout_s=5.0, chain_fallback=True, chain_timeout_s=5.0
        )
        out = capsys.readouterr().out
        assert calls == ["light", "light", "resident"]
        assert removed == 0
        assert new_text == text
        assert "ask.entailment_unavailable" in out
        assert "ask.entailment_check_skipped" in out


class TestRouteWiringPassesAnswerElapsed:
    def test_route_call_site_passes_answer_elapsed_s(self) -> None:
        import inspect

        from eoa.api.routes import ask as ask_route

        source = inspect.getsource(ask_route.ask)
        assert "answer_elapsed_s=" in source
        assert "t_answer_start" in source
