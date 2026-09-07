"""Round 12 (package R12-chat, docs/qa/loop/round_11_judge.md, D5 score 83; worst-list #1, #6).

Two real D5 defects, both logged in judge notes across multiple rounds before either became a
named, targeted fix in this package:

1. **Q6 (AUSA 2026) bare "סי." fragment (worst #1), reproduced byte-for-byte since round 7:**
   traced offline directly against `_iter_units`/`_is_real_sentence_terminator` -- a dotted,
   transliterated Hebrew acronym or place name ("די.סי." for "D.C.", "יו.אס.סי." for "U.S.C.",
   "אי.אר." for "A.R.") glues each 1-3-letter Hebrew segment straight onto the next with *zero*
   whitespace anywhere in the run (e.g. "בוושינגטון, די.סי." has no space between "די." and
   "סי."). `_SENTENCE_END_RE` matched every "." with no boundary check beyond the round-10 decimal/
   abbreviation cases, so each internal "." inside such a run was treated as a real sentence
   terminator, splitting "...די.סי." into "...די." and "סי." as two separate `_iter_units` spans --
   and once *some other, unrelated* guard removed the first half (e.g. an uncited-claim or
   entailment-check violation on "...בוושינגטון, די."), the second half survived on its own,
   exactly the live "סי." shape the round-7 through round-11 judges all independently found. Two
   fixes, matching the module's established two-layer pattern (root cause + content-blind safety
   net):
   - `_is_real_sentence_terminator` gains a third non-boundary case (alongside the existing digit-
     digit decimal and ASCII-abbreviation-word cases): a "." sitting directly between two Hebrew
     letters (no space on either side) is never a real sentence boundary. Applies independently at
     every "." in a run, so a 3+-segment chain ("יו.אס.סי.") needs no special-casing of length --
     only the run's own final "." (followed by real whitespace) is left as the real terminator.
   - `enforce_answer_coherence` gains a second, content-blind pass
     (`_drop_orphan_short_fragments`/`_is_meaningless_short_fragment`) that sweeps the *entire*
     answer -- not just each section's own leading unit, which is all the two existing round-10/11
     checks ever look at -- for a bare, meaningless leftover unit anywhere in the flow: shorter than
     3 words, ends on a literal ".", and has no word of >= 3 Hebrew/Latin letters. Belt-and-
     suspenders for every other way a short orphan like this could arise, not just the dotted-
     acronym case the root-cause fix above already closes directly.
2. **Q7 stray leading space before an otherwise complete sentence (worst #6), confirmed out of
   report-code scope in round 11 and traced this round to the chat/grounding path:** reproduced
   offline against `strip_template_phrases` -- when a banned so_what/filler phrase sits at the very
   start of a *section's own first unit* (right after a heading, with the unit itself carrying an
   incidental leading space from the raw model output -- not a real separator from any previous
   unit, since there is none), the phrase strippers correctly discard that leading space along with
   the phrase (both `strip_so_what_phrases`/`strip_filler_phrases` `.strip()` their own output), but
   `strip_template_phrases`'s own leading-whitespace re-attachment step blindly restored it, on the
   assumption every unit it sees is a mid-paragraph continuation. Fixed at the source: the
   re-attachment now only fires when the unit genuinely follows inline content on the same physical
   line (`answer_text[start - 1] != "\\n"`) -- a real separator is preserved exactly as before, but
   a section-opening unit's own incidental leading space is never reintroduced. A second,
   content-blind safety net (`ask_grounding.strip_stray_line_edges`, wired into `routes.ask` as the
   very last normalisation step) trims stray leading/trailing whitespace from every line of the
   final answer regardless of which call site produced it, skipping fenced code blocks and never
   touching a line's own internal content (table-cell padding included).

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 .venv\\Scripts\\python -m pytest tests/unit/test_ask_round12.py -q``
"""

from __future__ import annotations

import inspect

from eoa.api import ask_grounding

# ---------------------------------------------------------------------------------------------
# 1. `_is_real_sentence_terminator` / `_iter_units` -- finding 1 root cause (dotted Hebrew acronym)
# ---------------------------------------------------------------------------------------------


