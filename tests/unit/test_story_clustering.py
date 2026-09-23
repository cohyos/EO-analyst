"""Unit tests for `eoa.pipeline.story_clustering` (2026-09-17, "improve same-story grouping"
task). `_build_components` is a pure function over an already-loaded item pool -- every DB-touching
call (`get_corroboration_edges_for_items`, `bulk_set_story_ids`, `mark_stage`,
`get_items_for_story_clustering`) is monkeypatched, same policy as `tests/unit/test_corroboration.py`.
No live DB, no network, no LLM.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_story_clustering.py -q``
"""

from __future__ import annotations

import datetime as dt

import pytest

from eoa.pipeline import story_clustering as sc


def _dt(day: int, hour: int = 12) -> dt.datetime:
    return dt.datetime(2026, 9, day, hour, tzinfo=dt.UTC)


def _item(
    id: int,
    *,
    dedup_of: int | None = None,
    lang: str = "en",
    title: str = "",
    entities: list[str] | None = None,
    published_at: dt.datetime | None = None,
    embedding: list[float] | None = None,
    story_id: int | None = None,
) -> dict:
    return {
        "id": id,
        "dedup_of": dedup_of,
        "lang": lang,
        "title": title,
        "entities_mentioned": entities or [],
        "published_at": published_at or _dt(15),
        "fetched_at": published_at or _dt(15),
        "embedding": embedding,
        "story_id": story_id,
    }


@pytest.fixture(autouse=True)
def no_corroboration_edges(monkeypatch: pytest.MonkeyPatch):
    """Default: no corroboration edges -- individual tests override via monkeypatch when needed."""
    monkeypatch.setattr(sc, "get_corroboration_edges_for_items", lambda ids: [])


class TestBuildComponentsDedupEdge:
    def test_dedup_of_pair_becomes_one_story(self) -> None:
        items = [_item(10), _item(11, dedup_of=10)]
        assignment, stats = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        assert assignment[10] == assignment[11] == 10
        assert stats.edges_dedup == 1
        assert stats.stories_total == 1
        assert stats.stories_multi_member == 1

    def test_dedup_of_target_outside_pool_is_ignored_not_crashed(self) -> None:
        items = [_item(11, dedup_of=999)]
        assignment, stats = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        assert assignment == {11: 11}
        assert stats.edges_dedup == 0


class TestBuildComponentsCorroborationEdge:
    def test_corroboration_pair_becomes_one_story(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sc, "get_corroboration_edges_for_items", lambda ids: [(20, 21)])
        items = [_item(20), _item(21)]
        assignment, stats = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        assert assignment[20] == assignment[21] == 20
        assert stats.edges_corroboration == 1

    def test_corroboration_edge_referencing_item_outside_pool_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sc, "get_corroboration_edges_for_items", lambda ids: [(20, 999)])
        items = [_item(20)]
        assignment, stats = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        assert assignment == {20: 20}
        assert stats.edges_corroboration == 0


class TestBuildComponentsEmbeddingEdge:
    def test_similar_embeddings_within_window_link(self) -> None:
        items = [
            _item(30, embedding=[1.0, 0.0], published_at=_dt(15)),
            _item(31, embedding=[0.99, 0.01], published_at=_dt(16)),
        ]
        assignment, stats = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        assert assignment[30] == assignment[31]
        assert stats.edges_embedding == 1

    def test_dissimilar_embeddings_do_not_link(self) -> None:
        items = [
            _item(30, embedding=[1.0, 0.0], published_at=_dt(15)),
            _item(31, embedding=[0.0, 1.0], published_at=_dt(15)),
        ]
        assignment, _ = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        assert assignment[30] != assignment[31]

    def test_similar_embeddings_outside_window_do_not_link(self) -> None:
        items = [
            _item(30, embedding=[1.0, 0.0], published_at=_dt(1)),
            _item(31, embedding=[0.99, 0.01], published_at=_dt(20)),
        ]
        assignment, stats = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        assert assignment[30] != assignment[31]
        assert stats.edges_embedding == 0

    def test_transitive_merge_through_a_hub_item(self) -> None:
        """The live SPICE-1000 calibration case: two items below the embedding threshold against
        each other still end up in the same story because a THIRD item clears the threshold
        against both -- union-find merges transitively."""
        items = [
            _item(1, embedding=[1.0, 0.0, 0.0], published_at=_dt(15)),  # far from 2
            _item(2, embedding=[0.0, 1.0, 0.0], published_at=_dt(15)),  # far from 1
            _item(3, embedding=[0.7, 0.7, 0.0], published_at=_dt(15)),  # close to both
        ]
        assignment, _ = sc._build_components(items, embedding_threshold=0.60, window_days=3)
        assert len({assignment[1], assignment[2], assignment[3]}) == 1


