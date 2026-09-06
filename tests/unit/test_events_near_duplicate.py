"""Unit tests for Q3-6b (docs/qa/findings_Q3_r2.md): near-duplicate events for the same item that
differ only by `kind` (e.g. item 70's investment/contract event, read once as `contract_award` and
once as `test`) are merged into a single row instead of creating a second one -- extending
``eoa.memory.relational.insert_event``'s existing exact-match (item_id, kind, lower(title)) upsert
with a character-level (difflib) near-duplicate check across different kinds.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_events_near_duplicate.py -q``
"""

from __future__ import annotations

import pytest

from eoa.memory import relational


class _FakeCursor:
    def __init__(self, *, fetchall_result: list | None = None, fetchone_result: dict | None = None) -> None:
        self._fetchall = fetchall_result or []
        self._fetchone = fetchone_result
        self.calls: list[tuple[str, dict]] = []

    def execute(self, query: str, params: dict) -> None:
        self.calls.append((query, params))

    def fetchall(self) -> list:
        return self._fetchall

    def fetchone(self) -> dict | None:
        return self._fetchone

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self, row_factory=None) -> _FakeCursor:
        return self._cursor

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _ConnectionQueue:
    """``relational.connection`` is called once per ``with connection() as conn, conn.cursor() ...``
    block -- this replays a fixed sequence of prepared cursors, one per call, so a test can assert
    on each SQL statement independently."""

    def __init__(self, cursors: list[_FakeCursor]) -> None:
        self._cursors = list(cursors)

    def __call__(self) -> _FakeConnection:
        return _FakeConnection(self._cursors.pop(0))


class TestEventTitleSimilarity:
    def test_identical_titles_are_1(self) -> None:
        assert relational.event_title_similarity("Elbit wins pod contract", "Elbit wins pod contract") == 1.0

    def test_completely_different_titles_score_low(self) -> None:
        assert relational.event_title_similarity("Elbit wins pod contract", "Rafael tests new sensor") < 0.5

    def test_near_identical_titles_score_high(self) -> None:
        score = relational.event_title_similarity(
            "Elbit Systems Ltd wins a new advanced pod contract award from the USAF",
            "Elbit Systems Ltd wins a new advanced pod contract award from USAF",
        )
        assert score >= 0.9

    def test_empty_or_none_titles_never_match(self) -> None:
        assert relational.event_title_similarity("", "Elbit wins pod contract") == 0.0
        assert relational.event_title_similarity(None, None) == 0.0

    def test_item_70_real_near_duplicate_from_finding(self) -> None:
        """Regression for the actual Q3-6b motivating example (docs/qa/findings_Q3_r2.md): a
        single one-letter prefix ("ל") difference on one word out of six/seven -- token-Jaccard
        scored this ~0.71 (below threshold); difflib correctly scores it near 1.0."""
        score = relational.event_title_similarity(
            "זכייה במכרז לפיתוח פלטפורמת פיקוד ושליטה",
            "זכייה במכרז פיתוח פלטפורמת פיקוד ושליטה",
        )
        assert score >= relational.EVENT_TITLE_DEDUP_THRESHOLD


class TestMoreSpecificEventKind:
    def test_contract_award_beats_test(self) -> None:
        assert relational.more_specific_event_kind("test", "contract_award") == "contract_award"
        assert relational.more_specific_event_kind("contract_award", "test") == "contract_award"

    def test_acquisition_beats_deployment(self) -> None:
        assert relational.more_specific_event_kind("deployment", "acquisition") == "acquisition"

    def test_unranked_kinds_keep_first(self) -> None:
        assert relational.more_specific_event_kind("other", "other") == "other"