class TestDottedHebrewAcronymTerminator:
    def test_washington_dc_stays_glued_as_one_unit(self) -> None:
        """The literal live Q6 shape: 'די.סי.' (transliterated 'D.C.') never splits into 'די.' and
        a stray 'סי.' -- the whole run stays part of the sentence it belongs to."""
        text = "כנס AUSA 2026 מתקיים בוושינגטון, די.סי. בתחילת אוקטובר [1]."
        units = [text[s:e] for s, e in ask_grounding._iter_units(text)]
        assert units == [
            "כנס AUSA 2026 מתקיים בוושינגטון, די.סי.",
            " בתחילת אוקטובר [1].",
        ]
        assert "סי." not in [u.strip() for u in units]

    def test_three_segment_chain_stays_glued(self) -> None:
        """A 3+-segment dotted chain ('יו.אס.סי.', transliterated 'U.S.C.') needs no special-
        casing of chain length -- each internal '.' is checked independently."""
        text = "הנושא מוסדר תחת יו.אס.סי. בהתאם לחוק [2]."
        units = [text[s:e] for s, e in ask_grounding._iter_units(text)]
        assert units == [
            "הנושא מוסדר תחת יו.אס.סי.",
            " בהתאם לחוק [2].",
        ]

    def test_two_letter_segment_chain_stays_glued(self) -> None:
        text = "הפניה נעשית אל אי.אר. כפי שצוין לעיל [3]."
        units = [text[s:e] for s, e in ask_grounding._iter_units(text)]
        assert units == [
            "הפניה נעשית אל אי.אר.",
            " כפי שצוין לעיל [3].",
        ]

    def test_final_dot_of_the_run_is_still_a_real_terminator(self) -> None:
        """Only the *internal* dots of the run are non-boundaries -- the run's own last '.',
        followed by real whitespace and not another Hebrew letter, still correctly ends the
        sentence, so the two halves are not glued into one giant unit."""
        text = "מדובר בהצעה מטעם די.סי. וזאת בלבד."
        units = [text[s:e] for s, e in ask_grounding._iter_units(text)]
        assert len(units) == 2
        assert units[0].strip().endswith("די.סי.")

    def test_decimal_point_regression_unaffected(self) -> None:
        """Round 10's digit-digit decimal-point case is untouched by the new Hebrew-letter check."""
        text = "ההערכה עומדת על 1.53 מיליארד דולר לפי [1]."
        units = [text[s:e] for s, e in ask_grounding._iter_units(text)]
        assert len(units) == 1
        assert units[0] == text

    def test_latin_abbreviation_regression_unaffected(self) -> None:
        """Round 10's ASCII-abbreviation-word case ('Inc.', 'vs.', ...) is untouched."""
        text = "לחברת Aerojet Rocketdyne Inc. יש חוזה [1]."
        units = [text[s:e] for s, e in ask_grounding._iter_units(text)]
        assert len(units) == 1
        assert units[0] == text

    def test_a_dot_between_a_hebrew_letter_and_a_digit_is_still_a_real_terminator(self) -> None:
        """The new check only ever fires when *both* neighbours of the '.' are Hebrew letters --
        a Hebrew letter immediately followed by a digit (not another dotted-acronym segment, and
        not a digit-digit decimal either) is an unrelated shape and must still terminate exactly
        as it already did before this round's fix."""
        text = "הפריט תואר בסעיף ד.5 בהמשך המסמך."
        dot_pos = text.index(".", text.index("ד"))
        assert text[dot_pos - 1] == "ד"
        assert text[dot_pos + 1] == "5"
        assert ask_grounding._is_real_sentence_terminator(text, dot_pos)


# ---------------------------------------------------------------------------------------------
# 2. `_is_meaningless_short_fragment` / `_drop_orphan_short_fragments` -- the belt-and-suspenders
#    safety net over the *whole* answer, not just a section's leading unit.
# ---------------------------------------------------------------------------------------------


