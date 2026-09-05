"""Unit tests for Q3-1 (docs/qa/findings_Q3_r1.md): the Hebrew-acronym truncation guard added to
``eoa.llm.ollama_client``'s ``chat_structured`` post-validation.

Covers the pure detection/normalisation helpers directly (no Ollama/HTTP involved), plus
``_guard_hebrew_truncation``'s retry-then-accept behavior with a stubbed ``_structured_once``.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_hebrew_truncation_guard.py -q``
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from eoa.errors import LLMOutputError
from eoa.llm import ollama_client as oc


class _Inner(BaseModel):
    evidence_he: str


class _Sample(BaseModel):
    summary_he: str = ""
    relevance_note: str = ""  # English field, never flagged even with Hebrew edge cases
    key_facts: list[str] = []
    inner: list[_Inner] = []


class TestLooksTruncated:
    @pytest.mark.parametrize(
        "text",
        [
            "פעולה של הכוח נגד כטב",  # positive: ends mid-acronym stem "כטב" (כטב"ם)
            "הודעה רשמית של מטע",  # positive: stem "מטע" (מטע"ד)
            "הפעולה בוצעה על ידי תע",  # positive: stem "תע" (תע"א)
        ],
    )
    def test_positive_truncated_stem(self, text: str) -> None:
        assert oc._looks_truncated_mid_hebrew_acronym(text, "summary_he") is True

    @pytest.mark.parametrize(
        "text",
        [
            "זהו משפט שלם ומלא בעברית.",
            "האם זה משפט שלם?",
            "This is a complete English sentence.",
            "",
        ],
    )
    def test_negative_complete_or_non_hebrew(self, text: str) -> None:
        assert oc._looks_truncated_mid_hebrew_acronym(text, "summary_he") is False

    def test_negative_gershayim_terminated(self) -> None:
        """A correctly-written acronym using the real gershayim ״ is never flagged."""
        assert oc._looks_truncated_mid_hebrew_acronym("הכוח פעל נגד כטב״ם.", "summary_he") is False

    def test_generic_he_field_long_without_punctuation(self) -> None:
        """A long `*_he` field with no terminal punctuation is suspect even without a known stem."""
        text = "זהו טקסט ארוך בעברית שנקטע באמצע המשפט בלי שום סימן פיסוק בסופו לגמרי"
        assert oc._looks_truncated_mid_hebrew_acronym(text, "summary_he") is True

    def test_generic_check_does_not_apply_to_non_he_fields(self) -> None:
        """The length-based generic net only applies to fields literally named `*_he`."""
        text = "זהו טקסט ארוך בעברית שנקטע באמצע המשפט בלי שום סימן פיסוק בסופו לגמרי"
        assert oc._looks_truncated_mid_hebrew_acronym(text, "relevance_note") is False

    def test_short_generic_he_field_not_flagged(self) -> None:
        assert oc._looks_truncated_mid_hebrew_acronym("קצר מדי", "summary_he") is False


class TestNormalizeHebrewQuotes:
    def test_replaces_ascii_quote_between_hebrew_letters(self) -> None:
        assert oc._normalize_hebrew_quotes('כטב"ם') == "כטב״ם"

    def test_multiple_occurrences(self) -> None:
        result = oc._normalize_hebrew_quotes('כטב"ם וגם מטע"ד')
        assert '"' not in result
        assert result.count("״") == 2

    def test_leaves_non_hebrew_quotes_untouched(self) -> None:
        text = 'The "quoted" English text stays as-is.'
        assert oc._normalize_hebrew_quotes(text) == text

    def test_leaves_already_correct_gershayim_untouched(self) -> None:
        text = "כטב״ם"
        assert oc._normalize_hebrew_quotes(text) == text


class TestIterModelStringsAndSuspects:
    def test_finds_top_level_and_nested_and_list_strings(self) -> None:
        model = _Sample(
            summary_he="פעולה נגד כטב",
            relevance_note="fine",
            key_facts=["עובדה תקינה.", "עובדה נוספת נגד מטע"],
            inner=[_Inner(evidence_he="ראיה נגד תע")],
        )
        suspects = oc._find_truncation_suspects(model)
        # summary_he, one of the key_facts entries, and the nested evidence_he all flagged.
        assert "summary_he" in suspects
        assert "key_facts" in suspects
        assert "evidence_he" in suspects
        assert "relevance_note" not in suspects

    def test_no_suspects_for_clean_model(self) -> None:
        model = _Sample(
            summary_he="משפט שלם ותקין.",
            relevance_note="fine",
            key_facts=["עובדה אחת.", "עובדה שתיים."],
            inner=[_Inner(evidence_he="ראיה תקינה לגמרי.")],
        )
        assert oc._find_truncation_suspects(model) == []

    def test_normalize_model_hebrew_quotes_mutates_in_place(self) -> None:
        model = _Sample(summary_he='כטב"ם פעל', key_facts=['מטע"ד תקין.'])
        fixed = oc._normalize_model_hebrew_quotes(model)
        assert fixed is model
        assert model.summary_he == "כטב״ם פעל"
        assert model.key_facts[0] == "מטע״ד תקין."


class TestGuardHebrewTruncation:
    def test_no_suspects_returns_normalized_without_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called = []
        monkeypatch.setattr(oc, "_structured_once", lambda *a, **k: called.append(1) or (None, None))

        model = _Sample(summary_he='כטב"ם פעל אתמול.')  # already terminated (period) -- not suspect
        result = oc._guard_hebrew_truncation(
            "resident", _Sample, [], model, task="classify", interactive=False, options=None, provider=None
        )
        assert called == []
        assert result.summary_he == "כטב״ם פעל אתמול."

    def test_suspect_triggers_one_retry_and_accepts_fixed_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fixed = _Sample(summary_he="הכוח פעל נגד כטב״ם בהצלחה.")

        def fake_structured_once(role, schema, messages, **kwargs):
            assert any("חתוכים" in m.get("content", "") for m in messages if m["role"] == "user")
            return fixed, None

        monkeypatch.setattr(oc, "_structured_once", fake_structured_once)

        model = _Sample(summary_he="הכוח פעל נגד כטב")  # truncated
        result = oc._guard_hebrew_truncation(
            "resident", _Sample, [{"role": "user", "content": "x"}], model,
            task="classify", interactive=False, options=None, provider=None,
        )
        assert result is fixed
        assert result.summary_he.endswith("בהצלחה.")

    def test_retry_still_suspect_is_accepted_and_normalized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        still_bad = _Sample(summary_he='עדיין נקטע נגד מטע"ד')  # ends with punctuation-free ASCII-quote form

        monkeypatch.setattr(oc, "_structured_once", lambda *a, **k: (still_bad, None))

        model = _Sample(summary_he="הכוח פעל נגד כטב")
        result = oc._guard_hebrew_truncation(
            "resident", _Sample, [{"role": "user", "content": "x"}], model,
            task="classify", interactive=False, options=None, provider=None,
        )
        # accepted (no exception), and the ASCII quote got normalized regardless
        assert "״" in result.summary_he
        assert '"' not in result.summary_he

    def test_retry_failure_falls_back_to_original_normalized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_llm_error(*a, **k):
            raise LLMOutputError("boom")

        monkeypatch.setattr(oc, "_structured_once", raise_llm_error)

        model = _Sample(summary_he='נגד כטב')
        result = oc._guard_hebrew_truncation(
            "resident", _Sample, [{"role": "user", "content": "x"}], model,
            task="classify", interactive=False, options=None, provider=None,
        )
        assert result is model  # fell back to original (normalization is a no-op here, no quotes)
