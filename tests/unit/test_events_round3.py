"""Round-3 QA-loop fixes for the events table (docs/qa/loop/round_2_judge.md, D3):

* same-kind re-worded duplicates (item 81's three Norkin appointments) now merge on the way in;
* parties are unioned on merge instead of "keep existing unless empty" (item 50 / Palantir);
* `amount_usd` is anchored to the source text's magnitude word ("$464.8 million" -> 464800000);
* `merge_duplicate_events` collapses clusters already stored, dry-run by default.
"""

from __future__ import annotations

from typing import Any

import pytest

from eoa.memory import relational
from eoa.memory.relational import (
    EVENT_SAME_KIND_DEDUP_THRESHOLD,
    _best_duplicate_candidate,
    _distinct_numbers,
    _distinct_proper_nouns,
    _parties_conflict,
    _union_parties,
    event_semantic_similarity,
    merge_duplicate_events,
    same_kind_duplicate,
)
from eoa.pipeline.analyze import normalize_amount_from_source

NORKIN_A = "מינוי אמיתי נורקין לראש פעילות אנדוריל בישראל"
NORKIN_B = "מינוי עמירם נורקין למנהל הפעילות הישראלית של אנדוריל"
NORKIN_C = "מינוי אמירם נורקין לעמוד בראש פעילות אנדוריל בישראל"
FUND_A = "סבב גיוס הון חדש של אנדוריל"
FUND_B = "גיוס הון משמעותי לאנדוריל"
ELBIT_A = "שיתוף פעולה בין אנדוריל לאלביט מערכות"
ELBIT_B = "שיתוף פעולה עם אלביט מערכות"
ENTRY = "כניסת אנדוריל לשוק הישראלי"


# ---------------------------------------------------------------------------------------------
# similarity
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a, b", [(NORKIN_A, NORKIN_B), (NORKIN_A, NORKIN_C), (FUND_A, FUND_B), (ELBIT_A, ELBIT_B)]
)
def test_reworded_same_event_clears_same_kind_threshold(a: str, b: str) -> None:
    assert event_semantic_similarity(a, b) >= EVENT_SAME_KIND_DEDUP_THRESHOLD


@pytest.mark.parametrize("a, b", [(FUND_A, ENTRY), (NORKIN_A, ENTRY), (ELBIT_B, FUND_B)])
def test_distinct_events_stay_below_threshold(a: str, b: str) -> None:
    assert event_semantic_similarity(a, b) < EVENT_SAME_KIND_DEDUP_THRESHOLD


def test_similarity_is_symmetric_and_bounded() -> None:
    assert event_semantic_similarity(NORKIN_A, NORKIN_B) == pytest.approx(
        event_semantic_similarity(NORKIN_B, NORKIN_A)
    )
    assert 0.0 <= event_semantic_similarity("a", "b") <= 1.0
    assert event_semantic_similarity("", NORKIN_A) == 0.0
    assert event_semantic_similarity(None, None) == 0.0


@pytest.mark.parametrize(
    "a, b",
    [
        (NORKIN_A, NORKIN_B),  # weak rule: 4/7 shared content words + 66% characters
        (NORKIN_A, NORKIN_C),
        (FUND_A, FUND_B),
        (ELBIT_A, ELBIT_B),
        ("זכייה בחוזה נוסף מצבא ארה״ב", "UVision Air ו-Mistral זכו בחוזה נוסף מצבא ארה״ב"),
        ("פרסום מאמר מחקר", "פרסום מאמר מחקר ב-arXiv"),
        ("Anduril wins $65m US Army contract", "Anduril wins $65m US Army contract to build TITAN hardware"),
        ("יוון אישרה רכש הגנה אווירית ישראלי", "יוון אישרה עסקת רכש היסטורית של מערכות הגנה אווירית מישראל"),
    ],
)
def test_same_kind_duplicate_accepts_reworded_same_event(a: str, b: str) -> None:
    assert same_kind_duplicate(a, b)


