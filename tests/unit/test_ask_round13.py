"""Round 13 (package R13-chat, docs/qa/loop/round_12_judge.md, D5 score 89; worst-list #2, #3).

Two real D5 defects from round 12's judge report, both traced to a concrete root cause offline
(no live probes needed to *find* either -- only to confirm the fix live afterwards):

1. **Q5 (Skyranger) rate-of-fire figure "doesn't reach the final synthesized text" (worst #3):**
   round 12's own judge confirmed the *retrieval* layer works (item 1353 is cited, its content is
   used) but the exact "1,000 rounds per minute" figure never appears in the final answer. Traced
   directly against the live DB row (item 1353, "U.S. Air Force Seeks Anti-Aircraft Guns To Protect
   Its Overseas Bases"): the article discusses *two* different systems' rates of fire under the
   same generic noun -- "Centurion, at a rate of fire of 4,500 rounds per minute" (unrelated,
   appearing earlier in the document) and "[Skyranger] ... a firing rate of 1,000 rounds per
   minute" (the actually-cited figure). `_relevant_excerpt`'s round-11 anchor window correctly
   includes the real figure in what the model sees (proven directly against the live row, see
   `TestQ5RelevantExcerptStillIncludesTheFigure` below) -- the bug is downstream, in
   `_count_mismatch_violation` (`filter_claim_count_mismatch`): a comma-grouped number ("1,000") is
   split by `_count_candidates` into two separate count candidates (the "1" and the "000"), and the
   function's old first-match-wins comparison matched the claim's "1" against the *earlier*,
   unrelated "4,500" mention (same noun, "rounds") without ever checking whether a *later* same-noun
   occurrence in the same cited source actually agrees -- silently "correcting" a verbatim-correct
   "1,000 rounds per minute [n]" into a fabricated "4,000 rounds per minute [n]". Fixed in
   `ask_grounding._count_mismatch_violation`: a claim's digit is now compared against *every*
   same-noun source candidate, not just the first found in document order -- grounded (no mismatch)
   if it matches *any* of them, regardless of what an earlier, unrelated same-noun occurrence
   elsewhere in a multi-topic source says.

   Investigated and ruled out as the (or an) actual cause, but a genuine latent gap found along the
   way and fixed anyway (`_digits_grounded`/`_grouped_digit_pattern`): a money-figure candidate
   whose digits are compared against a *comma/period-grouped* corpus occurrence ("$1,000") never
   matched, because the candidate's own digits are stripped of separators before the search but the
   corpus text is not -- the literal digit-substring search can never find a comma sitting in the
   middle of the run it is looking for. Does not apply to the Q5 figure itself (a bare "1,000 rounds
   per minute" carries no currency symbol or scale word, so it never reaches `_MONEY_RE`/
   `_digits_grounded` in the first place), but is a real, independently-reproducible grounding gap
   for any money figure >= 1000 whose corpus mention uses a thousands separator.

2. **Q6 (AUSA 2026) headless mid-clause opening, a new shape of the round-12 "סי." defect class
   (worst #2):** round 12's own two new checks (the dotted-Hebrew-acronym terminator fix, the
   short-orphan-fragment sweep) both closed the *specific* "סי." repro but were scoped to short
   (< 3-word) fragments -- round 12's live answer instead opened with a *longer* (7-word), well
   -punctuated-looking leftover, `מים, ודירוג הכנסות של חברות ביטחון גלובליות)...`, that reads as a
   bare noun/conjunction chain with no finite verb or copula anywhere in it once an earlier guard
   removed the sentence that used to introduce it. Generalised `enforce_answer_coherence` with a
   fourth, independent leading-unit check (`_is_incoherent_leading_unit`) that asks whether a
   section's own leading unit reads as one complete, independent clause at all -- see
   `ask_grounding.py`'s own round-13 section note (right above `_is_incoherent_leading_unit`) for
   the full four-signal design and the two false-positive regressions this test file's own
   end-to-end-shaped fixtures caught and drove fixes for during development (a legitimate past-tense
   verb not recognised, and this module's own blockquote/warning-prefix leading markers wrongly
   treated as incoherent).

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 .venv\\Scripts\\python -m pytest tests/unit/test_ask_round13.py -q``
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from eoa.api import ask_grounding, services

# ---------------------------------------------------------------------------------------------
# helpers (self-contained, matching every other round's own test-file convention)
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


# The live item-1353 shape (paraphrased close to the actual retrieved article text quoted in
# round_12_judge.md and traced directly against the DB row): two different systems' rates of fire,
# both under the generic noun "rounds", the unrelated one appearing first in document order.
_Q5_SOURCE_TEXT = (
    "The U.S. Air Force is weighing several counter-drone gun options. Centurion, at a rate of "
    "fire of 4,500 rounds per minute, would provide short-range point defense against small "
    "drones. The Skyranger 35 is equipped with a KDG 35/1000 revolver cannon in 35 mm x 228 "
    "caliber with a firing rate of 1,000 rounds per minute, according to Rheinmetall, which began "
    "delivering these to Ukraine in January. It has an effective range of up to 4,000 meters."
)


# ---------------------------------------------------------------------------------------------
# 1. `_count_mismatch_violation` / `filter_claim_count_mismatch` -- Q5 root cause (worst #3)
# ---------------------------------------------------------------------------------------------


class TestCountMismatchAnySameNounOccurrenceGrounds:
    def test_live_repro_correct_comma_grouped_figure_is_no_longer_corrupted(self) -> None:
        """The concrete live repro: a correctly-quoted "1,000 rounds per minute [1]" claim, cited
        against the real multi-topic item-1353-shaped source, used to be silently rewritten to
        "4,000 rounds per minute" because the claim's own "1" (from the comma-split "1,000") was
        compared only against the *first* same-noun ("rounds") occurrence in the source -- an
        earlier, unrelated "4,500 rounds per minute" mention for a different system entirely."""
        rows = [_src(1353, "U.S. Air Force Seeks Anti-Aircraft Guns", _Q5_SOURCE_TEXT)]
        text = "### עובדות מרכזיות\n- ל-Skyranger 35 קצב אש של 1,000 rounds per minute [1]."
        new_text, removed = ask_grounding.filter_claim_count_mismatch(text, rows)
        assert removed == 0
        assert new_text == text
        assert "1,000 rounds per minute" in new_text
        assert "4,000 rounds per minute" not in new_text

    def test_offline_replay_figure_survives_the_full_ground_and_filter_answer_pipeline(self) -> None:
        """Prove -- not just assert -- that the fully guarded answer (both `filter_claim_count_
        mismatch` and `ground_and_filter_answer`, the two guards this figure passes through)
        still contains the figure, end to end, offline (no live LLM call)."""
        rows = [_src(1353, "U.S. Air Force Seeks Anti-Aircraft Guns", _Q5_SOURCE_TEXT)]
        answer = (
            "### עובדות מרכזיות\n"
            '- Skyranger 35 של Rheinmetall מצויד בתותח 35 מ"מ עם קצב אש של 1,000 rounds per '
            "minute [1]."
        )
        after_count, n1 = ask_grounding.filter_claim_count_mismatch(answer, rows)
        after_grounding, n2 = ask_grounding.ground_and_filter_answer(
            after_count, "כיצד משתווה ה-Skyranger למערכות מקבילות?", rows
        )
        assert n1 == 0
        assert n2 == 0
        assert "1,000 rounds per minute" in after_grounding

    def test_claim_matching_the_later_occurrence_not_the_first_is_still_grounded(self) -> None:
        """Same shape, digits swapped: the claim now matches the *second* same-noun source
        occurrence rather than the first -- proves the fix checks every occurrence, not just
        "whichever one happens to come last"."""
        rows = [_src(1, "Item", "Alpha fires at 200 rounds per minute. Beta fires at 900 rounds per minute.")]
        text = "### עובדות מרכזיות\n- Beta יורה בקצב של 900 rounds per minute [1]."
        new_text, removed = ask_grounding.filter_claim_count_mismatch(text, rows)
        assert removed == 0
        assert new_text == text

    def test_genuine_mismatch_against_a_multi_value_source_is_still_caught(self) -> None:
        """The fix must not become so permissive that a genuinely wrong figure slips through just
        because the source happens to mention *some* other same-noun count -- a claimed "700" that
        matches neither of the source's two real values (200, 900) is still flagged and corrected
        to the first same-noun value found."""
        rows = [_src(1, "Item", "Alpha fires at 200 rounds per minute. Beta fires at 900 rounds per minute.")]
        text = "### עובדות מרכזיות\n- Gamma יורה בקצב של 700 rounds per minute [1]."
        new_text, removed = ask_grounding.filter_claim_count_mismatch(text, rows)
        assert removed == 1
        assert "700 rounds per minute" not in new_text
        assert "200 rounds per minute" in new_text

    def test_round8_seven_to_eight_single_value_regression_still_corrected(self) -> None:
        """Regression guard: the original round-8 single-same-noun-value repro (round_7_judge_b.md
        D5 finding #1) must behave identically after the round-13 "any occurrence" generalisation
        -- there is only one candidate to compare against either way."""
        rows = [_src(257, "XM30", "The company plans to deliver seven additional prototypes this year.")]
        text = "### עובדות מרכזיות\n- החברה מתכננת לספק 8 prototypes נוספים השנה [1]."
        new_text, removed = ask_grounding.filter_claim_count_mismatch(text, rows)
        assert removed == 1
        assert "8 prototypes" not in new_text
        assert "7 prototypes" in new_text

    def test_round8_matching_count_still_left_alone(self) -> None:
        rows = [_src(1, "Item", "The company plans to deliver eight additional prototypes this year.")]
        text = "### עובדות מרכזיות\n- החברה מתכננת לספק 8 prototypes נוספים השנה [1]."
        new_text, removed = ask_grounding.filter_claim_count_mismatch(text, rows)
        assert removed == 0
        assert new_text == text


class TestQ5RelevantExcerptStillIncludesTheFigure:
    """Round 12's own excerpt-window fix (`_relevant_excerpt`, round 11) is confirmed still working
    -- this round's investigation ruled out "the model never sees the figure" as the cause, and this
    is the offline proof: with anchors matching the live golden Q5 question's own rare tokens, the
    figure sits well inside the accepted excerpt windows."""

    def test_figure_is_within_the_excerpt_window_around_the_skyranger_anchor(self) -> None:
        from eoa.api.services import _relevant_excerpt

        # A long head-padded document (mirrors the live item's own ~13,500-char offset to its
        # Skyranger mention) so the figure would fall outside a naive `text[:budget]` prefix.
        padding = "לא רלוונטי. " * 1000
        text = padding + _Q5_SOURCE_TEXT
        excerpt = _relevant_excerpt(text, ["Skyranger", "Rheinmetall"], 4000)
        assert "1,000 rounds per minute" in excerpt


# ---------------------------------------------------------------------------------------------
# 2. `_digits_grounded` / `_grouped_digit_pattern` -- thousands-separator normalisation
# ---------------------------------------------------------------------------------------------


class TestDigitsGroundedThousandsSeparator:
    def test_comma_grouped_corpus_figure_grounds_a_separator_free_candidate(self) -> None:
        assert ask_grounding._digits_grounded("1000", "the source states $1,000 exactly") is True

    def test_period_grouped_corpus_figure_grounds_a_separator_free_candidate(self) -> None:
        assert ask_grounding._digits_grounded("1234567", "total figure is 1.234.567 units") is True

    def test_candidate_itself_carrying_a_comma_still_grounds(self) -> None:
        assert ask_grounding._digits_grounded("1,000", "the source states $1,000 exactly") is True

    def test_larger_number_still_grounds_with_multiple_grouping_positions(self) -> None:
        assert ask_grounding._digits_grounded("12345678", "reported as 12,345,678 total") is True

    def test_decimal_boundary_regression_still_rejected(self) -> None:
        """Round 9's own live-found fabrication (docs/qa/loop/round_8_judge_b.md finding 3) must
        still be rejected after the round-13 grouping change -- "53" must never match inside
        "$1.53bn"."""
        assert ask_grounding._digits_grounded("53", "the program is worth $1.53bn total") is False

    def test_short_number_regression_is_completely_unaffected(self) -> None:
        assert ask_grounding._digits_grounded("53", "the contract covers 53 units total") is True
        assert ask_grounding._digits_grounded("15", "measured at 15.3 degrees") is False

    def test_grouped_pattern_does_not_match_an_unrelated_larger_number(self) -> None:
        """A grouped digit run must not accidentally match as a substring of a larger, genuinely
        different number -- "1000" must not ground against a corpus "$11,000" (a different value,
        not just a differently-punctuated form of the same one)."""
        assert ask_grounding._digits_grounded("1000", "the deal is worth $11,000 total") is False

    def test_money_figure_grounded_end_to_end_with_a_thousands_separator_source(self) -> None:
        rows = [_src(1, "Item", "The contract is valued at $5,000 million according to the filing.")]
        text = "### עובדות מרכזיות\n- החוזה מוערך ב-5,000 מיליון דולר לפי הדיווח [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text


# ---------------------------------------------------------------------------------------------
# 3. `_is_incoherent_leading_unit` / `enforce_answer_coherence` -- Q6 generalised coherence check
# ---------------------------------------------------------------------------------------------


class TestIncoherentLeadingUnitPositiveRepros:
    """Units that must be recognised as incoherent and dropped."""

    def test_q6_judge_quoted_opening_shape_is_dropped(self) -> None:
        """The live round-12 repro (docs/qa/loop/round_12_judge.md, D5 worst #2), reconstructed as
        its own leading unit from the judge's own quoted opening: a bare noun/conjunction chain,
        no finite verb or copula anywhere in it."""
        text = "מים, ודירוג הכנסות של חברות ביטחון גלובליות בהשוואה לכנסים אחרים."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1
        assert new_text == ""

    def test_bare_domain_noun_chain_with_blocklisted_nouns_is_dropped(self) -> None:
        """Every content word here shares the templatic מ/נ/ת-prefix shape with a real verb, but
        every one of them is a curated blocklisted domain noun -- confirms the blocklist actually
        suppresses the false "verb-shaped" signal it exists to suppress."""
        text = "מערכת מטרה מרכזי מגזר ביטחוני ומדינות שונות בעולם ובתעשייה."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1
        assert new_text == ""

    def test_lowercase_latin_opening_is_dropped(self) -> None:
        text = "the remaining systems are described below in detail for completeness sake."
        _new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1

    def test_vav_prefixed_conjunction_opening_is_dropped(self) -> None:
        text = "ולמרות זאת החברה ממשיכה לפעול כרגיל בכל התחומים הרלוונטיים."
        _new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1

    def test_shin_prefixed_conjunction_opening_is_dropped(self) -> None:
        text = "שהמצב הזה ממשיך כבר תקופה ארוכה מאוד ללא כל שינוי."
        _new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1


class TestIncoherentLeadingUnitNegativeRegressions:
    """Legitimate openings that must survive untouched -- including two real end-to-end
    regressions this round's own development caught and drove fixes for."""

    def test_legitimate_six_plus_word_present_tense_opening_survives(self) -> None:
        """The brief's own explicit contract: a legitimate 6+-word opening must survive."""
        text = "החברה מפתחת מערכת חדשה לזיהוי איומים באוויר."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 0
        assert new_text == text

    def test_legitimate_short_four_word_opening_survives_round10_contract(self) -> None:
        """Round 10/11's own existing contract (`test_ask_round10.py`,
        `test_complete_short_lead_with_enough_words_and_terminal_punctuation_is_kept`) must still
        hold -- this round's check carries no separate word-count floor of its own."""
        text = "התוכנית אושרה השבוע במלואה [1]."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 0
        assert new_text == text

    def test_legitimate_past_tense_reporting_verb_survives(self) -> None:
        """Real end-to-end regression caught during this round's own development: "חתמה" (a common
        past-tense verb with no future/hifil/piel present-tense prefix) directly preceded by a
        Latin proper noun ("Rheinmetall"), invisible to the suffix-fallback's own Hebrew-only
        preceding-word check -- fixed via the curated past-tense stem list."""
        text = "Rheinmetall חתמה חוזה חדש עם משרד ההגנה האמריקאי [1]."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 0
        assert new_text == text

    def test_legitimate_opening_with_a_number_survives_without_any_verb(self) -> None:
        text = "נכון ל-2026 קיימות 4 מערכות דומות בשוק הבינלאומי הרלוונטי."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 0
        assert new_text == text

    def test_admission_caveat_blockquote_survives(self) -> None:
        """Real end-to-end regression caught during this round's own development:
        `relocate_source_admission_caveat`'s own prepended "> ⚠️ " blockquote marker was wrongly
        treated as an incoherent opening character by an earlier version of this check."""
        text = (
            "> ⚠️ למעשה, 6 מתוך 8 המקורות שאותרו אינם קשורים ישירות לנושא.\n\n"
            "### עובדות מרכזיות\n- Rheinmetall חתמה חוזה חדש [1]."
        )
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 0
        assert new_text == text

    def test_off_topic_warning_prefix_survives(self) -> None:
        """Real end-to-end regression caught during this round's own development:
        `routes.ask._OFF_TOPIC_PREFIX`'s own leading "⚠" glyph was wrongly treated as an
        incoherent opening character by an earlier version of this check."""
        text = (
            "⚠ ייתכן שהתשובה אינה עוסקת בשאלה: המקורות שנשלפו אינם מזכירים LORA עבור ההקשר "
            "שנשאל — לא ניתן לאשר תשובה ישירה."
        )
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 0
        assert new_text == text

    def test_bullet_item_is_never_touched_by_this_check_even_when_short_and_verbless(self) -> None:
        text = "### עובדות מרכזיות\n- מים ודירוג בלבד."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 0
        assert new_text == text

    def test_round11_cross_reference_shape_is_still_handled_by_that_check_not_this_one(self) -> None:
        """The round-11 "שאר המקורות..." shape (docs/qa/loop/round_10_judge.md worst #3) is caught
        first by the existing `_CROSS_REF_OPENING_RE` branch in the same `elif` chain -- its
        antecedent-free token is stripped and the remainder kept, not dropped outright the way this
        round's new check would (it never even reaches this check)."""
        text = "שאר המקורות שנבדקו אינם מתייחסים ישירות לנושא זה ואינם מספקים מידע נוסף רלוונטי."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1
        assert new_text == "המקורות שנבדקו אינם מתייחסים ישירות לנושא זה ואינם מספקים מידע נוסף רלוונטי."


class TestIsIncoherentLeadingUnitDirect:
    """Pure-function-level tests of `_is_incoherent_leading_unit` itself, isolated from the
    section/heading-splitting machinery in `enforce_answer_coherence`."""

    def test_blank_unit_is_never_incoherent(self) -> None:
        assert ask_grounding._is_incoherent_leading_unit("") is False

    def test_decoration_only_unit_is_left_alone(self) -> None:
        """A unit that is nothing but leading decoration once stripped (no real content at all) is
        not this check's concern -- it is left alone rather than guessed at."""
        assert ask_grounding._is_incoherent_leading_unit("> ⚠️ ...") is False

    def test_next_unit_continuation_shaped_leaves_both_units_alone(self) -> None:
        """The brief's own merge-or-drop contract: when the section's very next unit is itself
        continuation-shaped, dropping only the first unit would just crown the second as the new
        (equally headless) leading fragment -- both are left alone instead."""
        text = (
            "מים, ודירוג הכנסות של חברות ביטחון גלובליות בהשוואה לכנסים אחרים. "
            "ובנוסף לכך יש עוד נתונים רבים שטרם פורסמו כלל."
        )
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 0
        assert new_text == text


# ---------------------------------------------------------------------------------------------
# 4. End-to-end SSE wiring -- both fixes through the live `/api/ask` guard pipeline
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
) -> str:
    """Keeps the real `services.ask_build_messages` (unlike a canned-citation stub) so the `[n]`
    numbering the guards rely on is the exact numbering the model's own citations refer to -- only
    retrieval and the LLM calls themselves are mocked, same convention as
    `test_ask_round3_grounding.py`'s own helper of the same name."""
    from eoa.llm import ollama_client

    monkeypatch.setattr(services, "ask_retrieve", lambda *a, **k: rows)
    monkeypatch.setattr(ollama_client, "resolve_provider_info", lambda provider: ("ollama", "resident"))
    monkeypatch.setattr(ollama_client, "chat_stream", lambda *a, **k: iter(chunks))
    monkeypatch.setattr(ollama_client, "chat", lambda *a, **k: type("R", (), {"content": ""})())
    return question


class TestEndToEndQ5CountFigureSurvives:
    def test_correct_comma_grouped_figure_reaches_answer_final_unmodified(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [_row(1353, title="U.S. Air Force Seeks Anti-Aircraft Guns", clean_text=_Q5_SOURCE_TEXT)]
        question = _mock_ask_with_real_sources(
            monkeypatch,
            rows,
            ["### עובדות מרכזיות\n- ל-Skyranger 35 קצב אש של 1,000 rounds per minute [1]."],
            question="כיצד משתווה ה-Skyranger של Rheinmetall למערכות מקבילות?",
        )
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        finals = [e for e in events if e["type"] == "answer_final"]
        assert len(finals) == 1
        assert "1,000 rounds per minute" in finals[0]["text"]
        assert finals[0].get("ungrounded_removed", 0) == 0


class TestEndToEndQ6CoherentOpeningSurvives:
    def test_headless_leading_fragment_never_reaches_answer_final(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [_row(1, title="AUSA 2026", clean_text="AUSA 2026 is a major defense conference.")]
        question = _mock_ask_with_real_sources(
            monkeypatch,
            rows,
            [
                "מים, ודירוג הכנסות של חברות ביטחון גלובליות בהשוואה לכנסים אחרים "
                "[1].\n\n### עובדות מרכזיות\n- AUSA 2026 הוא כנס מרכזי בתעשיית הביטחון [1]."
            ],
            question="מהי הרלוונטיות של כנס AUSA 2026 לתעשייה הישראלית?",
        )
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        finals = [e for e in events if e["type"] == "answer_final"]
        assert len(finals) == 1
        assert not finals[0]["text"].lstrip().startswith("מים,")
        assert finals[0]["removed_by_guard"].get("dangling_fragment", 0) >= 1
