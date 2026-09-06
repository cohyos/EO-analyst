"""Unit tests for scripts/mark_legacy_investigations.py's pure transforms (Q3-5, docs/qa/
findings_Q3_r2.md). No DB -- only ``apply_legacy_label``/``apply_not_found_to_partial``.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_mark_legacy_investigations.py -q``
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

if "eoa.db" not in sys.modules:
    try:
        import eoa.db  # noqa: F401
    except ImportError:
        fake_db = types.ModuleType("eoa.db")
        fake_db.connection = lambda: None  # type: ignore[attr-defined]
        fake_db.get_pool = lambda: None  # type: ignore[attr-defined]
        sys.modules["eoa.db"] = fake_db

_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "mark_legacy_investigations.py"
_spec = importlib.util.spec_from_file_location("mark_legacy_investigations", _SCRIPT_PATH)
mli = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(mli)


class TestApplyLegacyLabel:
    def test_appends_note_and_sets_flag(self) -> None:
        result, changed = mli.apply_legacy_label({"outcome": "not_found", "what_was_tried_he": "3 שאילתות."})
        assert changed is True
        assert result["legacy_no_sources"] is True
        assert result["what_was_tried_he"] == f"3 שאילתות.{mli.LEGACY_NOTE_HE}"

    def test_idempotent_on_already_labelled_result(self) -> None:
        already = {
            "outcome": "not_found",
            "what_was_tried_he": f"3 שאילתות.{mli.LEGACY_NOTE_HE}",
            "legacy_no_sources": True,
        }
        result, changed = mli.apply_legacy_label(already)
        assert changed is False
        assert result == already

    def test_handles_missing_what_was_tried_he(self) -> None:
        result, changed = mli.apply_legacy_label({"outcome": "not_found"})
        assert changed is True
        assert result["what_was_tried_he"] == mli.LEGACY_NOTE_HE

    def test_does_not_mutate_input(self) -> None:
        original = {"outcome": "not_found", "what_was_tried_he": "x"}
        mli.apply_legacy_label(original)
        assert "legacy_no_sources" not in original


class TestApplyNotFoundToPartial:
    def test_reclassifies_outcome_and_confidence(self) -> None:
        result = mli.apply_not_found_to_partial(
            {"outcome": "not_found", "confidence": 0.2, "sources": ["https://x"]}
        )
        assert result["outcome"] == "partial"
        assert result["confidence"] == mli.PARTIAL_CONFIDENCE_FALLBACK

    def test_leaves_other_fields_untouched(self) -> None:
        before = {"outcome": "not_found", "confidence": 0.1, "sources": ["https://x"], "answer_he": "תשובה"}
        result = mli.apply_not_found_to_partial(before)
        assert result["sources"] == before["sources"]
        assert result["answer_he"] == before["answer_he"]

    def test_does_not_mutate_input(self) -> None:
        original = {"outcome": "not_found", "confidence": 0.1}
        mli.apply_not_found_to_partial(original)
        assert original["outcome"] == "not_found"