@pytest.mark.parametrize(
    "a, b",
    [
        # live false positives caught in the round-3 dry run (docs/qa/loop/round_3_fixes.md)
        ("השלכות על שוק ההגנה האווירית", "השלכות על תעשיות ישראליות"),  # 0.60 chars, 1 shared word
        ("שיגור לוויין אופק 19", "שיגור לוויין דור 1"),  # two satellites
        ("T-REX 2026", "T-REX 25-2"),  # two exercises
        (
            "XTEND חתמה על הסכמים עם משרד ההגנה האמריקאי",
            "XTEND חתמה על הסכם רב-שנתי עם משרד הביטחון של מדינה חברה בנאט״ו",
        ),
        ("השקת Nexus Observer", "השקת Nexus Sentinel"),
        (FUND_A, ENTRY),
    ],
)
def test_same_kind_duplicate_rejects_distinct_events(a: str, b: str) -> None:
    assert not same_kind_duplicate(a, b)


def test_distinct_numbers_guard() -> None:
    assert _distinct_numbers("שיגור לוויין אופק 19", "שיגור לוויין דור 1")
    assert not _distinct_numbers("XTEND הונפקה בשווי 1.5 מיליארד", "XTEND הגיעה לשווי של 1.5 מיליארד")
    assert not _distinct_numbers("Anduril wins $65m contract", "Anduril wins contract")
    assert not _distinct_numbers("a", "b")


def test_hebrew_prefix_stripping_matches_clitic_forms() -> None:
    # "לאנדוריל" vs "אנדוריל", "הפעילות" vs "פעילות" -- same content word
    assert event_semantic_similarity("גיוס הון לאנדוריל", "גיוס הון אנדוריל") >= 0.9


# ---------------------------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------------------------


def test_distinct_proper_nouns_blocks_two_products_but_not_one_sided_names() -> None:
    assert _distinct_proper_nouns("השקת Nexus Observer", "השקת Nexus Sentinel")
    assert not _distinct_proper_nouns("השקת Nexus Observer", "השקת Nexus")
    assert not _distinct_proper_nouns("US Army awards production contract", "US Army awards laser contract")
    assert not _distinct_proper_nouns(NORKIN_A, NORKIN_B)


def test_parties_conflict_only_when_both_non_empty_and_disjoint() -> None:
    assert not _parties_conflict([], ["Anduril"])
    assert not _parties_conflict(None, None)
    assert not _parties_conflict(["Anduril", "Elbit"], ["Elbit Systems"])
    assert not _parties_conflict(["anduril"], ["Anduril"])
    assert _parties_conflict(["Rafael"], ["Anduril"])


def test_union_parties_keeps_existing_order_and_dedupes_prefix_forms() -> None:
    assert _union_parties(["US Army", "Anduril"], ["Palantir", "anduril", "US Army"]) == [
        "US Army",
        "Anduril",
        "Palantir",
    ]
    assert _union_parties(["Elbit Systems"], ["Elbit"]) == ["Elbit Systems"]
    assert _union_parties(None, [" ", "X"]) == ["X"]


# ---------------------------------------------------------------------------------------------
# candidate selection (pure)
# ---------------------------------------------------------------------------------------------


def _cands() -> list[dict[str, Any]]:
    return [
        {"id": 22, "kind": "m_and_a", "title": NORKIN_A, "parties": ["Anduril", "Amikam Norkin"]},
        {"id": 23, "kind": "m_and_a", "title": FUND_A, "parties": ["Anduril"]},
        {"id": 24, "kind": "m_and_a", "title": ELBIT_A, "parties": ["Anduril", "Elbit"]},
    ]


def test_same_kind_reworded_title_matches_best_candidate() -> None:
    assert _best_duplicate_candidate("m_and_a", NORKIN_B, ["Anduril", "Amikam Norkin"], _cands())["id"] == 22
    assert _best_duplicate_candidate("m_and_a", NORKIN_C, [], _cands())["id"] == 22
    assert _best_duplicate_candidate("m_and_a", FUND_B, ["Anduril"], _cands())["id"] == 23
    assert (
        _best_duplicate_candidate("m_and_a", ELBIT_B, ["Anduril", "Elbit Systems", "Elbit"], _cands())["id"]
        == 24
    )


def test_same_kind_but_unrelated_title_is_not_matched() -> None:
    assert _best_duplicate_candidate("m_and_a", ENTRY, ["Anduril"], _cands()) is None


def test_conflicting_parties_block_a_same_kind_merge() -> None:
    assert _best_duplicate_candidate("m_and_a", FUND_B, ["Rafael"], _cands()) is None


