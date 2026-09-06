"""P10 (docs/qa/loop/round_5_chat_fixes.md, "New findings this round"): five residual grounding
gaps the round-5 write-up's own live 8-question x 2-sample verification pass found, live-reproduced
but out of that package's own scope to fix:

1. A single-digit money magnitude ("5 מיליארד דולר" vs. a real "$1.53bn") bypassed
   `_digits_grounded`'s own `< 2` digit floor entirely.
2. `_equivalence_violation` flagged the question's own acronym/expansion gloss ("DROIC" / "Digital
   Read-Out Integrated Circuit") as a fabricated equivalence claim.
3. Every proper-noun/entity pattern in `ask_grounding.py` was Latin-script-only, so a fabricated
   Hebrew institution name ("מאוניברסיטת אריזונה סטייט") was invisible to every guard.
4. Unit-based removal truncated a plain numbered list mid-item.
5. The model can satisfy the existing answer-side anchor-presence guard by repeating the question's
   own anchor term throughout an answer whose *retrieved sources* never mention it at all (Q3/LORA,
   Q6/AUSA) -- closed by the new `retrieval_relevance_caveat`.

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 .venv\\Scripts\\python -m pytest tests/unit/test_ask_round5_grounding.py -q``
"""

from __future__ import annotations

from typing import Any

from eoa.api import ask_grounding


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
# 1. Single-digit money-magnitude grounding
# ---------------------------------------------------------------------------------------------


class TestSingleDigitMoneyMagnitude:
    def test_live_repro_5_billion_vs_real_1_53_billion_is_removed(self) -> None:
        """Live-verified (docs/qa/loop/round_5_chat_fixes.md, "New findings" #1, Q1 sample 1): the
        real cited figure is $1.53bn; the model restated it as "5 מיליארד דולר". A single-digit
        magnitude must not auto-pass just because it is < 2 digits."""
        rows = [_src(1, "XM30 program funding", "The program is valued at $1.53 billion per item 257.")]
        text = "### עובדות מרכזיות\n- תוכנית ה-XM30 מוערכת בכ-5 מיליארד דולר [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "כמה שווה תוכנית XM30?", rows)
        assert removed == 1
        assert "5 מיליארד" not in new_text

    def test_same_magnitude_different_unit_phrasing_is_grounded(self) -> None:
        rows = [_src(1, "Contract value", "The contract is worth 5,000 million dollars.")]
        text = "### עובדות מרכזיות\n- החוזה מוערך בכ-5 מיליארד דולר [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_exact_dollar_sign_billion_suffix_is_grounded(self) -> None:
        rows = [_src(1, "Contract value", "AeroVironment signed a deal worth $5bn.")]
        text = "### עובדות מרכזיות\n- AeroVironment חתמה על חוזה בשווי 5 מיליארד דולר [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_non_money_single_digit_count_is_still_never_flagged(self) -> None:
        """The pre-existing auto-pass for a plain count ("3 מערכות") must be untouched -- it was
        never matched by `_MONEY_RE` in the first place, so it never reaches the new check."""
        rows = [_src(1, "Generic overview", "כתבה כללית ללא מספרים ספציפיים.")]
        text = "### עובדות מרכזיות\n- המערכת כוללת 3 מערכות משנה שונות [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_money_conflation_guard_also_catches_a_misattributed_single_digit_magnitude(self) -> None:
        """The same live pattern as the round-3 `_money_conflation_violation` repro, but carried by
        a single-digit magnitude instead of a multi-digit figure: a real "$5bn" figure genuinely
        retrieved under a different citation, misattributed to an unrelated one."""
        rows = [
            _src(1, "Defense News company ranking", "דירוג חברות ביטחון, ללא מספרים."),
            _src(6, "AeroVironment laser contract", "AeroVironment received a contract worth $5bn."),
        ]
        text = "### עובדות מרכזיות\n- רפאל חתמה על חוזה מגן אור בשווי 5 מיליארד דולר [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(
            text, "מהם פרטי חוזה מגן אור העדכני ביותר של רפאל?", rows
        )
        assert removed == 1
        assert "5 מיליארד" not in new_text


# ---------------------------------------------------------------------------------------------
# 2. Entity-equivalence acronym/expansion gloss exemption
# ---------------------------------------------------------------------------------------------


