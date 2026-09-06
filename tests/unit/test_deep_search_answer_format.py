"""Unit tests for Round-4b W27 (docs/REVIEW_2026-09-06_evening.md): the deterministic
``format_investigation_answer_he`` assembly and its bidi-safe spacing/isolation pass.

Pure string-processing -- no DB, no Ollama, no network (CONVENTIONS.md rule 10).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_deep_search_answer_format.py -q``
"""

from __future__ import annotations

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
            key_facts=["עובדה אחת [1]"],
            contradictions_he="",  # no gaps to report -- section must not appear
            sources=["https://example.com/a"],
        )
        facts_pos = out.index("עובדות מרכזיות")
        sources_pos = out.index("מקורות")
        assert out.index("תשובה ישירה כלשהי.") < facts_pos < sources_pos
        assert "פערים" not in out  # contradictions_he was empty -> section skipped
        assert "הקשר" not in out  # no second paragraph -> section skipped

    def test_second_paragraph_becomes_context_section(self) -> None:
        out = format_investigation_answer_he("פסקה ראשונה.\n\nפסקה שנייה עם הקשר נוסף.")
        assert "### הקשר" in out
        assert "פסקה שנייה עם הקשר נוסף." in out.split("### הקשר", 1)[1]

    def test_sources_section_is_built_from_ground_truth_list_never_from_model_text(self) -> None:
        """Even if the model's own answer_he mentions a URL, the מקורות section must reflect only
        the `sources` argument -- ground-truth doctrine, same as `_finalize_outcome`'s
        `sources = list(inv.read_urls)` (Q3-5)."""
        out = format_investigation_answer_he(
            "התשובה מבוססת על https://not-a-real-source.example.",
            sources=["https://real-source.example/a", "https://real-source.example/b"],
        )
        sources_block = out.split("### מקורות", 1)[1]
        assert "real-source.example/a" in sources_block
        assert "real-source.example/b" in sources_block
        assert sources_block.count("- [") == 2

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
            sources=["https://example.com/contract-award"],
        )
        # Structural checks: fixed section order, all present given the rich input above.
        for marker in ("עובדות מרכזיות", "הקשר", "פערים / מה לא ידוע", "מקורות"):
            assert f"### {marker}" in out
        order = [out.index(f"### {m}") for m in ("עובדות מרכזיות", "הקשר", "פערים / מה לא ידוע", "מקורות")]
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
