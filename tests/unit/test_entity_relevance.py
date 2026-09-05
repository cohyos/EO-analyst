"""Unit tests for `eoa.pipeline.entity_relevance` (F15: entity relevance scoring).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_entity_relevance.py -q``
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

from eoa.pipeline import entity_relevance as er

# --------------------------------------------------------------------------
# score_entity (pure function)
# --------------------------------------------------------------------------


class TestScoreEntity:
    def test_watchlist_match_always_scores_1(self) -> None:
        assert er.score_entity(kind="person", mention_count=1, in_scope_mentions=0, is_watchlist=True) == 1.0
        assert er.score_entity(kind="company", mention_count=0, in_scope_mentions=0, is_watchlist=True) == 1.0

    def test_zero_mentions_scores_zero(self) -> None:
        assert (
            er.score_entity(kind="company", mention_count=0, in_scope_mentions=0, is_watchlist=False) == 0.0
        )

    def test_single_mention_out_of_scope_company_scores_low(self) -> None:
        """The F15 'Zipline in a Houston-highway story' shape: one mention, not in-scope."""
        score = er.score_entity(kind="company", mention_count=1, in_scope_mentions=0, is_watchlist=False)
        assert score < er.RELEVANCE_THRESHOLD

    def test_single_mention_in_scope_person_scores_lower_than_company(self) -> None:
        """Same mention/scope profile, but kind=person gets the extra single-mention downweight."""
        company = er.score_entity(kind="company", mention_count=1, in_scope_mentions=1, is_watchlist=False)
        person = er.score_entity(kind="person", mention_count=1, in_scope_mentions=1, is_watchlist=False)
        assert person < company

    def test_repeated_in_scope_mentions_score_high(self) -> None:
        score = er.score_entity(kind="company", mention_count=8, in_scope_mentions=8, is_watchlist=False)
        assert score >= er.RELEVANCE_THRESHOLD
        assert score <= 1.0

    def test_score_is_monotonic_in_mention_count(self) -> None:
        low = er.score_entity(kind="system", mention_count=1, in_scope_mentions=1, is_watchlist=False)
        high = er.score_entity(kind="system", mention_count=5, in_scope_mentions=5, is_watchlist=False)
        assert high > low

    def test_score_is_monotonic_in_in_scope_fraction(self) -> None:
        low = er.score_entity(kind="org", mention_count=4, in_scope_mentions=0, is_watchlist=False)
        high = er.score_entity(kind="org", mention_count=4, in_scope_mentions=4, is_watchlist=False)
        assert high > low

    def test_score_never_exceeds_1(self) -> None:
        score = er.score_entity(
            kind="program", mention_count=1000, in_scope_mentions=1000, is_watchlist=False
        )
        assert score <= 1.0

    def test_country_kind_with_multiple_mentions_less_downweighted_than_single(self) -> None:
        one = er.score_entity(kind="country", mention_count=1, in_scope_mentions=1, is_watchlist=False)
        many = er.score_entity(kind="country", mention_count=3, in_scope_mentions=3, is_watchlist=False)
        assert many > one


# --------------------------------------------------------------------------
# is_watchlist_match
# --------------------------------------------------------------------------


class TestIsWatchlistMatch:
    def _patch_watchlist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_settings = types.SimpleNamespace(
            watchlist={
                "companies": [
                    {"name": "Elbit", "aliases": ["Elbit Systems", "אלביט"]},
                    {"name": "RTX", "aliases": ["Raytheon"]},
                ],
                "programs": [{"name": "Replicator", "aliases": []}],
            }
        )
        monkeypatch.setattr(er, "settings", lambda: fake_settings)

    def test_exact_canonical_name_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_watchlist(monkeypatch)
        assert er.is_watchlist_match("Elbit") is True

    def test_alias_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_watchlist(monkeypatch)
        assert er.is_watchlist_match("Raytheon") is True

    def test_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_watchlist(monkeypatch)
        assert er.is_watchlist_match("ELBIT") is True

    def test_program_name_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_watchlist(monkeypatch)
        assert er.is_watchlist_match("Replicator") is True

    def test_unrelated_name_does_not_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_watchlist(monkeypatch)
        assert er.is_watchlist_match("Zipline") is False

    def test_entity_aliases_argument_also_checked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_watchlist(monkeypatch)
        # The entity's own recorded alias matches a watchlist alias, even though its
        # canonical `name` in `entities` doesn't literally equal the watchlist entry.
        assert er.is_watchlist_match("Elbit Systems Ltd", aliases=["אלביט"]) is True

    def test_substring_match_both_directions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_watchlist(monkeypatch)
        assert er.is_watchlist_match("Elbit Systems Corporation") is True


# --------------------------------------------------------------------------
# score_and_persist_entity (DB I/O)
# --------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict]:
        return self._rows

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None


class _FakeConnection:
    def __init__(self, rows_by_query: dict[str, list[dict]] | None = None) -> None:
        self.executed: list[tuple] = []
        self._rows_by_query = rows_by_query or {}

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, query: str, params: tuple | None = None) -> _FakeCursor:
        self.executed.append((query, params))
        for key, rows in self._rows_by_query.items():
            if key in query:
                return _FakeCursor(rows)
        return _FakeCursor([])


class TestScoreAndPersistEntity:
    def test_returns_none_when_entity_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _FakeConnection(rows_by_query={"SELECT id, kind, aliases FROM entities": []})
        monkeypatch.setattr("eoa.db.connection", lambda: conn)
        assert er.score_and_persist_entity("Ghost Co") is None

    def test_persists_score_and_watchlist_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _FakeConnection(
            rows_by_query={
                "SELECT id, kind, aliases FROM entities": [{"id": 5, "kind": "company", "aliases": []}],
                "SELECT domain, level FROM items": [
                    {"domain": "air_defense", "level": "red"},
                    {"domain": "air_defense", "level": "yellow"},
                ],
            }
        )
        monkeypatch.setattr("eoa.db.connection", lambda: conn)
        monkeypatch.setattr(er, "is_watchlist_match", lambda name, aliases=None: True)

        score = er.score_and_persist_entity("Elbit")

        assert score == 1.0
        update_calls = [c for c in conn.executed if c[0].strip().startswith("UPDATE entities")]
        assert len(update_calls) == 1
        _, params = update_calls[0]
        assert params == (1.0, True, 5)

    def test_never_raises_on_db_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom() -> None:
            raise RuntimeError("db down")

        monkeypatch.setattr("eoa.db.connection", boom)
        assert er.score_and_persist_entity("Anything") is None


# --------------------------------------------------------------------------
# compute_relevance_row (used by the backfill script)
# --------------------------------------------------------------------------


class TestComputeRelevanceRow:
    def test_matches_score_and_persist_formula(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(er, "is_watchlist_match", lambda name, aliases=None: False)
        row = {"name": "Zipline", "kind": "company", "aliases": []}
        score, watchlist = er.compute_relevance_row(row, mention_count=1, in_scope_mentions=0)
        assert watchlist is False
        assert score == er.score_entity(
            kind="company", mention_count=1, in_scope_mentions=0, is_watchlist=False
        )