class TestEquivalenceAcronymExpansionExemption:
    def test_live_repro_droic_gloss_from_the_question_itself_is_kept(self) -> None:
        """Live-verified (docs/qa/loop/round_5_chat_fixes.md, "New findings" #2, Q4 sample 2): the
        question's own "DROIC" / "Digital Read-Out Integrated Circuit" gloss was wrongly replaced
        with a "cannot confirm identity" gap sentence."""
        rows = [_src(1, "DROIC sensor readout paper", "A paper about infrared readout electronics.")]
        text = "### עובדות מרכזיות\n- DROIC הוא Digital Read-Out Integrated Circuit [1]."
        question = "מהי המגמה הטכנולוגית האחרונה ב-DROIC (Digital Read-Out Integrated Circuit)?"
        new_text, removed = ask_grounding.filter_entity_equivalence(text, rows, question)
        assert removed == 0
        assert new_text == text

    def test_roic_expansion_gloss_without_the_leading_word_is_also_kept(self) -> None:
        rows = [_src(1, "Some paper", "כתבה כללית.")]
        text = "### עובדות מרכזיות\n- ROIC, הידוע גם כ-Read-Out Integrated Circuit, משמש לחיישנים [1]."
        new_text, removed = ask_grounding.filter_entity_equivalence(text, rows)
        assert removed == 0
        assert new_text == text

    def test_true_positive_two_unrelated_watchlist_systems_still_fires(self) -> None:
        """Regression guard: the acronym/expansion exemption must not swallow the genuine
        David's Sling/Skynex false-equivalence case round 5 built this guard for."""
        rows = [
            _src(
                1,
                "Rheinmetall Skynex counter-drone system",
                "Rheinmetall's Skynex system is an air-defense solution developed for Germany.",
            )
        ]
        text = "### עובדות מרכזיות\n- David's Sling הוא Skynex, מערכת יירוט גרמנית דומה [1]."
        new_text, removed = ask_grounding.filter_entity_equivalence(
            text, rows, "מהם מאפייני מערכות ההגנה האווירית המובילות כיום?"
        )
        assert removed == 1
        assert "David's Sling הוא" not in new_text

    def test_default_call_signature_without_question_still_works_unchanged(self) -> None:
        """The existing call site in `routes/ask.py` (`filter_entity_equivalence(answer_text,
        retrieved)`, no `question` arg) must keep working exactly as before."""
        rows = [_src(1, "Skynex system", "Rheinmetall Skynex is a German air-defense system.")]
        text = "### עובדות מרכזיות\n- David's Sling הוא Skynex, מערכת יירוט גרמנית דומה [1]."
        _new_text, removed = ask_grounding.filter_entity_equivalence(text, rows)
        assert removed == 1


# ---------------------------------------------------------------------------------------------
# 3. Hebrew-script fabricated entity guard
# ---------------------------------------------------------------------------------------------


