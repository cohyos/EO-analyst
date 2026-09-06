"""Unit tests for scripts/repair_forecasts_country.py's pure/DB-mocked helpers (Q3-11,
docs/qa/findings_Q3_r1.md).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_repair_forecasts_country.py -q``
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

if "eoa.db" not in sys.modules:
    try:
        import eoa.db  # noqa: F401
    except ImportError:
        fake_db = types.ModuleType("eoa.db")
        fake_db.connection = lambda: None  # type: ignore[attr-defined]
        fake_db.get_pool = lambda: None  # type: ignore[attr-defined]
        sys.modules["eoa.db"] = fake_db

_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "repair_forecasts_country.py"
_spec = importlib.util.spec_from_file_location("repair_forecasts_country", _SCRIPT_PATH)
rfc = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(rfc)


class _FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def execute(self, query: str, params: dict | None = None) -> None:
        pass

    def fetchall(self) -> list[dict]:
        return self._rows

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeConnection:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def cursor(self, row_factory=None) -> _FakeCursor:
        return _FakeCursor(self._rows)


class TestStalePlatformMatch:
    def test_unrecognized_platform_label_is_never_flagged(self) -> None:
        row = {"platform": "לא קיים בטקסונומיה", "sources": ["item:1"]}
        conn = _FakeConnection([{"title": "x", "clean_text": "y", "summary_he": "z"}])
        assert rfc._stale_platform_match(row, conn) is False

    def test_no_recoverable_item_ids_is_never_flagged(self) -> None:
        row = {"platform": "מסוק קרב", "sources": None}
        conn = _FakeConnection([])
        assert rfc._stale_platform_match(row, conn) is False

    def test_regression_forecast_id_10_apache_passing_mention_flagged_stale(self) -> None:
        """The exact Q3-11 named example: forecast id 10 ('מסוק קרב'/attack_helicopter) whose
        only trigger item mentions "Apache" once, in passing, in an unrelated drone article --
        after platform_payloads.yaml's fix (bare "Apache" -> "Apache helicopter"), this row's own
        trigger text should no longer match its own platform."""
        row = {"platform": "מסוק קרב", "sources": ["item:309"]}
        conn = _FakeConnection(
            [
                {
                    "title": "Tekever acquires Flowcopter following AR6 reveal",
                    "clean_text": "... casualty evacuation, forward logistics, and combat wingman "
                    "concepts – such the UK Apache teaming concept under Project Nyx. Although the "
                    "executive did not comment ...",
                    "summary_he": "",
                }
            ]
        )
        assert rfc._stale_platform_match(row, conn) is True

    def test_genuine_attack_helicopter_deal_not_flagged(self) -> None:
        row = {"platform": "מסוק קרב", "sources": ["item:1"]}
        conn = _FakeConnection(
            [{"title": "Army orders new attack helicopter sights", "clean_text": "", "summary_he": ""}]
        )
        assert rfc._stale_platform_match(row, conn) is False


class TestRecomputeBuyerCountry:
    def test_known_country_left_unchanged_without_db_call(self) -> None:
        row = {"buyer_country": "US", "sources": ["item:1"], "rationale_he": ""}

        class _BoomConn:
            def cursor(self, row_factory=None):
                raise AssertionError("should not query the DB when buyer_country is already known")

        assert rfc._recompute_buyer_country(row, _BoomConn()) == "US"

    def test_falls_back_to_rationale_mention_when_nothing_else_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("eoa.tenders.forecast._country_from_entities", lambda ids: None)
        row = {"buyer_country": "other", "sources": [], "rationale_he": "העסקה נחתמה עבור צרפת."}
        conn = _FakeConnection([])
        assert rfc._recompute_buyer_country(row, conn) == "FR"

    def test_stays_other_when_no_signal_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("eoa.tenders.forecast._country_from_entities", lambda ids: None)
        row = {"buyer_country": "other", "sources": [], "rationale_he": "נימוק כללי."}
        conn = _FakeConnection([])
        assert rfc._recompute_buyer_country(row, conn) == "other"
