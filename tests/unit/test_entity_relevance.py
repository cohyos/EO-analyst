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
# score_entity (pure function) -- watchlist / news-source / country short-circuits
# --------------------------------------------------------------------------


class TestScoreEntityShortCircuits:
    def test_watchlist_match_always_scores_1(self) -> None:
        assert (
            er.score_entity(
                kind="person",
                mention_count=1,
                edge_count=0,
                event_count=0,
                in_scope_evidence_count=0,
                total_evidence_count=1,
                is_watchlist=True,
            )
            == 1.0
        )
        assert (
            er.score_entity(
                kind="company",
                mention_count=0,
                edge_count=0,
                event_count=0,
                in_scope_evidence_count=0,
                total_evidence_count=0,
                is_watchlist=True,
            )
            == 1.0
        )

    def test_watchlist_wins_over_news_source(self) -> None:
        score = er.score_entity(
            kind="company",
            mention_count=5,
            edge_count=5,
            event_count=5,
            in_scope_evidence_count=5,
            total_evidence_count=5,
            is_watchlist=True,
            is_news_source=True,
        )
        assert score == 1.0

    def test_news_source_scores_low_flat_value(self) -> None:
        """Even with lots of mentions/edges, a news outlet stays pinned near 0.1 -- it's
        where the item came from, not a market participant (F15 'The War Zone' shape)."""
        score = er.score_entity(
            kind="company",
            mention_count=20,
            edge_count=20,
            event_count=20,
            in_scope_evidence_count=20,
            total_evidence_count=20,
            is_watchlist=False,
            is_news_source=True,
        )
        assert score == 0.1
        assert score < er.RELEVANCE_THRESHOLD

    def test_country_with_edge_scores_mid(self) -> None:
        score = er.score_entity(
            kind="country",
            mention_count=0,
            edge_count=1,
            event_count=0,
            in_scope_evidence_count=1,
            total_evidence_count=1,
            is_watchlist=False,
        )
        assert score == 0.45

    def test_country_with_event_scores_mid(self) -> None:
        score = er.score_entity(
            kind="country",
            mention_count=0,
            edge_count=0,
            event_count=1,
            in_scope_evidence_count=1,
            total_evidence_count=1,
            is_watchlist=False,
        )
        assert score == 0.45

    def test_country_bare_mentions_only_scores_low(self) -> None:
        """A country name-dropped in text (mentions only, no edge/event participation)
        stays below threshold even with several mentions -- countries are buyers/markets,
        not tracked players, per the spec's binary-ish country rule."""
        score = er.score_entity(
            kind="country",
            mention_count=5,
            edge_count=0,
            event_count=0,
            in_scope_evidence_count=5,
            total_evidence_count=5,
            is_watchlist=False,
        )
        assert score == 0.2
        assert score < er.RELEVANCE_THRESHOLD

    def test_country_with_no_evidence_scores_bare(self) -> None:
        score = er.score_entity(
            kind="country",
            mention_count=0,
            edge_count=0,
            event_count=0,
            in_scope_evidence_count=0,
            total_evidence_count=0,
            is_watchlist=False,
        )
        assert score == 0.2


# --------------------------------------------------------------------------
# score_entity -- the weighted mention/edge/event/in-scope combination
# --------------------------------------------------------------------------