class TestOrphanShortFragmentSafetyNet:
    def test_bare_si_fragment_is_meaningless(self) -> None:
        """The exact live Q6 leftover: one word, 2 letters, ends on '.'."""
        assert ask_grounding._is_meaningless_short_fragment("סי.")

    def test_short_word_with_a_long_enough_word_is_not_meaningless(self) -> None:
        """'אין נתונים.' is a normal, valid two-word answer shape -- 'נתונים' is 6 letters, well
        above the >= 3 letter floor, so this must never be touched."""
        assert not ask_grounding._is_meaningless_short_fragment("אין נתונים.")

    def test_lo_yadua_the_documented_valid_bullet_shape_is_not_meaningless(self) -> None:
        """'לא ידוע.' -- explicitly documented elsewhere in this module as a normal, valid answer
        shape ('ידוע' is 4 letters)."""
        assert not ask_grounding._is_meaningless_short_fragment("לא ידוע.")

    def test_bare_punctuation_with_no_letter_word_at_all_is_not_meaningless(self) -> None:
        """A lone '.' (e.g. left over from an unrelated '(...)'-style ellipsis) has no letter-word
        at all -- a different, out-of-scope cosmetic artifact this check deliberately leaves alone
        (regression guard: this exact shape must not start being flagged, see the round-11 cross-
        reference test suite's own fixture using literal '(...)')."""
        assert not ask_grounding._is_meaningless_short_fragment(".")

    def test_unit_ending_in_exclamation_is_never_flagged(self) -> None:
        """Only a literal '.' ending is in scope -- '!'/'?'/gershayim read as complete even when
        short."""
        assert not ask_grounding._is_meaningless_short_fragment("לא!")

    def test_three_word_unit_is_never_flagged_regardless_of_word_length(self) -> None:
        assert not ask_grounding._is_meaningless_short_fragment("א ב ג.")

    def test_drop_orphan_short_fragments_removes_mid_flow_leftover(self) -> None:
        text = "תשובה ראשית תקינה עם ציטוט מלא כאן [1].\n\nסי.\n\n### פערים / מה לא ידוע\n- פער ראשון [1]."
        new_text, removed = ask_grounding._drop_orphan_short_fragments(text)
        assert removed == 1
        assert "סי." not in new_text
        assert "תשובה ראשית תקינה" in new_text
        assert "פער ראשון" in new_text

    def test_bullet_short_fragment_is_never_flagged_by_the_sweep(self) -> None:
        text = "### עובדות מרכזיות\n- סי.\n- עובדה נוספת עם ציטוט מלא [1]."
        new_text, removed = ask_grounding._drop_orphan_short_fragments(text)
        assert removed == 0
        assert new_text == text

    def test_no_op_when_nothing_qualifies(self) -> None:
        text = "תשובה תקינה לגמרי בלי שום שבר תלוי [1]."
        new_text, removed = ask_grounding._drop_orphan_short_fragments(text)
        assert removed == 0
        assert new_text == text


class TestEnforceAnswerCoherenceOrphanIntegration:
    def test_the_live_ausa_shape_end_to_end(self) -> None:
        """Full integration through the public entry point: a section's leading unit is fine (long
        enough, real punctuation), but a bare 'סי.' sits orphaned between it and the next heading --
        exactly what round 7-11's judges described live on Q6."""
        text = (
            "המקורות שסופקו אינם עוסקים בכנס AUSA 2026 באופן ישיר, ואינם מזכירים חברות "
            "ישראליות המתכננות להשתתף בו [2].\n\n"
            "סי.\n\n"
            "### פערים / מה לא ידוע\n"
            "- לא נמצא מקור העוסק בכנס AUSA 2026 עצמו [1]."
        )
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1
        assert "\nסי.\n" not in new_text
        assert not any(line.strip() == "סי." for line in new_text.split("\n"))
        assert "המקורות שסופקו אינם עוסקים" in new_text
        assert "לא נמצא מקור העוסק" in new_text

    def test_existing_round11_cross_reference_behaviour_is_unaffected(self) -> None:
        """Regression guard for the exact fixture `test_ask_round11.py`'s own
        `TestCrossReferenceOpeningFragment.test_short_cross_ref_opening_is_dropped_entirely` pins
        (``removed == 1``) -- the new orphan sweep must never additionally flag the stray bare '.'
        units an unrelated '(...)' placeholder leaves behind in that fixture."""
        text = "שאר המקורות (...) \n\n### עובדות מרכזיות\n- עובדה תקינה עם ציטוט מלא [1]."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1
        assert "שאר המקורות" not in new_text
        assert "עובדה תקינה" in new_text


# ---------------------------------------------------------------------------------------------
# 3. `strip_template_phrases` -- finding 2 root cause (Q7 stray leading space)
# ---------------------------------------------------------------------------------------------


