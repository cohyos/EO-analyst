"""Unit tests for Round-4b W27 (docs/REVIEW_2026-09-06_evening.md): the deterministic
``format_investigation_answer_he`` assembly and its bidi-safe spacing/isolation pass.

Pure string-processing -- no DB, no Ollama, no network (CONVENTIONS.md rule 10).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_deep_search_answer_format.py -q``
"""

from __future__ import annotations

import re
from typing import ClassVar

import pytest

from eoa.search.deep_search import (
    _LRI,
    _PDI,
    _bidi_space_and_isolate,
    _needs_bidi_space,
    _split_bidi_runs,
    format_investigation_answer_he,
)


class TestSplitBidiRuns:
    def test_pure_hebrew_is_one_run(self) -> None:
        runs = _split_bidi_runs("שלום עולם")
        assert runs == [("he", "שלום עולם")]

    def test_pure_latin_is_one_other_run(self) -> None:
        runs = _split_bidi_runs("Elbit Systems")
        assert runs == [("other", "Elbit Systems")]

    def test_hebrew_then_latin_breaks_into_two_runs(self) -> None:
        runs = _split_bidi_runs("שלום Elbit")
        classes = [c for c, _ in runs]
        assert classes == ["he", "other"]

    def test_bracket_pair_symmetry_matches_docx_builder_convention(self) -> None:
        """'(Targeting Pods)' embedded in Hebrew prose: the closing ')' must take the class its
        matching '(' was emitted with (here: 'he', since '(' directly follows Hebrew text) rather
        than whatever is "current" at the closing mark's own position -- exactly
        `docx_builder.split_runs`'s own documented behavior for the same construct. Both brackets
        therefore land in the surrounding Hebrew run, and the isolated English run is the bare
        "Targeting Pods" with no bracket on either side -- an asymmetric split would instead put
        only the closing ')' in the Latin run."""
        runs = _split_bidi_runs("פודים (Targeting Pods) חדשים")
        other_chunks = [chunk for cls, chunk in runs if cls == "other"]
        assert other_chunks == ["Targeting Pods"]
        he_chunks = [chunk for cls, chunk in runs if cls == "he"]
        assert any("(" in chunk for chunk in he_chunks)
        assert any(")" in chunk for chunk in he_chunks)


class TestNeedsBidiSpace:
    def test_letter_then_hebrew_letter_needs_space(self) -> None:
        assert _needs_bidi_space("g", "פ") is True

    def test_digit_then_hebrew_letter_needs_space(self) -> None:
        assert _needs_bidi_space("5", "א") is True

    def test_existing_whitespace_never_doubles(self) -> None:
        assert _needs_bidi_space(" ", "פ") is False
        assert _needs_bidi_space("g", " ") is False

    def test_bracket_boundary_never_forces_a_space(self) -> None:
        """The exact regression this function exists to avoid: "- [1] https://..." must never
        become "- [ 1] https://..."."""
        assert _needs_bidi_space("[", "1") is False
        assert _needs_bidi_space("1", "]") is False


class TestBidiSpaceAndIsolate:
    def test_latin_run_immediately_before_hebrew_gets_a_space_and_isolate_marks(self) -> None:
        out = _bidi_space_and_isolate("Elbit Systemsזכתה בחוזה")
        assert out == f"{_LRI}Elbit Systems{_PDI} זכתה בחוזה"

    def test_already_spaced_text_is_not_double_spaced(self) -> None:
        """A single space in the source stays a single space -- whether it ends up just inside or
        just outside the isolate marks is an implementation detail (both render identically, the
        marks are invisible); only the visible text and the absence of a doubled space matter."""
        out = _bidi_space_and_isolate("Elbit Systems זכתה בחוזה")
        assert "  " not in out
        visible = out.replace(_LRI, "").replace(_PDI, "")
        assert visible == "Elbit Systems זכתה בחוזה"

    def test_pure_hebrew_line_is_untouched_besides_no_isolation(self) -> None:
        out = _bidi_space_and_isolate("החוזה נחתם היום")
        assert out == "החוזה נחתם היום"
        assert _LRI not in out and _PDI not in out

    def test_markdown_header_after_bracketed_citation_is_not_swallowed(self) -> None:
        """Regression: a trailing "[2]" at the end of one line must not bleed its isolate-wrapped
        'other' run across the newline into the next line's own "### " header -- the bug this
        module's per-line design specifically fixes (see `_bidi_space_and_isolate`'s docstring)."""
        text = "עובדה עם ציטוט [2]\n\n### הקשר"
        out = _bidi_space_and_isolate(text)
        assert out.endswith("### הקשר")
        assert "### " not in out.split("\n\n")[0]  # header text never merged onto the prior line


