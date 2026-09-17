"""Unit tests for the story-clustering additions to `eoa.api.services.list_items`/`get_item`
(2026-09-17, "improve same-story grouping" task): `story_size`/`story_primary`/`story_members` on
every item card, and the `group_stories=1` mode that collapses a story to its single richest
("primary") card.

`services._fetchall`/`_fetchone` and `eoa.memory.relational.get_story_groups_for_items` are
monkeypatched at the module level -- no live DB, no network, no LLM (same policy as
`tests/unit/test_api_smoke.py`).

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_api_items_story_grouping.py -q``
"""

from __future__ import annotations

from typing import Any

import pytest

from eoa.api import services


def _row(id: int, **overrides: Any) -> dict[str, Any]:
    base = {
        "id": id,
        "title": f"item {id}",
        "url": f"https://example.test/{id}",
        "source_name": "Source",
        "published_at": "2026-09-16T10:00:00Z",
        "lang": "en",
        "domain": "airborne_pods",
        "score": 50,
        "level": "yellow",
        "summary_he": None,
        "so_what_he": None,
        "story_id": None,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def no_corroboration(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_attach_corroboration` does its own DB call and degrades to the "unknown" default on
    failure -- stub it out entirely so these tests only exercise the story-grouping logic."""
    monkeypatch.setattr(services, "_attach_corroboration", lambda cards: None)


class TestItemCardStoryDefaults:
    def test_lone_item_defaults_to_its_own_one_member_story(self) -> None:
        card = services._item_card(_row(1))
        assert card["story_size"] == 1
        assert card["story_primary"] is True
        assert card["story_members"] == []


class TestAttachStoryInfo:
    def test_multi_member_story_marks_one_primary_and_lists_the_rest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        groups = {
            100: [
                {"id": 100, "title": "richest", "source_name": "A", "lang": "en", "url": "u1",
                 "summary_he": "s", "so_what_he": "sw", "score": 8, "published_at": "2026-09-16"},
                {"id": 101, "title": "thinner", "source_name": "B", "lang": "en", "url": "u2",
                 "summary_he": None, "so_what_he": None, "score": 3, "published_at": None},
            ]
        }
        import eoa.memory.relational as relational

        monkeypatch.setattr(relational, "get_story_groups_for_items", lambda ids: groups)

        cards = [services._item_card(_row(100, story_id=100)), services._item_card(_row(101, story_id=100))]
        services._attach_story_info(cards)

        primary = next(c for c in cards if c["id"] == 100)
        other = next(c for c in cards if c["id"] == 101)
        assert primary["story_size"] == 2
        assert primary["story_primary"] is True
        assert [m["id"] for m in primary["story_members"]] == [101]
        assert other["story_primary"] is False
        assert other["story_size"] == 2

    def test_lookup_failure_degrades_to_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import eoa.memory.relational as relational

        def _boom(ids: list[int]) -> Any:
            raise RuntimeError("db down")

        monkeypatch.setattr(relational, "get_story_groups_for_items", _boom)
        cards = [services._item_card(_row(1))]
        services._attach_story_info(cards)  # must not raise
        assert cards[0]["story_size"] == 1
        assert cards[0]["story_primary"] is True


class TestListItemsGroupStories:
    def test_group_stories_collapses_to_one_card_per_story(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Two items share a story_id -- `group_stories=True` must return exactly one card (the
        richer member), with `story_size == 2` and `total == 1` (one STORY, not two items)."""
        all_ids_rows = [{"id": 200}, {"id": 201}]
        full_rows = {200: _row(200, story_id=200, score=8), 201: _row(201, story_id=200, score=3)}
        group_members = [
            {"id": 200, "title": "richer", "source_name": "A", "lang": "en", "url": "u1",
             "summary_he": "s", "so_what_he": "sw", "score": 8, "published_at": "2026-09-16"},
            {"id": 201, "title": "thinner", "source_name": "B", "lang": "en", "url": "u2",
             "summary_he": None, "so_what_he": None, "score": 3, "published_at": "2026-09-16"},
        ]

        def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
            if "SELECT i.id FROM items i WHERE" in query:
                return all_ids_rows
            if "WHERE i.id = ANY(%(ids)s)" in query:
                ids = params["ids"]
                return [full_rows[i] for i in ids if i in full_rows]
            raise AssertionError(f"unexpected query: {query}")

        monkeypatch.setattr(services, "_fetchall", fake_fetchall)
        import eoa.memory.relational as relational

        monkeypatch.setattr(relational, "get_story_groups_for_items", lambda ids: {200: group_members})

        total, cards = services.list_items(group_stories=True, page=1, page_size=50)
        assert total == 1
        assert len(cards) == 1
        assert cards[0]["id"] == 200
        assert cards[0]["story_size"] == 2
        assert cards[0]["story_primary"] is True

    def test_group_stories_false_is_unchanged_one_card_per_item(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_fetchone(query: str, params: Any = None) -> dict[str, Any]:
            return {"n": 2}

        def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
            return [_row(200, story_id=200), _row(201, story_id=200)]

        monkeypatch.setattr(services, "_fetchone", fake_fetchone)
        monkeypatch.setattr(services, "_fetchall", fake_fetchall)
        import eoa.memory.relational as relational

        monkeypatch.setattr(relational, "get_story_groups_for_items", lambda ids: {})

        total, cards = services.list_items(group_stories=False, page=1, page_size=50)
        assert total == 2
        assert len(cards) == 2

    def test_empty_filtered_pool_returns_no_cards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "_fetchall", lambda query, params=None: [])
        total, cards = services.list_items(group_stories=True)
        assert total == 0
        assert cards == []
