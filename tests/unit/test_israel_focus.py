"""Unit tests for `eoa.pipeline.israel_focus` (A13: Israeli-industry focus).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_israel_focus.py -q``
"""

from __future__ import annotations

import sys
import types

import pytest

if "eoa.db" not in sys.modules:
    try:
        import eoa.db  # noqa: F401
    except ImportError:
        fake_db = types.ModuleType("eoa.db")
        fake_db.connection = lambda: None  # type: ignore[attr-defined]
        fake_db.get_pool = lambda: None  # type: ignore[attr-defined]
        sys.modules["eoa.db"] = fake_db

from eoa.pipeline import israel_focus as il


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    il.clear_caches()
    yield
    il.clear_caches()


class TestIsraelRelevancePureFunction:
    def test_israeli_company_mention_scores_and_reasons(self) -> None:
        out = il.israel_relevance("Elbit Systems won a contract for a new targeting pod", [])
        assert out["score"] > 0
        assert il.REASON_ISRAELI_COMPANY in out["reasons"]

    def test_israeli_company_via_entities_list(self) -> None:
        out = il.israel_relevance("A company announced a new sensor", ["Rafael"])
        assert il.REASON_ISRAELI_COMPANY in out["reasons"]

    def test_israeli_agency_mention(self) -> None:
        out = il.israel_relevance('צה"ל רכש מערכת חדשה', [])
        assert il.REASON_ISRAELI_AGENCY in out["reasons"]

    def test_competitor_to_israeli_company(self) -> None:
        # Northrop Grumman shares `airborne_pods`/`air_defense` focus with Rafael/Elbit/IAI.
        out = il.israel_relevance("Northrop Grumman announced a new pod", ["Northrop Grumman"])
        assert il.REASON_COMPETITOR in out["reasons"]

    def test_export_market_by_geography_code(self) -> None:
        out = il.israel_relevance("A deal was signed", [], geography="IN")
        assert il.REASON_EXPORT_MARKET in out["reasons"]

    def test_export_market_by_text_mention(self) -> None:
        out = il.israel_relevance("India ordered new EO/IR sensors", [], geography="other")
        assert il.REASON_EXPORT_MARKET in out["reasons"]

    def test_hebrew_language_source(self) -> None:
        out = il.israel_relevance("עדכון כללי", [], lang="he")
        assert il.REASON_HEBREW_SOURCE in out["reasons"]

    def test_no_signal_scores_zero(self) -> None:
        out = il.israel_relevance("A generic story about an unrelated topic", [], lang="en", geography="US")
        assert out["score"] == 0.0
        assert out["reasons"] == []

    def test_score_capped_at_one(self) -> None:
        text = 'Elbit Systems ומשרד הביטחון חתמו הסכם; Northrop Grumman מתחרה. India export deal.'
        out = il.israel_relevance(text, ["Elbit", "Northrop Grumman"], lang="he", geography="IN")
        assert out["score"] <= 1.0
        assert len(out["reasons"]) >= 3


class TestIsraeliWatchlistNames:
    def test_includes_known_israeli_companies(self) -> None:
        names = il.israeli_watchlist_names()
        for expected in ("Elbit", "Rafael", "IAI", "Controp", "RADA"):
            assert expected in names

    def test_excludes_non_israeli_companies(self) -> None:
        names = il.israeli_watchlist_names()
        assert "Northrop Grumman" not in names
        assert "Thales" not in names


class TestScoreAndPersistEntityIsraeli:
    def test_returns_none_when_entity_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeConn:
            def execute(self, *_args, **_kwargs):
                class _Cur:
                    def fetchone(self):
                        return None

                return _Cur()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(il, "connection", lambda: FakeConn(), raising=False)
        import eoa.db as eoa_db

        monkeypatch.setattr(eoa_db, "connection", lambda: FakeConn())
        assert il.score_and_persist_entity_israeli("Nonexistent Co") is None