class TestScoreEntityWeightedFormula:
    def test_zero_evidence_scores_zero(self) -> None:
        assert (
            er.score_entity(
                kind="company",
                mention_count=0,
                edge_count=0,
                event_count=0,
                in_scope_evidence_count=0,
                total_evidence_count=0,
                is_watchlist=False,
            )
            == 0.0
        )

    def test_single_mention_out_of_scope_company_scores_low(self) -> None:
        """The F15 'Zipline in a Houston-highway story' shape: one mention, not in-scope,
        no graph or event evidence at all."""
        score = er.score_entity(
            kind="company",
            mention_count=1,
            edge_count=0,
            event_count=0,
            in_scope_evidence_count=0,
            total_evidence_count=1,
            is_watchlist=False,
        )
        assert score < er.RELEVANCE_THRESHOLD

    def test_org_with_edges_only_can_clear_threshold_without_mentions(self) -> None:
        """The core F15 regression case: 'US Navy'/'Air Force'-shaped entities have zero
        `items.entities_mentioned` hits but several in-scope graph edges -- the edge
        evidence source (previously ignored entirely) must be able to carry the score."""
        score = er.score_entity(
            kind="org",
            mention_count=0,
            edge_count=8,
            event_count=0,
            in_scope_evidence_count=8,
            total_evidence_count=8,
            is_watchlist=False,
        )
        assert score >= er.RELEVANCE_THRESHOLD

    def test_org_kind_floored_at_half_once_it_has_any_edge_or_event(self) -> None:
        """Even with weak in-scope/mention signal, an org/program/system with at least one
        edge or event is floored -- an institutional actor in any tracked relationship is
        inherently relevant."""
        score = er.score_entity(
            kind="org",
            mention_count=0,
            edge_count=1,
            event_count=0,
            in_scope_evidence_count=0,
            total_evidence_count=1,
            is_watchlist=False,
        )
        assert score >= 0.5

    def test_org_kind_not_floored_without_any_edge_or_event(self) -> None:
        score = er.score_entity(
            kind="org",
            mention_count=1,
            edge_count=0,
            event_count=0,
            in_scope_evidence_count=0,
            total_evidence_count=1,
            is_watchlist=False,
        )
        assert score < 0.5

    def test_event_evidence_alone_can_carry_a_program_entity(self) -> None:
        score = er.score_entity(
            kind="program",
            mention_count=0,
            edge_count=0,
            event_count=3,
            in_scope_evidence_count=1,
            total_evidence_count=1,
            is_watchlist=False,
        )
        assert score >= er.RELEVANCE_THRESHOLD

    def test_repeated_in_scope_evidence_scores_high(self) -> None:
        score = er.score_entity(
            kind="company",
            mention_count=8,
            edge_count=8,
            event_count=3,
            in_scope_evidence_count=8,
            total_evidence_count=8,
            is_watchlist=False,
        )
        assert score >= er.RELEVANCE_THRESHOLD
        assert score <= 1.0

    def test_score_is_monotonic_in_mention_count(self) -> None:
        low = er.score_entity(
            kind="system",
            mention_count=1,
            edge_count=0,
            event_count=0,
            in_scope_evidence_count=1,
            total_evidence_count=1,
            is_watchlist=False,
        )
        high = er.score_entity(
            kind="system",
            mention_count=5,
            edge_count=0,
            event_count=0,
            in_scope_evidence_count=5,
            total_evidence_count=5,
            is_watchlist=False,
        )
        assert high > low

    def test_score_is_monotonic_in_edge_count(self) -> None:
        low = er.score_entity(
            kind="company",
            mention_count=0,
            edge_count=1,
            event_count=0,
            in_scope_evidence_count=1,
            total_evidence_count=1,
            is_watchlist=False,
        )
        high = er.score_entity(
            kind="company",
            mention_count=0,
            edge_count=5,
            event_count=0,
            in_scope_evidence_count=5,
            total_evidence_count=5,
            is_watchlist=False,
        )
        assert high > low

    def test_score_is_monotonic_in_in_scope_fraction(self) -> None:
        low = er.score_entity(
            kind="org",
            mention_count=4,
            edge_count=0,
            event_count=0,
            in_scope_evidence_count=0,
            total_evidence_count=4,
            is_watchlist=False,
        )
        high = er.score_entity(
            kind="org",
            mention_count=4,
            edge_count=0,
            event_count=0,
            in_scope_evidence_count=4,
            total_evidence_count=4,
            is_watchlist=False,
        )
        assert high > low

    def test_score_never_exceeds_1(self) -> None:
        score = er.score_entity(
            kind="program",
            mention_count=1000,
            edge_count=1000,
            event_count=1000,
            in_scope_evidence_count=1000,
            total_evidence_count=1000,
            is_watchlist=False,
        )
        assert score <= 1.0

    def test_person_single_hit_scores_low(self) -> None:
        """F15 'Eric Trump' shape: a person with a single piece of evidence (whether a
        mention or a graph edge) across the whole DB stays hidden."""
        via_mention = er.score_entity(
            kind="person",
            mention_count=1,
            edge_count=0,
            event_count=0,
            in_scope_evidence_count=1,
            total_evidence_count=1,
            is_watchlist=False,
        )
        via_edge = er.score_entity(
            kind="person",
            mention_count=0,
            edge_count=1,
            event_count=0,
            in_scope_evidence_count=1,
            total_evidence_count=1,
            is_watchlist=False,
        )
        assert via_mention < er.RELEVANCE_THRESHOLD
        assert via_edge < er.RELEVANCE_THRESHOLD

    def test_person_recurring_evidence_scores_higher_than_single_hit(self) -> None:
        one_hit = er.score_entity(
            kind="person",
            mention_count=1,
            edge_count=0,
            event_count=0,
            in_scope_evidence_count=1,
            total_evidence_count=1,
            is_watchlist=False,
        )
        many_hits = er.score_entity(
            kind="person",
            mention_count=4,
            edge_count=2,
            event_count=0,
            in_scope_evidence_count=4,
            total_evidence_count=4,
            is_watchlist=False,
        )
        assert many_hits > one_hit

    def test_single_mention_person_scores_lower_than_company_same_profile(self) -> None:
        company = er.score_entity(
            kind="company",
            mention_count=1,
            edge_count=0,
            event_count=0,
            in_scope_evidence_count=1,
            total_evidence_count=1,
            is_watchlist=False,
        )
        person = er.score_entity(
            kind="person",
            mention_count=1,
            edge_count=0,
            event_count=0,
            in_scope_evidence_count=1,
            total_evidence_count=1,
            is_watchlist=False,
        )
        assert person < company


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
# is_news_source
# --------------------------------------------------------------------------