class TestInsertEventMergesNearDuplicateAcrossKind:
    def test_near_duplicate_different_kind_merges_and_upgrades_kind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        title = "Elbit Systems wins pod contract from USAF"
        existing = {
            "id": 70,
            "kind": "test",
            "title": title,
            "date": None,
            "amount_usd": None,
            "currency": None,
            "parties": None,
            "customer": None,
            "program": None,
            "summary_he": None,
            "confidence": 0.4,
        }
        find_cursor = _FakeCursor(fetchall_result=[existing])
        update_cursor = _FakeCursor()
        monkeypatch.setattr(relational, "connection", _ConnectionQueue([find_cursor, update_cursor]))

        event_id = relational.insert_event(
            item_id=70, kind="contract_award", title=title, amount_usd=5_000_000, confidence=0.8
        )

        assert event_id == 70
        assert len(find_cursor.calls) == 1
        # round-3: the candidate query now loads every titled event of the item (same-kind
        # re-wordings are matched in Python, see _best_duplicate_candidate)
        assert "WHERE item_id = %(item_id)s AND title IS NOT NULL" in find_cursor.calls[0][0]
        assert len(update_cursor.calls) == 1
        query, params = update_cursor.calls[0]
        assert "UPDATE events SET" in query
        assert params["kind"] == "contract_award"  # more specific than the existing "test"
        assert params["id"] == 70

    def test_no_near_duplicate_falls_through_to_normal_upsert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        find_cursor = _FakeCursor(fetchall_result=[])
        insert_cursor = _FakeCursor(fetchone_result={"id": 99})
        monkeypatch.setattr(relational, "connection", _ConnectionQueue([find_cursor, insert_cursor]))

        event_id = relational.insert_event(
            item_id=70, kind="contract_award", title="A brand new distinct event"
        )

        assert event_id == 99
        query, _params = insert_cursor.calls[0]
        assert "ON CONFLICT (item_id, kind, (lower(title)))" in query

    def test_title_less_event_skips_near_duplicate_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No title -> nothing to compare identity on -- go straight to the normal insert (which
        never conflicts for a NULL title either, per the existing Q3-6 behaviour)."""
        insert_cursor = _FakeCursor(fetchone_result={"id": 7})
        monkeypatch.setattr(relational, "connection", _ConnectionQueue([insert_cursor]))

        event_id = relational.insert_event(item_id=70, kind="other")

        assert event_id == 7

    def test_low_similarity_different_kind_does_not_merge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        existing = {
            "id": 70,
            "kind": "test",
            "title": "Completely unrelated title here",
            "date": None,
            "amount_usd": None,
            "currency": None,
            "parties": None,
            "customer": None,
            "program": None,
            "summary_he": None,
            "confidence": 0.4,
        }
        find_cursor = _FakeCursor(fetchall_result=[existing])
        insert_cursor = _FakeCursor(fetchone_result={"id": 101})
        monkeypatch.setattr(relational, "connection", _ConnectionQueue([find_cursor, insert_cursor]))

        event_id = relational.insert_event(
            item_id=70, kind="contract_award", title="Elbit wins pod contract from USAF"
        )

        assert event_id == 101


class TestFindKindDiffDuplicateGroupsRepair:
    """Q3-6b repair pass: scripts/repair_events_dedup.py's find_kind_diff_duplicate_groups (pure,
    no DB)."""

    @staticmethod
    def _load_script():
        import importlib.util
        from pathlib import Path

        script_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "repair_events_dedup.py"
        spec = importlib.util.spec_from_file_location("repair_events_dedup", script_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        return mod

    def test_groups_same_item_different_kind_near_duplicate_titles(self) -> None:
        red = self._load_script()
        rows = [
            {"id": 1, "item_id": 70, "kind": "test", "title": "Elbit wins pod contract from USAF"},
            {"id": 2, "item_id": 70, "kind": "contract_award", "title": "Elbit wins pod contract from USAF"},
        ]
        groups = red.find_kind_diff_duplicate_groups(rows)
        assert len(groups) == 1
        assert groups[0]["keep"]["id"] == 1
        assert [r["id"] for r in groups[0]["merge"]] == [2]

    def test_same_kind_is_not_grouped_here(self) -> None:
        red = self._load_script()
        rows = [
            {"id": 1, "item_id": 70, "kind": "test", "title": "Elbit wins pod contract"},
            {"id": 2, "item_id": 70, "kind": "test", "title": "Elbit wins pod contract"},
        ]
        assert red.find_kind_diff_duplicate_groups(rows) == []

    def test_different_items_never_grouped(self) -> None:
        red = self._load_script()
        rows = [
            {"id": 1, "item_id": 70, "kind": "test", "title": "Elbit wins pod contract from USAF"},
            {"id": 2, "item_id": 71, "kind": "contract_award", "title": "Elbit wins pod contract from USAF"},
        ]
        assert red.find_kind_diff_duplicate_groups(rows) == []

    def test_dissimilar_titles_not_grouped(self) -> None:
        red = self._load_script()
        rows = [
            {"id": 1, "item_id": 70, "kind": "test", "title": "Elbit wins pod contract from USAF"},
            {
                "id": 2,
                "item_id": 70,
                "kind": "contract_award",
                "title": "Completely unrelated event happened",
            },
        ]
        assert red.find_kind_diff_duplicate_groups(rows) == []