class TestFormatInvestigationAnswerHe:
    def test_empty_input_returns_empty(self) -> None:
        assert format_investigation_answer_he("") == ""

    def test_plain_fallback_message_passes_through_unchanged(self) -> None:
        """The not_found/no-answer fallback strings built in `_finalize_outcome`/
        `investigate_batch_cloud` are pure Hebrew with no facts/sources -- must render as just
        that one sentence, no empty section headers."""
        msg = "לא נמצא מידע מספק במסגרת התקציב."
        assert format_investigation_answer_he(msg) == msg

    def test_sections_appear_in_fixed_order_and_skip_when_empty(self) -> None:
        out = format_investigation_answer_he(
            "תשובה ישירה כלשהי.",
            key_facts=["עובדה שונה לגמרי [1]"],
            contradictions_he="",  # no gaps to report -- section must not appear
        )
        facts_pos = out.index("עובדות מרכזיות")
        assert out.index("תשובה ישירה כלשהי.") < facts_pos
        assert "פערים" not in out  # contradictions_he was empty -> section skipped
        assert "הקשר" not in out  # no second paragraph -> section skipped

    def test_second_paragraph_becomes_context_section(self) -> None:
        out = format_investigation_answer_he("פסקה ראשונה.\n\nפסקה שנייה עם הקשר נוסף.")
        assert "### הקשר" in out
        assert "פסקה שנייה עם הקשר נוסף." in out.split("### הקשר", 1)[1]

    def test_no_sources_argument_and_no_sources_section_is_ever_emitted(self) -> None:
        """CR-invest.md: `InvestigationOut.sources` is a separate, already-structured field the
        UI renders on its own -- `format_investigation_answer_he` must never build a "### מקורות"
        block inside `answer_he` itself, even when the model's own prose mentions a URL, and no
        longer accepts a `sources` argument at all (the old ground-truth-only מקורות block this
        replaced is gone, not just re-sourced)."""
        out = format_investigation_answer_he(
            "התשובה מבוססת על https://not-a-real-source.example.",
            key_facts=["עובדה נבדלת שאינה מוזכרת בפסקה [1]"],
            contradictions_he="פער כלשהו.",
        )
        assert "מקורות" not in out
        with pytest.raises(TypeError):
            format_investigation_answer_he("טקסט", sources=["https://example.com/a"])  # type: ignore[call-arg]

    def test_key_fact_restating_the_direct_prose_is_dropped(self) -> None:
        """CR-invest.md (job 175): a key_facts bullet that mostly repeats a sentence already in
        the direct-answer paragraph must not also appear as a bullet -- the exact "same facts
        twice" bug the content review flagged."""
        direct = "העדשה מציעה טווח זום רציף של 15-300 מ\"מ עם פתיחת עדשה קבועה של f/4."
        out = format_investigation_answer_he(
            direct,
            key_facts=[
                "העדשה מציעה טווח זום רציף של 15-300 מ\"מ עם פתיחת עדשה קבועה של f/4 [1,2]",
                "העדשה כוללת מנגנון סגירת תריס מכני (NUC shutter) לשמירה על איכות התמונה [1,2]",
            ],
        )
        assert "עובדות מרכזיות" in out
        assert "מנגנון סגירת תריס מכני" in out  # the genuinely new fact survives
        # the restating bullet is gone; its distinctive tail ("NUC shutter" bullet is unrelated)
        # must not appear a second time as a "- " bullet line.
        bullet_lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
        assert len(bullet_lines) == 1
        assert "מנגנון סגירת תריס מכני" in bullet_lines[0]

    def test_direct_prose_capped_at_max_sentences(self) -> None:
        sentences = [f"משפט מספר {i} בפרוזה הישירה." for i in range(1, 10)]
        out = format_investigation_answer_he(" ".join(sentences))
        assert "משפט מספר 6" in out
        assert "משפט מספר 7" not in out
        assert "משפט מספר 9" not in out

    def test_context_paragraph_capped_at_max_sentences_independently_of_direct(self) -> None:
        direct = " ".join(f"ישיר {i}." for i in range(1, 8))
        context = " ".join(f"הקשר {i}." for i in range(1, 8))
        out = format_investigation_answer_he(f"{direct}\n\n{context}")
        direct_block, context_block = out.split("### הקשר", 1)
        assert "ישיר 6" in direct_block and "ישיר 7" not in direct_block
        assert "הקשר 6" in context_block and "הקשר 7" not in context_block

    def test_run_on_sample_produces_readable_structured_output(self) -> None:
        """A deliberately run-on, unpunctuated-transition, bidi-messy sample -- the kind of raw
        model output W27 exists to clean up."""
        raw = (
            "Elbit Systemsזכתה בחוזה של 650M$ מול חיל האוויר האמריקאי לאספקת Targeting Podחדש "
            "והחוזה כולל תמיכה טכנית לאורך 10שנים\n\n"
            "התחרות כללה גם את Rafaelו-L3Harrisשהציעו הצעות מחיר נמוכות יותר"
        )
        out = format_investigation_answer_he(
            raw,
            key_facts=["ערך החוזה 650 מיליון דולר [1]", "תקופת האספקה 10 שנים [1]"],
            contradictions_he="לא צוין מועד המסירה הראשון של המערכת.",
        )
        # Structural checks: fixed section order, all present given the rich input above; no
        # מקורות section (CR-invest.md -- sources are never part of this text any more).
        for marker in ("עובדות מרכזיות", "הקשר", "פערים / מה לא ידוע"):
            assert f"### {marker}" in out
        assert "מקורות" not in out
        order = [out.index(f"### {m}") for m in ("עובדות מרכזיות", "הקשר", "פערים / מה לא ידוע")]
        assert order == sorted(order)
        # No dangling/unterminated section: every "### " line is followed by non-empty content.
        for block in out.split("\n\n"):
            if block.startswith("### "):
                assert len(block.splitlines()) > 1
        # The reported bidi bug is fixed: no Latin letter/digit sits directly against a Hebrew
        # letter anywhere in the final text (every such boundary must have a space between them).
        import re

        hebrew_re = re.compile(r"[֐-׿]")
        latin_re = re.compile(r"[A-Za-z0-9]")
        for i in range(len(out) - 1):
            a, b = out[i], out[i + 1]
            if (latin_re.match(a) and hebrew_re.match(b)) or (hebrew_re.match(a) and latin_re.match(b)):
                raise AssertionError(f"unspaced bidi boundary at {i}: ...{out[max(0, i - 10) : i + 10]!r}...")