class TestLeadingSpaceStrayFix:
    def test_section_opening_unit_never_regains_a_leading_space(self) -> None:
        """The literal live Q7 shape reconstructed: a filler phrase at the very start of a
        section's own first unit, with an incidental leading space on that line -- the phrase
        strippers correctly discard it; `strip_template_phrases` must not restore it."""
        text = "### הערכת האנליסט\n בהקשר זה, התוכנית האמורה מצביעה על מגמה חשובה בשוק."
        new_text, count = ask_grounding.strip_template_phrases(text)
        assert count == 1
        lines = new_text.split("\n")
        assert lines[1] == "התוכנית האמורה מצביעה על מגמה חשובה בשוק."
        assert not lines[1].startswith(" ")

    def test_unheaded_lead_first_unit_never_regains_a_leading_space(self) -> None:
        """Same shape at the very start of the whole answer (``start == 0``), not under a heading."""
        text = " יש לציין כי התוכנית אושרה השבוע במלואה [1]."
        new_text, count = ask_grounding.strip_template_phrases(text)
        assert count == 1
        assert new_text == "התוכנית אושרה השבוע במלואה [1]."

    def test_true_mid_sentence_separator_space_is_still_preserved(self) -> None:
        """A genuine mid-paragraph continuation -- the unit really does follow inline content on
        the same line -- must keep behaving exactly as round 6 designed it: the separator space is
        restored so the surviving text doesn't glue onto the previous sentence's punctuation."""
        text = "התוכנית אושרה במלואה. בהקשר זה, התקציב הוגדל משמעותית [1]."
        new_text, count = ask_grounding.strip_template_phrases(text)
        assert count == 1
        assert new_text == "התוכנית אושרה במלואה. התקציב הוגדל משמעותית [1]."
        assert ".  " not in new_text  # no double-space glue artifact either


# ---------------------------------------------------------------------------------------------
# 4. `strip_stray_line_edges` -- finding 2 belt-and-suspenders safety net
# ---------------------------------------------------------------------------------------------


class TestStripStrayLineEdges:
    def test_strips_leading_and_trailing_spaces_per_line(self) -> None:
        text = "### כותרת\n התוכנית אושרה השבוע [1]. \n- עובדה ראשונה [1]."
        new_text = ask_grounding.strip_stray_line_edges(text)
        assert new_text == "### כותרת\nהתוכנית אושרה השבוע [1].\n- עובדה ראשונה [1]."

    def test_code_fence_content_is_never_touched(self) -> None:
        text = "טקסט רגיל.\n```\n  indented code line  \n```\nעוד טקסט."
        new_text = ask_grounding.strip_stray_line_edges(text)
        lines = new_text.split("\n")
        assert lines[2] == "  indented code line  "

    def test_table_row_edges_stripped_but_internal_cell_spacing_untouched(self) -> None:
        text = " | כותרת | ערך |  \n| --- | --- |\n| שם   | 5 |"
        new_text = ask_grounding.strip_stray_line_edges(text)
        lines = new_text.split("\n")
        assert lines[0] == "| כותרת | ערך |"
        # internal cell padding ("שם   |") is untouched -- only the line's own two ends were trimmed
        assert lines[2] == "| שם   | 5 |"

    def test_no_op_when_nothing_would_change(self) -> None:
        text = "### כותרת\nהתוכנית אושרה השבוע [1].\n- עובדה ראשונה [1]."
        new_text = ask_grounding.strip_stray_line_edges(text)
        assert new_text == text

    def test_single_line_text_with_no_newline_is_still_stripped(self) -> None:
        assert ask_grounding.strip_stray_line_edges(" תשובה קצרה. ") == "תשובה קצרה."

    def test_blank_text_is_a_no_op(self) -> None:
        assert ask_grounding.strip_stray_line_edges("") == ""


class TestRouteWiringStripStrayLineEdges:
    def test_route_calls_strip_stray_line_edges_after_residual_sources_strip(self) -> None:
        """`strip_stray_line_edges` must run as the very last normalisation step, after the
        sentinel/sources-block sanitizer and before the single `answer_final` emission -- see the
        module's own round-12 comment for why ordering matters (it must see the fully-assembled
        text, not an intermediate stage)."""
        from eoa.api.routes import ask as ask_route

        source = inspect.getsource(ask_route.ask)
        strip_pos = source.index("ask_grounding.strip_stray_line_edges(answer_text)")
        sources_block_pos = source.index("_strip_residual_sources_block(answer_text)")
        answer_final_pos = source.index('"type": "answer_final"')
        assert sources_block_pos < strip_pos < answer_final_pos
