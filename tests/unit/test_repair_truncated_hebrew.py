"""Unit tests for scripts/repair_truncated_hebrew.py's pure helpers (D1 round-1 fix,
docs/qa/loop/round_1_fixes.md, ``hebrew_truncation_zero_hits``). No DB, no LLM.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_repair_truncated_hebrew.py -q``
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

_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "repair_truncated_hebrew.py"
_spec = importlib.util.spec_from_file_location("repair_truncated_hebrew", _SCRIPT_PATH)
rth = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(rth)


class TestRowFlaggedFieldsTriageReason:
    """Regression for items 8/33/39/55/1091 (round-0 D1 finding, ``hebrew_truncation_zero_hits``):
    the repair script used to check ``triage_reason`` under its own literal column name, which
    never ends in ``"_he"`` -- silently skipping the generic "long sentence, no terminal
    punctuation" net that ``eoa.qa.d1_classify`` itself enables via the synthetic name
    ``"reason_he"``. These five items don't end on an exact known acronym stem, so only that
    generic net actually catches them."""

    def test_item_55_style_prefixed_stem_now_flagged(self) -> None:
        text = "אין אזכור של EO/IR/CV; עוסק בייצור רכיבים מכניים בלבד עבור פלטפורמה צבאית (רק"
        row = {"id": 55, "triage_reason": text}
        assert rth._row_flagged_fields(row, rth._ITEM_TRIAGE_FIELDS) == ["triage_reason"]

    def test_item_8_style_mid_word_cut_now_flagged(self) -> None:
        text = "לכן `core_relevance` = 1. `magnitude` = 1 כי מדובר באירוע חדשותי שגרתי, `novelty` = 1 כי אין חידוש טכנולוגי או מבצעי יוצא דופן מעבר לדיו"
        row = {"id": 8, "triage_reason": text}
        assert rth._row_flagged_fields(row, rth._ITEM_TRIAGE_FIELDS) == ["triage_reason"]

    def test_complete_sentence_not_flagged(self) -> None:
        row = {"id": 1, "triage_reason": "הפריט עוסק במערכת EO/IR תקינה ומלאה."}
        assert rth._row_flagged_fields(row, rth._ITEM_TRIAGE_FIELDS) == []

    def test_missing_value_not_flagged(self) -> None:
        row = {"id": 1, "triage_reason": None}
        assert rth._row_flagged_fields(row, rth._ITEM_TRIAGE_FIELDS) == []

    def test_analyze_fields_use_their_own_literal_name(self) -> None:
        """summary_he/so_what_he/uncertainty_he already end in "_he" -- no remapping needed, and
        none is present in _CHECK_FIELD_NAME."""
        assert "summary_he" not in rth._CHECK_FIELD_NAME
        row = {"id": 1, "summary_he": "משפט ארוך בעברית בלי שום סימן פיסוק בסופו שממשיך עוד ועוד"}
        assert rth._row_flagged_fields(row, rth._ITEM_ANALYZE_FIELDS) == ["summary_he"]


class TestRepairItemIdsFilter:
    def test_repair_restricts_to_given_item_ids(self, monkeypatch) -> None:
        text = "אין אזכור של EO/IR/CV; עוסק בייצור רכיבים מכניים בלבד עבור פלטפורמה צבאית (רק"
        items = [
            {"id": 1, "triage_reason": text},
            {"id": 2, "triage_reason": text},
        ]
        monkeypatch.setattr(rth, "_fetch_items", lambda: items)
        monkeypatch.setattr(rth, "_fetch_simple", lambda *a, **k: [])
        report = rth.repair(dry_run=True, item_ids={1})
        ids = {r["id"] for r in report["items_triage_repaired"]}
        assert ids == {1}