class TestIsNewsSource:
    def test_static_list_exact_match(self) -> None:
        assert er.is_news_source("The War Zone") is True
        assert er.is_news_source("Reuters") is True

    def test_static_list_substring_match(self) -> None:
        assert er.is_news_source("Breaking Defense News") is True

    def test_unrelated_company_not_flagged(self) -> None:
        assert er.is_news_source("Elbit") is False
        assert er.is_news_source("Zipline") is False

    def test_sources_table_match_after_normalizing_suffix(self) -> None:
        """A `sources.name` like 'The War Zone (TWZ)' (this install's feed title) should
        match the bare entity name 'The War Zone' NER produced from article bylines."""
        assert er.is_news_source("The War Zone", source_names=["The War Zone (TWZ)"]) is True

    def test_sources_table_normalized_exact_match_can_hit_a_companys_own_feed(self) -> None:
        """A watchlist company's own press-release page (e.g. 'Saab - Newsroom Press
        Releases') normalizes down to the bare company name, so `is_news_source` alone
        *can* flag it -- this is a known, accepted limitation of a name-only heuristic.
        It never mis-scores a real watchlist company in practice because `score_entity`
        checks `is_watchlist` first (see `test_watchlist_wins_over_news_source`), so the
        watchlist match always wins before `is_news_source` is even considered."""
        assert er.is_news_source("Saab", source_names=["Saab - Newsroom Press Releases"]) is True

    def test_empty_name_not_flagged(self) -> None:
        assert er.is_news_source("") is False


# --------------------------------------------------------------------------
# resolve_country_kind
# --------------------------------------------------------------------------


class TestResolveCountryKind:
    def test_company_kind_country_name_promoted(self) -> None:
        assert er.resolve_country_kind("Japan", "company") == "country"

    def test_org_kind_country_name_promoted(self) -> None:
        assert er.resolve_country_kind("Germany", "org") == "country"

    def test_case_insensitive(self) -> None:
        assert er.resolve_country_kind("GERMANY", "company") == "country"

    def test_non_country_name_unchanged(self) -> None:
        assert er.resolve_country_kind("Elbit", "company") == "company"

    def test_person_kind_never_promoted_even_if_name_matches(self) -> None:
        """Guards the static list's known ambiguity (e.g. 'Jordan' as a given name) --
        only the LLM's generic fallback kinds ('company'/'org') get reclassified."""
        assert er.resolve_country_kind("Jordan", "person") == "person"

    def test_system_kind_never_promoted(self) -> None:
        assert er.resolve_country_kind("Georgia", "system") == "system"


# --------------------------------------------------------------------------
# _build_evidence (mention/edge/event union + in-scope fraction dedup)
# --------------------------------------------------------------------------


