"""Unit tests for scripts/repair_gershayim.py's pure transforms (D1 round-1 fix,
docs/qa/loop/round_1_fixes.md, ``gershayim_no_ascii_quote``). No DB.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_repair_gershayim.py -q``
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

_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "repair_gershayim.py"
_spec = importlib.util.spec_from_file_location("repair_gershayim", _SCRIPT_PATH)
rg = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(rg)


class TestNeedsFix:
    def test_ascii_quote_between_hebrew_flagged(self) -> None:
        assert rg._needs_fix('כטב"ם') is True

    def test_gershayim_already_correct_not_flagged(self) -> None:
        assert rg._needs_fix("כטב״ם") is False

    def test_ascii_quote_not_between_hebrew_not_flagged(self) -> None:
        assert rg._needs_fix('a "quoted" word') is False

    def test_non_string_not_flagged(self) -> None:
        assert rg._needs_fix(None) is False
        assert rg._needs_fix(42) is False


class TestRowNeedsFix:
    def test_scalar_column_fixed(self) -> None:
        cols = [("summary_he", "scalar")]
        row = {"id": 1, "summary_he": 'הפריט עוסק במטע"ד חדש.'}
        updates = rg._row_needs_fix(row, cols)
        assert updates == {"summary_he": "הפריט עוסק במטע״ד חדש."}

    def test_array_column_fixed_only_offending_elements(self) -> None:
        cols = [("key_facts", "array")]
        row = {"id": 1, "key_facts": ['עובדה על כטב"ם', "עובדה נקייה"]}
        updates = rg._row_needs_fix(row, cols)
        assert updates == {"key_facts": ["עובדה על כטב״ם", "עובדה נקייה"]}

    def test_clean_row_no_updates(self) -> None:
        cols = [("summary_he", "scalar"), ("key_facts", "array")]
        row = {"id": 1, "summary_he": "טקסט נקי לגמרי.", "key_facts": ["עובדה אחת", "עובדה שתיים"]}
        assert rg._row_needs_fix(row, cols) == {}

    def test_null_array_column_no_updates(self) -> None:
        cols = [("key_facts", "array")]
        row = {"id": 1, "key_facts": None}
        assert rg._row_needs_fix(row, cols) == {}


class TestNormalizeJson:
    def test_string_leaf_fixed(self) -> None:
        new_value, changed = rg._normalize_json('טקסט עם צה"ל בפנים')
        assert changed is True
        assert new_value == "טקסט עם צה״ל בפנים"

    def test_clean_string_unchanged(self) -> None:
        new_value, changed = rg._normalize_json("טקסט נקי")
        assert changed is False
        assert new_value == "טקסט נקי"

    def test_nested_dict_and_list(self) -> None:
        obj = {
            "answer_he": 'התשובה כוללת אזכור של מטע"ד.',
            "key_facts": ['עובדה עם צה"ל', "עובדה נקייה"],
            "confidence": 0.8,
            "nested": {"contradictions_he": ['סתירה עם תע"א']},
        }
        new_obj, changed = rg._normalize_json(obj)
        assert changed is True
        assert new_obj["answer_he"] == "התשובה כוללת אזכור של מטע״ד."
        assert new_obj["key_facts"] == ["עובדה עם צה״ל", "עובדה נקייה"]
        assert new_obj["confidence"] == 0.8
        assert new_obj["nested"]["contradictions_he"] == ["סתירה עם תע״א"]

    def test_no_change_when_nothing_to_fix(self) -> None:
        obj = {"a": [1, 2, "clean"], "b": None}
        new_obj, changed = rg._normalize_json(obj)
        assert changed is False
        assert new_obj == obj