class TestHebrewEntityGuard:
    def test_live_repro_fabricated_arizona_state_university_is_removed(self) -> None:
        """Live-verified (docs/qa/loop/round_5_chat_fixes.md, "New findings" #3, Q4 sample 2): the
        real arXiv listing names no institution at all (anonymous submission); the model invented
        "אוניברסיטת אריזונה סטייט" (Arizona State University)."""
        rows = [_src(1, "DROIC readout paper", "Anonymous submission on infrared readout electronics.")]
        text = "### עובדות מרכזיות\n- אוניברסיטת אריזונה סטייט ביצעה את המחקר [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "מהי המגמה האחרונה ב-DROIC?", rows)
        assert removed == 1
        assert "אריזונה" not in new_text

    def test_glued_hebrew_prefix_on_the_head_noun_is_stripped_before_comparison(self) -> None:
        """ "מאוניברסיטת" ("from the university of") glues the "מ" preposition directly onto the
        head noun with no space -- the stripped form must still compare against a source that
        spells the bare head noun with no such prefix."""
        rows = [_src(1, "Weizmann profile", "מכון ויצמן הוא מוסד מחקר מוביל.")]
        text = "### עובדות מרכזיות\n- הנתונים פורסמו ממכון ויצמן [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_generic_allowlisted_institution_is_never_flagged(self) -> None:
        rows = [_src(1, "Some unrelated item", "כתבה כללית שאינה קשורה כלל.")]
        text = "### עובדות מרכזיות\n- לפי חיל האוויר, האספקה צפויה להסתיים ברבעון הבא [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_institution_present_in_a_different_retrieved_source_counts_as_weakly_grounded(self) -> None:
        """ "Any retrieved source, not only the cited one" -- an institution mentioned somewhere
        else in the retrieval must not be flagged just because the specific citation on this unit
        doesn't itself repeat it."""
        rows = [
            _src(1, "Unrelated general item", "כתבה כללית שאינה קשורה."),
            _src(2, "Weizmann Institute profile", "מכון ויצמן הוא מוסד מחקר מוביל בישראל."),
        ]
        text = "### עובדות מרכזיות\n- מכון ויצמן פרסם ממצאים חדשים [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_institution_named_in_the_question_itself_is_grounded(self) -> None:
        rows = [_src(1, "Unrelated item", "כתבה כללית.")]
        text = "### עובדות מרכזיות\n- מכון ויצמן ממשיך במחקריו בתחום [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "מה קורה במכון ויצמן?", rows)
        assert removed == 0
        assert new_text == text

    def test_analyst_assessment_section_is_exempt_even_when_cited(self) -> None:
        """The `### הערכת האנליסט` section never carries a citation by format rule 3, so this
        guard's own citation gate already exempts it -- verified here directly."""
        rows = [_src(1, "Unrelated item", "כתבה כללית.")]
        text = "### הערכת האנליסט\nיתכן שהמחקר בוצע במכון פיקטיבי כלשהו שאינו קיים במציאות."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_bare_hebrew_word_with_no_head_noun_shape_is_never_a_candidate(self) -> None:
        """ "Never bare Hebrew words" -- a sentence with no institution head-noun shape at all must
        never be touched by this guard, however unfamiliar its own proper nouns are."""
        rows = [_src(1, "Some source", "כתבה כללית לגמרי.")]
        text = "### עובדות מרכזיות\n- פלוני אלמוני כלשהו ציין נתון חדש [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text


# ---------------------------------------------------------------------------------------------
# 4. Numbered-list-aware unit removal + renumbering
# ---------------------------------------------------------------------------------------------


class TestNumberedListAwareRemoval:
    def test_removing_item_3_of_4_renumbers_the_remaining_items_sequentially(self) -> None:
        """Live-verified pattern (docs/qa/loop/round_5_chat_fixes.md, "New findings" #5, Q5 sample
        1): removing a flagged numbered-list item used to leave the list truncated mid-item."""
        rows = [_src(1, "Some source", "טקסט כללי בלי שום ישות מיוחדת.")]
        text = (
            "### עובדות מרכזיות\n"
            "1. פריט ראשון תקין [1].\n"
            "2. פריט שני תקין [1].\n"
            "3. המחקר בוצע בהובלת Kunat Pipatanakul מ-Ratchaburi Institute of Technology [1].\n"
            "4. פריט רביעי תקין [1].\n"
        )
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 1
        assert "Kunat Pipatanakul" not in new_text
        lines = [ln for ln in new_text.splitlines() if ln.strip()]
        item_lines = [ln for ln in lines if ln.strip()[0].isdigit()]
        assert item_lines == [
            "1. פריט ראשון תקין [1].",
            "2. פריט שני תקין [1].",
            "3. פריט רביעי תקין [1].",
        ]

    def test_numbered_item_with_a_continuation_line_is_removed_as_one_whole_unit(self) -> None:
        rows = [_src(1, "Some source", "טקסט כללי בלי שום ישות מיוחדת.")]
        text = (
            "### עובדות מרכזיות\n"
            "1. פריט תקין [1].\n"
            "2. המחקר בוצע בהובלת Kunat Pipatanakul,\n"
            "מ-Ratchaburi Institute of Technology [1].\n"
            "3. פריט תקין נוסף [1].\n"
        )
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 1
        assert "Kunat Pipatanakul" not in new_text
        assert "Ratchaburi Institute of Technology" not in new_text
        lines = [ln for ln in new_text.splitlines() if ln.strip()]
        item_lines = [ln for ln in lines if ln.strip()[0].isdigit()]
        assert item_lines == ["1. פריט תקין [1].", "2. פריט תקין נוסף [1]."]

    def test_numbered_list_with_no_removal_is_left_completely_untouched(self) -> None:
        rows = [_src(1, "XM30 program update", "Lynx XM30 is the GDLS-built Bradley replacement.")]
        text = "### עובדות מרכזיות\n1. פריט ראשון [1].\n2. פריט שני [1].\n"
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "מה קורה עם XM30?", rows)
        assert removed == 0
        assert new_text == text

    def test_renumber_lists_resets_at_a_blank_line_between_two_separate_lists(self) -> None:
        text = "1. א\n2. ב\n\n1. ג\n2. ד\n"
        assert ask_grounding._renumber_lists(text) == text


