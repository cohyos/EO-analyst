"""Unit tests for eoa.report.claims_gate (CR-monthly.md item 3, 2026-09-08 user feedback on
output/reports/monthly_2026-09-30.md): a sentence carrying an evaluative/intensifier phrase with no
supporting quantity in the SAME sentence must be softened or dropped, deterministically, no LLM.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_claims_gate.py -q``
"""

from __future__ import annotations

from eoa.llm.schemas.analysis import AnalystNote, Sentence
from eoa.report import claims_gate as cg

# --------------------------------------------------------------------------
# gate_text: the three live-report fixtures named in the task
# --------------------------------------------------------------------------


def test_niker_hitatzmut_dramatit_softened_to_the_exact_expected_rewrite():
    """The task's own worked example: 'ניכרת התעצמות דרמטית ברכש' -> 'נרשמו דיווחים על רכש'."""
    text = "החודש ניכרת התעצמות דרמטית ברכש מערכות הגנה אווירית"
    out = cg.gate_text(text)
    assert out == "החודש נרשמו דיווחים על רכש מערכות הגנה אווירית"


def test_mesamnot_kfitzat_madrega_softened_when_no_quantity():
    text = "מסמנות קפיצת מדרגה בשוק ה-EO/IR"
    out = cg.gate_text(text)
    assert out is not None
    assert "קפיצת מדרגה" not in out
    assert "מסמנות" not in out


def test_matzbia_bevirur_haadafa_holekhet_vegoveret_dropped_as_vacuous():
    """Both trigger phrases removed leaves nothing evidentiary -- the whole sentence is dropped."""
    text = "החודש מצביע בבירור על העדפה הולכת וגוברת"
    assert cg.gate_text(text) is None


# --------------------------------------------------------------------------
# quantity-present sentences are never touched, even when they use a trigger word
# --------------------------------------------------------------------------


def test_sentence_with_percent_and_intensifier_is_left_alone():
    text = "נרשמה עלייה חדה של 40 אחוז ברכש מל\"טים"
    assert cg.gate_text(text) == text


def test_sentence_with_item_count_and_intensifier_is_left_alone():
    text = "מגמה זו נתמכת ב-7 פריטים מ-3 מקורות שונים"
    assert cg.gate_text(text) == text


def test_sentence_with_explicit_baseline_comparison_is_left_alone():
    text = "נרשמה תאוצה בתחום זה לעומת הממוצע החודשי"
    assert cg.gate_text(text) == text


# --------------------------------------------------------------------------
# sentences with no trigger word at all pass through unchanged regardless of quantity
# --------------------------------------------------------------------------


def test_plain_factual_sentence_unaffected():
    text = "רפאל זכתה בחוזה לאספקת מערכות הגנה אווירית."
    assert cg.gate_text(text) == text


def test_empty_and_none_text_pass_through():
    assert cg.gate_text("") == ""
    assert cg.gate_text(None) is None


# --------------------------------------------------------------------------
# gate_sentences / gate_plain_strings: object-level wiring, cites untouched
# --------------------------------------------------------------------------


def test_gate_sentences_drops_vacuous_sentence_and_keeps_its_neighbor():
    sentences = [
        Sentence(text_he="רפאל זכתה בחוזה חדש.", cites=[1]),
        Sentence(text_he="החודש מצביע בבירור על העדפה הולכת וגוברת", cites=[2]),
    ]
    out, result = cg.gate_sentences(sentences, context="test")
    assert len(out) == 1
    assert out[0].text_he == "רפאל זכתה בחוזה חדש."
    assert result.dropped == 1
    assert result.softened == 0


def test_gate_sentences_softens_without_touching_cites():
    sentences = [Sentence(text_he="מסמנות קפיצת מדרגה בשוק ה-EO/IR", cites=[5, 6])]
    out, result = cg.gate_sentences(sentences, context="test")
    assert len(out) == 1
    assert out[0].cites == [5, 6]
    assert out[0].text_he != sentences[0].text_he
    assert result.softened == 1


def test_gate_plain_strings_handles_analyst_note_sentences():
    strings = ["ניכרת התעצמות דרמטית ברכש", "רפאל זכתה בחוזה."]
    out, result = cg.gate_plain_strings(strings, context="test")
    assert len(out) == 2
    assert result.softened == 1
    assert out[1] == "רפאל זכתה בחוזה."


# --------------------------------------------------------------------------
# apply_claims_gate: end-to-end over a duck-typed draft object
# --------------------------------------------------------------------------


class _FakeTrend:
    def __init__(self, title_he: str, sentences: list[Sentence]) -> None:
        self.title_he = title_he
        self.sentences = sentences

    def model_copy(self, update: dict[str, object]) -> _FakeTrend:
        merged = {"title_he": self.title_he, "sentences": self.sentences}
        merged.update(update)
        return _FakeTrend(merged["title_he"], merged["sentences"])


