"""Tests for Q3-10 (docs/qa/findings_Q3_r1.md): eoa.fetch.content_quality.assess and its wiring
into eoa.pipeline.analyze's pre-check.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_content_quality.py -q``
"""

from __future__ import annotations

import pytest

from eoa.fetch.content_quality import PARTIAL_MAX_CHARS, STUB_MAX_CHARS, assess
from eoa.pipeline.analyze import (
    PARTIAL_CONTENT_UNCERTAINTY_NOTE_HE,
    _analyze_prompt,
    _content_status_precheck,
    _with_partial_content_note,
)


class TestAssess:
    def test_none_text_is_stub(self) -> None:
        assert assess(None) == "stub"

    def test_empty_text_is_stub(self) -> None:
        assert assess("   ") == "stub"

    def test_blocked_status_is_always_stub(self) -> None:
        assert assess("a" * 5000, status="blocked") == "stub"

    def test_short_text_is_stub(self) -> None:
        assert assess("a" * (STUB_MAX_CHARS - 1)) == "stub"

    def test_medium_text_is_partial(self) -> None:
        assert assess("a" * (STUB_MAX_CHARS + 1)) == "partial"
        assert assess("a" * PARTIAL_MAX_CHARS) == "partial"

    def test_long_text_is_full(self) -> None:
        assert assess("a" * (PARTIAL_MAX_CHARS + 1)) == "full"

    def test_paywall_phrase_short_is_stub(self) -> None:
        assert assess("Subscribe to continue reading this article.") == "stub"

    def test_paywall_phrase_long_is_partial_not_full(self) -> None:
        text = ("Real article content. " * 200) + " Subscribe to continue reading."
        assert len(text) > PARTIAL_MAX_CHARS
        assert assess(text) == "partial"

    def test_hebrew_paywall_phrase_detected(self) -> None:
        assert assess("תוכן קצר. רק למנויים.") == "stub"

    def test_html_len_never_demotes_a_comfortably_long_extraction(self) -> None:
        """Regression: an earlier version used a raw-HTML-to-text ratio heuristic here, which
        misclassified ordinary complete articles as 'partial' on this project's own real data
        (modern news pages routinely have a 1.5-2.5% text/HTML ratio with nothing wrong with the
        extraction) -- html_len must never turn a long, phrase-clean text into anything but
        'full', regardless of how large it is."""
        text = "a" * (PARTIAL_MAX_CHARS + 100)
        assert assess(text, html_len=200_000) == "full"
        assert assess(text, html_len=10_000_000) == "full"

    def test_normal_long_text_no_html_hint_is_full(self) -> None:
        assert assess("a" * (PARTIAL_MAX_CHARS + 100), html_len=0) == "full"


class TestContentStatusPrecheck:
    def test_persists_status_when_changed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = []
        monkeypatch.setattr(
            "eoa.pipeline.analyze.update_item_fields", lambda item_id, **kw: calls.append((item_id, kw))
        )
        it = {"id": 1, "clean_text": "short", "content_status": "full"}
        status = _content_status_precheck(it)
        assert status == "stub"
        assert calls == [(1, {"content_status": "stub"})]
        assert it["content_status"] == "stub"

    def test_no_write_when_status_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = []
        monkeypatch.setattr(
            "eoa.pipeline.analyze.update_item_fields", lambda item_id, **kw: calls.append((item_id, kw))
        )
        it = {"id": 1, "clean_text": "a" * 5000, "content_status": "full"}
        status = _content_status_precheck(it)
        assert status == "full"
        assert calls == []


class TestPartialContentPromptAndUncertainty:
    def test_prompt_includes_partial_note(self) -> None:
        item = {"id": 1, "content_status": "partial", "clean_text": "x", "url": "https://x"}
        prompt = _analyze_prompt(item)
        assert "ייתכן שתוכן הפריט חלקי" in prompt

    def test_prompt_omits_note_for_full_content(self) -> None:
        item = {"id": 1, "content_status": "full", "clean_text": "x", "url": "https://x"}
        prompt = _analyze_prompt(item)
        assert "ייתכן שתוכן הפריט חלקי" not in prompt

    def test_uncertainty_note_added_when_missing(self) -> None:
        item = {"content_status": "partial"}
        result = _with_partial_content_note(item, None)
        assert result == PARTIAL_CONTENT_UNCERTAINTY_NOTE_HE

    def test_uncertainty_note_prepended_to_existing(self) -> None:
        item = {"content_status": "partial"}
        result = _with_partial_content_note(item, "עוד אי-ודאות")
        assert result.startswith(PARTIAL_CONTENT_UNCERTAINTY_NOTE_HE)
        assert "עוד אי-ודאות" in result

    def test_uncertainty_note_not_doubled(self) -> None:
        item = {"content_status": "partial"}
        existing = f"{PARTIAL_CONTENT_UNCERTAINTY_NOTE_HE}. עוד"
        result = _with_partial_content_note(item, existing)
        assert result == existing

    def test_full_content_uncertainty_untouched(self) -> None:
        item = {"content_status": "full"}
        assert _with_partial_content_note(item, "x") == "x"
        assert _with_partial_content_note(item, None) is None


class TestRunAnalyzeSkipsStubContent:
    """Q3-10 end-to-end within run_analyze: a 'stub'-content item is marked done (never retried)
    but never sent to the LLM; a 'partial'-content item still gets analyzed."""

    def test_stub_item_never_reaches_analyze_item(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import eoa.pipeline.analyze as az

        marked: list[int] = []
        analyzed: list[int] = []
        monkeypatch.setattr(az, "is_cloud_batch_mode", lambda: False)
        monkeypatch.setattr(
            az,
            "get_items_for_stage",
            lambda stage, limit: [
                {"id": 1, "level": "red", "clean_text": "short", "security_status": "clean"},
                {"id": 2, "level": "red", "clean_text": "a" * 5000, "security_status": "clean"},
            ],
        )
        monkeypatch.setattr(az, "mark_stage", lambda item_id, stage: marked.append(item_id))
        monkeypatch.setattr(az, "update_item_fields", lambda item_id, **kw: None)

        def fake_analyze_item(it, role="resident"):
            analyzed.append(it["id"])
            return az.AnalyzeOut(summary_he="s", so_what_he="להערכתנו x", key_facts=[], events=[], edges=[])

        monkeypatch.setattr(az, "analyze_item", fake_analyze_item)
        monkeypatch.setattr(az, "insert_event", lambda **kw: 1)
        monkeypatch.setattr(az, "upsert_entity", lambda **kw: 1)
        monkeypatch.setattr(
            "eoa.pipeline.entity_relevance.score_and_persist_entity", lambda name: None, raising=False
        )

        stats = az.run_analyze()

        assert 1 in marked  # stub item's stage marked done without analysis
        assert 1 not in analyzed  # ...and never sent to the LLM
        assert 2 in analyzed  # the full-content item was analyzed normally
        assert stats.done == 1
