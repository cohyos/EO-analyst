"""Unit tests for scripts/repair_forecast_sources.py's pure dedup logic (Q3-11b, docs/qa/
findings_Q3_r2.md). No DB -- only ``_dedupe``.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_repair_forecast_sources.py -q``
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

_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "repair_forecast_sources.py"
_spec = importlib.util.spec_from_file_location("repair_forecast_sources", _SCRIPT_PATH)
rfs = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(rfs)


class TestDedupe:
    def test_removes_repeated_entries_order_preserving(self) -> None:
        assert rfs._dedupe(["item:105", "item:105", "item:105"]) == ["item:105"]

    def test_preserves_order_of_first_occurrence(self) -> None:
        assert rfs._dedupe(["item:815", "item:257", "item:58", "item:58", "item:105"]) == [
            "item:815",
            "item:257",
            "item:58",
            "item:105",
        ]

    def test_no_duplicates_returns_equal_list(self) -> None:
        assert rfs._dedupe(["item:1", "item:2"]) == ["item:1", "item:2"]

    def test_none_and_empty_pass_through(self) -> None:
        assert rfs._dedupe(None) is None
        assert rfs._dedupe([]) == []