class _FakeSection:
    def __init__(self, title_he: str, sentences: list[Sentence]) -> None:
        self.title_he = title_he
        self.sentences = sentences

    def model_copy(self, update: dict[str, object]) -> _FakeSection:
        merged = {"title_he": self.title_he, "sentences": self.sentences}
        merged.update(update)
        return _FakeSection(merged["title_he"], merged["sentences"])


class _FakeDraft:
    def __init__(self, **kwargs: object) -> None:
        self.bluf: list[Sentence] = kwargs.get("bluf", [])
        self.exec_summary: list[Sentence] = kwargs.get("exec_summary", [])
        self.analyst_note_he: AnalystNote | None = kwargs.get("analyst_note_he")
        self.trends: list[_FakeTrend] = kwargs.get("trends", [])
        self.sections: list[_FakeSection] = kwargs.get("sections", [])

    def model_copy(self, update: dict[str, object]) -> _FakeDraft:
        state = {
            "bluf": self.bluf,
            "exec_summary": self.exec_summary,
            "analyst_note_he": self.analyst_note_he,
            "trends": self.trends,
            "sections": self.sections,
        }
        state.update(update)
        return _FakeDraft(**state)


def test_apply_claims_gate_walks_every_target_field():
    draft = _FakeDraft(
        exec_summary=[Sentence(text_he="ניכרת התעצמות דרמטית ברכש מערכות.", cites=[1])],
        analyst_note_he=AnalystNote(sentences_he=["החודש מצביע בבירור על העדפה הולכת וגוברת"]),
        trends=[_FakeTrend("מגמה", [Sentence(text_he="מסמנות קפיצת מדרגה בשוק", cites=[2])])],
        sections=[_FakeSection("סעיף", [Sentence(text_he="רפאל זכתה בחוזה חדש.", cites=[3])])],
    )
    gated, result = cg.apply_claims_gate(draft, "monthly")
    assert result.softened == 1  # exec_summary
    assert result.dropped == 2  # analyst note + trend sentence, both vacuous once softened
    assert gated.trends[0].sentences == []
    # the untouched section sentence stays exactly as-is
    assert gated.sections[0].sentences[0].text_he == "רפאל זכתה בחוזה חדש."
    # citation markers are never touched
    assert gated.exec_summary[0].cites == [1]
    assert gated.sections[0].sentences[0].cites == [3]


def test_apply_claims_gate_no_targets_present_is_a_no_op():
    draft = _FakeDraft()
    gated, result = cg.apply_claims_gate(draft, "weekly")
    assert result.softened == 0
    assert result.dropped == 0
    assert gated is draft


# --------------------------------------------------------------------------
# F17 (SOL-AUDIT-2026-09-24): gate_item_texts / gate_deep_search_entries must keep a fully
# rejected (empty) softening result -- not silently fall back to the original unsupported text.
# --------------------------------------------------------------------------


def test_gate_item_texts_keeps_fully_rejected_summary_as_falsy():
    items = [
        {
            "id": 1,
            # entirely vacuous once the trigger phrases are stripped -- gate_text -> None
            "summary_he": "החודש מצביע בבירור על העדפה הולכת וגוברת",
            "so_what_he": "רפאל זכתה בחוזה חדש.",
        }
    ]
    out = cg.gate_item_texts(items)
    assert not out[0]["summary_he"]  # None or "" -- never the original unsupported claim
    assert "הולכת וגוברת" not in (out[0]["summary_he"] or "")
    assert out[0]["so_what_he"] == "רפאל זכתה בחוזה חדש."  # untouched, no trigger word


def test_gate_deep_search_entries_keeps_fully_rejected_answer_as_falsy():
    entries = [
        {
            "answer_he": "החודש מצביע בבירור על העדפה הולכת וגוברת",
            "contradictions_he": "רפאל זכתה בחוזה חדש.",
        }
    ]
    out = cg.gate_deep_search_entries(entries)
    assert not out[0]["answer_he"]
    assert out[0]["contradictions_he"] == "רפאל זכתה בחוזה חדש."


def test_gate_item_texts_softens_without_restoring_original_when_partially_rejected():
    items = [{"id": 1, "summary_he": "מסמנות קפיצת מדרגה בשוק ה-EO/IR"}]
    out = cg.gate_item_texts(items)
    assert out[0]["summary_he"] is not None
    assert out[0]["summary_he"] != "מסמנות קפיצת מדרגה בשוק ה-EO/IR"
    assert "קפיצת מדרגה" not in out[0]["summary_he"]