class TestBuildComponentsCrossLanguageTitleEdge:
    def test_shared_latin_tokens_and_entities_across_languages_link(self) -> None:
        items = [
            _item(
                40,
                lang="he",
                title="SPICE 1000 של רפאל משולב במטוסי F-35",
                entities=["Rafael", "Lockheed Martin", "SPICE 1000", "F-35"],
                published_at=_dt(15),
            ),
            _item(
                41,
                lang="en",
                title="Rafael Integrates SPICE 1000 Precision Weapon With F-35",
                entities=["Rafael", "Lockheed Martin", "F-35", "SPICE 1000", "Israel Air Force"],
                published_at=_dt(16),
            ),
        ]
        assignment, stats = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        assert assignment[40] == assignment[41]
        assert stats.edges_title == 1

    def test_same_language_pair_never_uses_title_edge(self) -> None:
        """Edge (d) is explicitly cross-language only -- two same-language items with shared
        tokens/entities but below the embedding threshold must NOT link via this edge (same-
        language paraphrase matching is `eoa.report.clustering`'s job, not this stage's)."""
        items = [
            _item(
                40,
                lang="en",
                title="Rafael SPICE 1000 F-35 news",
                entities=["Rafael", "F-35"],
                published_at=_dt(15),
            ),
            _item(
                41,
                lang="en",
                title="Rafael SPICE 1000 F-35 update",
                entities=["Rafael", "F-35"],
                published_at=_dt(15),
            ),
        ]
        assignment, stats = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        assert assignment[40] != assignment[41]
        assert stats.edges_title == 0

    def test_fewer_than_two_shared_entities_does_not_link(self) -> None:
        items = [
            _item(40, lang="he", title="SPICE F-35", entities=["Rafael"], published_at=_dt(15)),
            _item(41, lang="en", title="SPICE F-35 news", entities=["Lockheed Martin"], published_at=_dt(15)),
        ]
        assignment, stats = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        assert assignment[40] != assignment[41]
        assert stats.edges_title == 0


class TestBuildComponentsIdempotence:
    def test_story_id_is_stable_min_id_across_reruns(self) -> None:
        items = [_item(50), _item(51, dedup_of=50), _item(52, dedup_of=50)]
        first, _ = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        second, _ = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        assert first == second == {50: 50, 51: 50, 52: 50}

    def test_rerun_with_existing_story_id_values_does_not_change_result(self) -> None:
        """`assign_story_ids` only writes ids whose assignment actually changed -- feeding back
        the previous run's `story_id` values must not perturb the component structure."""
        items = [
            _item(50, story_id=50),
            _item(51, dedup_of=50, story_id=50),
            _item(52, dedup_of=50, story_id=50),
        ]
        assignment, _ = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        assert assignment == {50: 50, 51: 50, 52: 50}


class TestBuildComponentsSixItemSpiceCase:
    """The exact measured evidence from the task brief: 6 items, 2 pre-existing dedup_of pairs,
    should end up as ONE story once embedding similarity (with a hub item) is applied."""

    def test_all_six_spice_items_become_one_story(self) -> None:
        items = [
            _item(22396, lang="he", dedup_of=None, embedding=[0.8, 0.2, 0.0], published_at=_dt(15, 14)),
            _item(22798, lang="en", dedup_of=None, embedding=[0.75, 0.4, 0.1], published_at=_dt(15, 19)),
            _item(23002, lang="en", dedup_of=None, embedding=[0.7, 0.45, 0.1], published_at=_dt(15, 21)),
            _item(24089, lang="en", dedup_of=None, embedding=[0.72, 0.5, 0.05], published_at=_dt(16, 6)),
            _item(25948, lang="en", dedup_of=22396, embedding=[0.78, 0.35, 0.0], published_at=_dt(16, 19)),
            _item(26284, lang="en", dedup_of=None, embedding=[0.73, 0.48, 0.08], published_at=_dt(16, 20)),
        ]
        assignment, stats = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        assert len({assignment[i] for i in (22396, 22798, 23002, 24089, 25948, 26284)}) == 1
        assert stats.stories_total == 1
        assert stats.stories_multi_member == 1


