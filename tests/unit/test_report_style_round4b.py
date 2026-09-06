"""Unit tests for Round-4b W26 (docs/REVIEW_2026-09-06_evening.md): the deterministic
executive-summary style guard in `eoa.report.style`.

Pure string-processing/pydantic-model checks -- no DB, no Ollama, no network (CONVENTIONS.md rule
10). Uses the real `Sentence`/`StructuredSection`/`AnalystNote` schemas from
`eoa.llm.schemas.analysis` so the duck-typed traversal in `apply_style_guard` is exercised against
actual report-draft shapes, not stand-ins.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_report_style_round4b.py -q``
"""

from __future__ import annotations

from eoa.llm.schemas.analysis import AnalystNote, DailyReportDraft, Sentence, StructuredSection
from eoa.report.style import (
    BANNED_FILLER_PHRASES_HE,
    MAX_WORDS_PER_SENTENCE_HE,
    apply_style_guard,
    check_sentence_length_he,
    strip_filler_phrases,
)


class TestStripFillerPhrases:
    def test_removes_named_examples_from_the_task(self) -> None:
        for phrase in ("יש לציין", "חשוב להדגיש", "בהקשר זה"):
            assert phrase in BANNED_FILLER_PHRASES_HE

    def test_no_op_when_nothing_matches(self) -> None:
        text = "החברה זכתה בחוזה בסך 100 מיליון דולר."
        cleaned, removed = strip_filler_phrases(text)
        assert cleaned == text
        assert removed == []

    def test_strips_filler_and_reports_it(self) -> None:
        cleaned, removed = strip_filler_phrases("יש לציין כי החברה זכתה בחוזה חדש.")
        assert "יש לציין" not in cleaned
        assert cleaned == "החברה זכתה בחוזה חדש."
        assert removed  # at least one phrase reported

    def test_strips_filler_in_the_middle_of_a_sentence(self) -> None:
        cleaned, removed = strip_filler_phrases("החברה, יש לציין כי, זכתה בחוזה.")
        assert "יש לציין" not in cleaned
        assert "  " not in cleaned
        assert removed

    def test_longest_phrase_wins_over_a_shorter_prefix(self) -> None:
        """ "יש לציין כי" must be removed whole, not leave a dangling "כי" behind from a shorter
        "יש לציין" match consuming only part of it."""
        cleaned, _ = strip_filler_phrases("יש לציין כי המכרז נפתח.")
        assert "כי" not in cleaned.split()  # no orphaned "כי" left as its own word
        assert cleaned == "המכרז נפתח."

    def test_empty_string_is_safe(self) -> None:
        assert strip_filler_phrases("") == ("", [])


class TestCheckSentenceLength:
    def test_short_sentence_is_none(self) -> None:
        assert check_sentence_length_he("החברה זכתה בחוזה.") is None

    def test_long_sentence_flagged_with_word_count(self) -> None:
        long_sentence = " ".join(["מילה"] * (MAX_WORDS_PER_SENTENCE_HE + 5))
        n = check_sentence_length_he(long_sentence)
        assert n == MAX_WORDS_PER_SENTENCE_HE + 5

    def test_exactly_at_limit_is_not_flagged(self) -> None:
        exactly_20 = " ".join(["מילה"] * MAX_WORDS_PER_SENTENCE_HE)
        assert check_sentence_length_he(exactly_20) is None


class TestApplyStyleGuardOnDailyDraft:
    def _draft(self, exec_summary_text: str, section_text: str) -> DailyReportDraft:
        return DailyReportDraft(
            exec_summary=[Sentence(text_he=exec_summary_text, cites=[1])],
            sections=[
                StructuredSection(
                    title_he="פודים אוויריים",
                    domain="airborne_pods",
                    sentences=[Sentence(text_he=section_text, cites=[2])],
                )
            ],
        )

    def test_strips_filler_from_exec_summary_sentence(self) -> None:
        draft = self._draft("יש לציין כי החברה זכתה בחוזה גדול.", "עובדה רגילה בסעיף.")
        updated, report = apply_style_guard(draft)
        assert "יש לציין" not in updated.exec_summary[0].text_he
        assert updated.exec_summary[0].cites == [1]  # cites untouched by a text-only edit
        assert any(v.kind == "filler_removed" for v in report.violations)

    def test_strips_filler_from_section_sentence(self) -> None:
        draft = self._draft("עובדה רגילה בתקציר.", "חשוב להדגיש שהמערכת חדשה.")
        updated, _report = apply_style_guard(draft)
        assert "חשוב להדגיש" not in updated.sections[0].sentences[0].text_he
        assert updated.sections[0].sentences[0].cites == [2]

    def test_clean_draft_is_returned_unchanged_with_no_violations(self) -> None:
        draft = self._draft("החברה זכתה בחוזה של 100 מיליון דולר.", "המערכת נבחנה בהצלחה.")
        updated, report = apply_style_guard(draft)
        assert updated.exec_summary[0].text_he == draft.exec_summary[0].text_he
        assert report.violations == []

    def test_long_sentence_is_flagged_not_rewritten(self) -> None:
        long_text = " ".join(["מילה"] * 25) + "."
        draft = self._draft(long_text, "עובדה רגילה.")
        updated, report = apply_style_guard(draft)
        # never rewritten/split -- only flagged (module docstring: detection-only for length)
        assert updated.exec_summary[0].text_he == long_text
        assert any(v.kind == "too_long" and v.field == "exec_summary" for v in report.violations)

    def test_duplicate_sentence_across_sections_is_flagged(self) -> None:
        same_text = "המערכת הוצגה לראשונה השבוע."
        draft = DailyReportDraft(
            exec_summary=[Sentence(text_he=same_text, cites=[1])],
            sections=[
                StructuredSection(
                    title_he="פודים אוויריים",
                    domain="airborne_pods",
                    sentences=[Sentence(text_he=same_text, cites=[1])],
                )
            ],
        )
        _updated, report = apply_style_guard(draft)
        assert any(v.kind == "duplicate_sentence" for v in report.violations)

    def test_analyst_note_filler_is_stripped(self) -> None:
        draft = DailyReportDraft(
            exec_summary=[Sentence(text_he="עובדה.", cites=[1])],
            analyst_note_he=AnalystNote(sentences_he=["ראוי לציין כי המגמה תימשך."]),
        )
        updated, _report = apply_style_guard(draft)
        assert "ראוי לציין" not in updated.analyst_note_he.sentences_he[0]

    def test_report_log_all_does_not_raise(self) -> None:
        draft = self._draft("יש לציין כי דבר מה קרה.", "עובדה.")
        _updated, report = apply_style_guard(draft, report_kind="daily", job_id=42)
        report.log_all(report_kind="daily", job_id=42)  # structlog call -- just must not raise