# ---------------------------------------------------------------------------------------------
# 5. Retrieval-relevance caveat
# ---------------------------------------------------------------------------------------------


class TestRetrievalRelevanceCaveat:
    def test_live_repro_greece_lora_no_source_mentions_the_anchor_gets_a_caveat(self) -> None:
        """Live-verified pattern (docs/qa/loop/round_5_chat_fixes.md, "New findings" #4, Q3): none
        of the retrieved sources mention "LORA" anywhere, but the model's answer repeats it
        throughout, satisfying the answer-side anchor-presence guard trivially."""
        rows = [
            _src(
                1,
                "Greece signs air-defense deal",
                "Greece signed a major air-defense deal known as the 'Achilles Shield'.",
            )
        ]
        text = "### עובדות מרכזיות\n- יוון חתמה על עסקת ענק, כולל David's Sling ו-Barak MX [1]."
        question = "עסקת ה-LORA היוונית (Greece) -- מה המשמעות עבור התעשייה הביטחונית הישראלית?"
        new_text, added = ask_grounding.retrieval_relevance_caveat(text, question, rows)
        assert added is True
        assert new_text.startswith("> ⚠️")
        assert "LORA" in new_text
        assert new_text.endswith(text)

    def test_live_repro_ausa_no_source_mentions_the_anchor_gets_a_caveat(self) -> None:
        rows = [
            _src(
                1,
                "Commercial UAV Expo: DJI booth dispute",
                "Coverage of the Commercial UAV Expo, unrelated to any army conference.",
            )
        ]
        text = "### עובדות מרכזיות\n- כנס AUSA הוא אירוע מפתח בתעשיית הביטחון [1]."
        question = "מה קרה בכנס AUSA 2026?"
        new_text, added = ask_grounding.retrieval_relevance_caveat(text, question, rows)
        assert added is True
        assert "AUSA" in new_text.splitlines()[0]

    def test_anchor_mentioned_in_a_source_never_triggers_the_caveat(self) -> None:
        rows = [_src(1, "XM30 program update", "Lynx XM30 is the GDLS-built Bradley replacement.")]
        text = "### עובדות מרכזיות\n- תוכנית ה-XM30 מבוססת על GDLS Lynx [1]."
        new_text, added = ask_grounding.retrieval_relevance_caveat(text, "מה קורה עם XM30?", rows)
        assert added is False
        assert new_text == text

    def test_no_anchors_at_all_is_a_no_op(self) -> None:
        rows = [_src(1, "Some source", "כתבה כללית.")]
        text = "### עובדות מרכזיות\n- עובדה כלשהי [1]."
        new_text, added = ask_grounding.retrieval_relevance_caveat(text, "מה?", rows)
        assert added is False
        assert new_text == text

    def test_no_retrieved_sources_is_a_no_op(self) -> None:
        text = "תשובה כלשהי."
        new_text, added = ask_grounding.retrieval_relevance_caveat(text, "עסקת ה-LORA היוונית", [])
        assert added is False
        assert new_text == text

    def test_blank_answer_is_a_no_op(self) -> None:
        rows = [_src(1, "Some source", "כתבה כללית.")]
        new_text, added = ask_grounding.retrieval_relevance_caveat("   ", "עסקת ה-LORA היוונית", rows)
        assert added is False
        assert new_text == "   "

    def test_never_removes_any_content_only_prepends(self) -> None:
        rows = [_src(1, "Unrelated", "טקסט שאינו מזכיר את העוגן כלל.")]
        text = "### עובדות מרכזיות\n- תוכן קיים כלשהו לגבי TOPICX [1]."
        new_text, added = ask_grounding.retrieval_relevance_caveat(text, "מה קורה עם TOPICX?", rows)
        assert added is True
        assert text in new_text