class TestJob175Fixture:
    """CR-invest.md: job 175's stored ``answer_he`` (docs/qa/content_review/CR-invest.md) is the
    exact case the user reported ("look at the poor language of the report") -- literal LRI/PDI
    isolate characters (U+2066/U+2069) embedded mid-word, stray ``\\"`` backslash-escapes that
    leaked from JSON, and `key_facts` bullets that near-verbatim repeat the direct-answer prose.

    These fixtures reconstruct the *pre-assembly* inputs (the direct-answer paragraph and
    `key_facts`/`contradictions_he` as the model would have produced them, artifacts and all --
    `format_investigation_answer_he` runs on raw model output, not on its own already-assembled
    text) from job 175's DB row, to prove the current assembly logic actually cleans this exact
    real-world sample rather than only synthetic examples above."""

    #: Job 175's direct-answer paragraph, verbatim (isolate marks and stray backslash-quotes
    #: included) -- the first paragraph of the stored `answer_he`, before its own
    #: "### עובדות מרכזיות" heading.
    DIRECT_HE = (
        "המוצר החדש, ⁦Ophir® SupIR-X, ⁩הוא עדשת זום מוטורית רציפה "
        "(⁦Continuous Zoom⁩) בטווח ⁦15-300 ⁩מ\\\"מ ובעדשה קבועה ⁦f/4, "
        "⁩המיועדת ספציפית לגלאי ⁦MWIR ⁩מסוג ⁦10 µm SXGA. ⁩העדשה מיוצרת "
        "על ידי חברת ⁦Ophir Optronics (⁩שייכת לקונצרניט ⁦MKS Instruments) "
        "⁩ומיועדת למשימות ⁦ISR (⁩מודיעין, תצפית וסימון⁦) ⁩במרחקים "
        "ארוכים באוויר, ביבשה ובים. המערכת מאפשרת זיהוי כלי רכב מעבר ל-⁦26 ⁩ק\\\"מ "
        "וניתנת להרחבה (⁦Scalability⁩) עד למרחק מוקד של ⁦1200 ⁩מ\\\"מ "
        "באמצעות מתאמי המערכת של ⁦Ophir.⁩"
    )

    #: Job 175's `key_facts`, verbatim -- every one of these restates a clause already present in
    #: `DIRECT_HE` above except the NUC-shutter and air/land/sea-usage bullets.
    KEY_FACTS: ClassVar = [
        "העדשה מיועדת לגלאי MWIR מסוג 10 µm SXGA המיועדים למשימות ISR [1]",
        'העדשה מציעה טווח זום רציף של 15-300 מ\\"מ עם פתיחת עדשה קבועה של f/4 [1,2]',
        'העדשה תומכת בהרחבה (Scalability) עד ל-1200 מ"מ באמצעות מתאמי המערכת של Ophir [1,2]',
        'העדשה מאפשרת זיהוי כלי רכב מעבר ל-26 ק"מ בתנאי שטח סטנדרטיים [1,2]',
        "המוצר מיוצר על ידי Ophir Optronics, חברה של קונצרניט MKS Instruments [1,2]",
        "העדשה כוללת מנגנון סגירת תריס מכני (NUC shutter) לשמירה על איכות התמונה [1,2]",
        "העדשה מיועדת לשימוש באוויר, ביבשה וביים [1,2]",
    ]

    CONTRADICTIONS_HE = (
        "אין נתונים ספציפיים על סכומי חוזה, לקוחות ספציפיים או לוחות זמנים מסחריים "
        '(הדיווחים הם על השקת המוצר). אין אישור ישיר על קשר מסחרי עם תע\\"א, אך המוצר '
        "מיועד למשימות המוגדרות כליבת פעילותה."
    )

    def test_no_bidi_isolate_characters_survive(self) -> None:
        out = format_investigation_answer_he(self.DIRECT_HE, key_facts=self.KEY_FACTS)
        assert "⁦" not in out and "⁩" not in out

    def test_stray_backslash_quotes_become_gershayim(self) -> None:
        out = format_investigation_answer_he(self.DIRECT_HE)
        assert '\\"' not in out
        assert "מ״מ" in out  # U+05F4 GERSHAYIM, the correct mark for "מ"מ"

    def test_restating_key_facts_are_dropped_genuinely_new_ones_kept(self) -> None:
        out = format_investigation_answer_he(self.DIRECT_HE, key_facts=self.KEY_FACTS)
        bullet_lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
        # Job 175 showed all 7 facts as bullets, every one a near-repeat of the prose; only the
        # two genuinely additional facts (NUC shutter, air/land/sea usage) should survive.
        assert len(bullet_lines) < len(self.KEY_FACTS)
        assert any("NUC shutter" in ln for ln in bullet_lines)
        assert any("באוויר, ביבשה" in ln for ln in bullet_lines)
        assert not any("מסוג 10" in ln and "SXGA" in ln for ln in bullet_lines)  # pure repeat, dropped

    def test_no_sources_section_and_gaps_section_present(self) -> None:
        out = format_investigation_answer_he(
            self.DIRECT_HE, key_facts=self.KEY_FACTS, contradictions_he=self.CONTRADICTIONS_HE
        )
        assert "מקורות" not in out
        assert "### פערים / מה לא ידוע" in out
        assert "לוחות זמנים מסחריים" in out.split("### פערים / מה לא ידוע", 1)[1]

    def test_no_unspaced_bidi_boundary_in_final_output(self) -> None:
        out = format_investigation_answer_he(
            self.DIRECT_HE, key_facts=self.KEY_FACTS, contradictions_he=self.CONTRADICTIONS_HE
        )
        hebrew_re = re.compile(r"[֐-׿]")
        latin_re = re.compile(r"[A-Za-z0-9]")
        for i in range(len(out) - 1):
            a, b = out[i], out[i + 1]
            if (latin_re.match(a) and hebrew_re.match(b)) or (hebrew_re.match(a) and latin_re.match(b)):
                raise AssertionError(f"unspaced bidi boundary at {i}: ...{out[max(0, i - 10) : i + 10]!r}...")