class TestBuildEvidence:
    def test_deduplicates_item_shared_across_evidence_sources(self) -> None:
        """The same item backing both a mention and a graph edge counts once toward the
        in-scope fraction's denominator, not twice."""
        shared = {"id": 1, "domain": "air_defense", "level": "red"}
        shared_edge = {"item_id": 1, "domain": "air_defense", "level": "red"}
        evidence = er._build_evidence(
            mention_items=[shared],
            edge_items=[shared_edge],
            event_items=[],
        )
        assert evidence.mention_count == 1
        assert evidence.edge_count == 1
        assert evidence.total_evidence_count == 1
        assert evidence.in_scope_evidence_count == 1

    def test_out_of_scope_and_archive_excluded(self) -> None:
        evidence = er._build_evidence(
            mention_items=[
                {"id": 1, "domain": "out_of_scope", "level": "yellow"},
                {"id": 2, "domain": "air_defense", "level": "archive"},
                {"id": 3, "domain": "air_defense", "level": "orange"},
            ],
            edge_items=[],
            event_items=[],
        )
        assert evidence.total_evidence_count == 3
        assert evidence.in_scope_evidence_count == 1

    def test_event_items_counted(self) -> None:
        evidence = er._build_evidence(
            mention_items=[],
            edge_items=[],
            event_items=[{"item_id": 9, "domain": "c_uas", "level": "yellow"}],
        )
        assert evidence.event_count == 1
        assert evidence.total_evidence_count == 1
        assert evidence.in_scope_evidence_count == 1

    def test_no_evidence_yields_empty(self) -> None:
        evidence = er._build_evidence(mention_items=[], edge_items=[], event_items=[])
        assert evidence.total_evidence_count == 0
        assert evidence.in_scope_evidence_count == 0


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

    def execute(self, query: str, params: object | None = None) -> _FakeCursor:
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

    def test_persists_score_watchlist_and_kind(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _FakeConnection(
            rows_by_query={
                "SELECT id, kind, aliases FROM entities": [{"id": 5, "kind": "company", "aliases": []}],
                "FROM items WHERE entities_mentioned": [
                    {"id": 10, "domain": "air_defense", "level": "red"},
                    {"id": 11, "domain": "air_defense", "level": "yellow"},
                ],
                "FROM graph_edges": [],
                "FROM events": [],
                "SELECT name FROM sources": [],
            }
        )
        monkeypatch.setattr("eoa.db.connection", lambda: conn)
        monkeypatch.setattr(er, "is_watchlist_match", lambda name, aliases=None: True)

        score = er.score_and_persist_entity("Elbit")

        assert score == 1.0
        update_calls = [c for c in conn.executed if c[0].strip().startswith("UPDATE entities")]
        assert len(update_calls) == 1
        _, params = update_calls[0]
        assert params == (1.0, True, "company", 5)

    def test_never_raises_on_db_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom() -> None:
            raise RuntimeError("db down")

        monkeypatch.setattr("eoa.db.connection", boom)
        assert er.score_and_persist_entity("Anything") is None


# --------------------------------------------------------------------------
# compute_relevance_row (used by the backfill script)
# --------------------------------------------------------------------------


class TestComputeRelevanceRow:
    def test_matches_score_entity_formula(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(er, "is_watchlist_match", lambda name, aliases=None: False)
        row = {"name": "Zipline", "kind": "company", "aliases": []}
        evidence = er.EntityEvidence(
            mention_count=1, edge_count=0, event_count=0, in_scope_evidence_count=0, total_evidence_count=1
        )
        score, watchlist, kind = er.compute_relevance_row(row, evidence)
        assert watchlist is False
        assert kind == "company"
        assert score == er.score_entity(
            kind="company",
            mention_count=1,
            edge_count=0,
            event_count=0,
            in_scope_evidence_count=0,
            total_evidence_count=1,
            is_watchlist=False,
        )

    def test_reclassifies_country_kind(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(er, "is_watchlist_match", lambda name, aliases=None: False)
        row = {"name": "Japan", "kind": "company", "aliases": []}
        evidence = er.EntityEvidence()
        score, _watchlist, kind = er.compute_relevance_row(row, evidence)
        assert kind == "country"
        assert score == er._COUNTRY_BARE_SCORE

    def test_news_source_flag_forces_low_score(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(er, "is_watchlist_match", lambda name, aliases=None: False)
        row = {"name": "The War Zone", "kind": "company", "aliases": []}
        evidence = er.EntityEvidence(
            mention_count=0, edge_count=1, event_count=0, in_scope_evidence_count=1, total_evidence_count=1
        )
        score, _, _ = er.compute_relevance_row(row, evidence, is_news_source=True)
        assert score == er._NEWS_SOURCE_SCORE