def test_two_products_are_not_merged() -> None:
    cands = [{"id": 1, "kind": "launch", "title": "השקת Nexus Observer", "parties": []}]
    assert _best_duplicate_candidate("launch", "השקת Nexus Sentinel", [], cands) is None


def test_verbatim_same_kind_title_is_left_to_the_unique_index() -> None:
    assert _best_duplicate_candidate("m_and_a", NORKIN_A.upper(), [], _cands()) is None


def test_different_kind_still_needs_character_level_near_duplicate() -> None:
    # Q3-6b behaviour unchanged: a different kind needs the 0.9 character bar
    assert _best_duplicate_candidate("partnership", ELBIT_B, [], _cands()) is None
    assert _best_duplicate_candidate("partnership", ELBIT_A + " ", [], _cands())["id"] == 24


# ---------------------------------------------------------------------------------------------
# amount normalisation
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "amount, text, expected, mag",
    [
        (464.8, "US Army awards $464.8 million production contract to AeroVironment", 464800000.0, "million"),
        (464.8, "the $464.8M award", 464800000.0, "m"),
        (540.7, "Tekever acquires Flowcopter for €540.7 million", 540700000.0, "million"),
        (10, "Anduril raised $10 billion", 10000000000.0, "billion"),
        (2.5, "עסקה בהיקף 2.5 מיליארד דולר", 2500000000.0, "מיליארד"),
        (1595, "Nexus Observer priced at $1,595 per unit", 1595, None),
        (200000000, "a $200 million deal", 200000000, None),
        (464.8, "no figure here", 464.8, None),
        (None, "anything", None, None),
    ],
)
def test_normalize_amount_from_source(
    amount: float | None, text: str, expected: float | None, mag: str | None
) -> None:
    assert normalize_amount_from_source(amount, text) == (expected, mag)


def test_normalize_amount_ignores_a_different_number() -> None:
    # 12 appears only as part of "$120 million": must not scale 12
    assert normalize_amount_from_source(12, "worth $120 million") == (12, None)


# ---------------------------------------------------------------------------------------------
# maintenance merge (DB stubbed)
# ---------------------------------------------------------------------------------------------


class _Cur:
    def __init__(self, rows: list[dict[str, Any]], log: list[tuple[str, dict[str, Any]]]):
        self._rows, self._log = rows, log

    def execute(self, q: str, params: dict[str, Any] | None = None) -> None:
        self._log.append((q, params or {}))

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def __enter__(self) -> _Cur:
        return self

    def __exit__(self, *a: object) -> None:
        return None


class _Conn:
    def __init__(self, rows: list[dict[str, Any]], log: list[tuple[str, dict[str, Any]]]):
        self._rows, self._log = rows, log

    def cursor(self, row_factory: object = None) -> _Cur:
        return _Cur(self._rows, self._log)

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def _item81_rows() -> list[dict[str, Any]]:
    base = {
        "date": None,
        "amount_usd": None,
        "currency": None,
        "customer": None,
        "program": None,
        "summary_he": None,
    }
    return [
        {
            **base,
            "id": 22,
            "item_id": 81,
            "kind": "m_and_a",
            "title": NORKIN_A,
            "parties": ["Anduril", "Amikam Norkin"],
            "confidence": 0.8,
        },
        {
            **base,
            "id": 23,
            "item_id": 81,
            "kind": "m_and_a",
            "title": FUND_A,
            "parties": ["Anduril"],
            "amount_usd": 1e10,
            "confidence": 0.7,
        },
        {
            **base,
            "id": 24,
            "item_id": 81,
            "kind": "m_and_a",
            "title": ELBIT_A,
            "parties": ["Anduril", "Elbit"],
            "confidence": 0.7,
        },
        {
            **base,
            "id": 221,
            "item_id": 81,
            "kind": "m_and_a",
            "title": NORKIN_B,
            "parties": ["Anduril", "Amikam Norkin"],
            "confidence": 0.9,
        },
        {
            **base,
            "id": 222,
            "item_id": 81,
            "kind": "m_and_a",
            "title": FUND_B,
            "parties": ["Anduril"],
            "amount_usd": 1e10,
            "confidence": 0.6,
        },
        {
            **base,
            "id": 223,
            "item_id": 81,
            "kind": "m_and_a",
            "title": ELBIT_B,
            "parties": ["Anduril", "Elbit Systems", "Elbit"],
            "confidence": 0.7,
        },
        {
            **base,
            "id": 224,
            "item_id": 81,
            "kind": "m_and_a",
            "title": ENTRY,
            "parties": ["Anduril"],
            "confidence": 0.5,
        },
        {
            **base,
            "id": 245,
            "item_id": 81,
            "kind": "m_and_a",
            "title": NORKIN_C,
            "parties": [],
            "date": "2026-09-01",
            "confidence": 0.8,
        },
    ]