class TestBuildComponentsPreservesPriorStoryWhenRootAgesOut:
    """F20 (SOL-AUDIT-2026-09-24): the candidate-pool query windows by ``since_days``, so a story's
    oldest member (often the root, since ``story_id`` == the component's min item id) can age out of
    a later run's pool entirely -- it's simply absent from ``items``. Without edge (e), the
    remaining in-window member would fall back to its own id as a "new" story, silently splitting a
    previously persisted group."""

    def test_member_retains_persisted_story_id_when_root_is_absent_from_pool(self) -> None:
        # Root item 10 is NOT in the pool (aged out); item 11 still carries its persisted
        # story_id=10 from an earlier run and has no in-pool edge (no dedup_of/corroboration/
        # embedding/title match) to re-derive that link from scratch.
        items = [_item(11, story_id=10, dedup_of=None)]
        assignment, _stats = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        assert assignment[11] == 10

    def test_two_recent_members_of_an_aged_out_root_stay_grouped_together(self) -> None:
        items = [
            _item(11, story_id=10, dedup_of=None, published_at=_dt(15)),
            _item(12, story_id=10, dedup_of=None, published_at=_dt(16)),
        ]
        assignment, _ = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        assert assignment[11] == assignment[12] == 10

    def test_no_prior_story_id_still_forms_its_own_new_story(self) -> None:
        """Edge (e) only fires when a persisted story_id already exists -- a genuinely new,
        never-before-clustered item still gets its own id, same as before this fix."""
        items = [_item(20, story_id=None, dedup_of=None)]
        assignment, _ = sc._build_components(items, embedding_threshold=0.80, window_days=3)
        assert assignment[20] == 20


class TestAssignStoryIdsRootAgedOutIntegration:
    def test_reassigned_stays_zero_when_pool_lacks_the_aged_out_root(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end through `assign_story_ids`: the item whose root aged out must not be written
        with a new (self) story_id -- `bulk_set_story_ids` must not even be called for it."""
        items = [_item(11, story_id=10, dedup_of=None)]
        monkeypatch.setattr(sc, "get_items_for_story_clustering", lambda since_days: items)
        persisted: dict[int, int] = {}
        monkeypatch.setattr(sc, "bulk_set_story_ids", lambda assignment: persisted.update(assignment))
        monkeypatch.setattr(sc, "mark_stage", lambda item_id, stage: None)

        stats = sc.assign_story_ids(since_days=7)

        assert persisted == {}
        assert stats.reassigned == 0


class TestAssignStoryIdsStageEntrypoint:
    def test_empty_pool_returns_early(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sc, "get_items_for_story_clustering", lambda since_days: [])
        called = {"bulk": False}
        monkeypatch.setattr(sc, "bulk_set_story_ids", lambda assignment: called.__setitem__("bulk", True))
        stats = sc.assign_story_ids(since_days=7)
        assert stats.items_processed == 0
        assert called["bulk"] is False

    def test_only_changed_assignments_are_persisted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        items = [_item(1, story_id=1), _item(2, dedup_of=1, story_id=None)]
        monkeypatch.setattr(sc, "get_items_for_story_clustering", lambda since_days: items)
        persisted: dict[int, int] = {}
        monkeypatch.setattr(sc, "bulk_set_story_ids", lambda assignment: persisted.update(assignment))
        monkeypatch.setattr(sc, "mark_stage", lambda item_id, stage: None)
        stats = sc.assign_story_ids(since_days=7)
        assert persisted == {2: 1}  # id 1 already had the right story_id -- not rewritten
        assert stats.reassigned == 1