def test_merge_duplicate_events_dry_run_collapses_item81_cluster(monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(relational, "connection", lambda: _Conn(_item81_rows(), log))
    merges = merge_duplicate_events(item_id=81, dry_run=True)
    removed = sorted(m["removed_id"] for m in merges)
    assert removed == [221, 222, 223, 245]
    kept = {m["removed_id"]: m["kept_id"] for m in merges}
    assert kept == {221: 22, 222: 23, 223: 24, 245: 22}
    # dry run: only the initial SELECT hit the cursor
    assert all(q.lstrip().upper().startswith("SELECT") for q, _ in log)


def test_merge_duplicate_events_apply_writes_survivor_and_deletes_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(relational, "connection", lambda: _Conn(_item81_rows(), log))
    merge_duplicate_events(item_id=81, dry_run=False)
    updates = [p for q, p in log if q.lstrip().upper().startswith("UPDATE")]
    deletes = [p["id"] for q, p in log if q.lstrip().upper().startswith("DELETE")]
    assert sorted(deletes) == [221, 222, 223, 245]
    norkin = [u for u in updates if u["id"] == 22][-1]
    assert norkin["date"] == "2026-09-01"  # filled from event 245
    assert norkin["confidence"] == pytest.approx(0.9)  # max over the cluster
    assert norkin["parties"] == ["Anduril", "Amikam Norkin"]


# ---------------------------------------------------------------------------------------------
# F09 remainder (SOL-REVIEW4-2026-09-24 backlog): `delete_stale_analyze_events` -- clears an
# item's own previously-analyze-extracted events before a re-analysis persists its new set, but
# must NOT touch a row a manual cross-item reconciliation repair (`scripts/repair_round14_events.py
# --apply`) merged another item's event into (`source_item_ids` non-empty).
# ---------------------------------------------------------------------------------------------


class _DeleteCur:
    """Minimal fake cursor supporting `.rowcount`, unlike `_Cur` above (which `merge_duplicate_events`
    never needs since it always knows exactly which ids it deleted)."""

    def __init__(self, rowcount: int, log: list[tuple[str, dict[str, Any]]]):
        self.rowcount = rowcount
        self._log = log

    def execute(self, q: str, params: dict[str, Any] | None = None) -> None:
        self._log.append((q, params or {}))

    def __enter__(self) -> _DeleteCur:
        return self

    def __exit__(self, *a: object) -> None:
        return None


class _DeleteConn:
    def __init__(self, rowcount: int, log: list[tuple[str, dict[str, Any]]]):
        self._rowcount, self._log = rowcount, log

    def cursor(self, row_factory: object = None) -> _DeleteCur:
        return _DeleteCur(self._rowcount, self._log)

    def __enter__(self) -> _DeleteConn:
        return self

    def __exit__(self, *a: object) -> None:
        return None


class TestDeleteStaleAnalyzeEvents:
    def test_scopes_delete_to_item_id_and_excludes_merged_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        log: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(relational, "connection", lambda: _DeleteConn(3, log))

        n = relational.delete_stale_analyze_events(91)

        assert n == 3  # the fake cursor's rowcount is returned straight through
        assert len(log) == 1
        query, params = log[0]
        assert query.lstrip().upper().startswith("DELETE FROM EVENTS")
        assert params == {"item_id": 91}
        # THE regression check (both halves): scoped to this item...
        assert "item_id = %(item_id)s" in query
        # ...and a row that a manual cross-item reconciliation repair merged another item's event
        # into (non-empty `source_item_ids`) must be excluded, not blindly deleted.
        assert "array_length(source_item_ids, 1) IS NULL" in query

    def test_returns_zero_rows_deleted_when_nothing_matched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        log: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(relational, "connection", lambda: _DeleteConn(0, log))

        assert relational.delete_stale_analyze_events(12345) == 0
