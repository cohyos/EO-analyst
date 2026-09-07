"""Unit tests for the R13-reports package (round-13 QA loop, closing round-12 judge worst-list
items #1/#5/#9 plus the weekly-indicator finding -- docs/qa/loop/round_12_judge.md).

Every DB-touching function is monkeypatched at the module level (no live DB, no Ollama/LLM calls),
mirroring the conventions already used by tests/unit/test_reports_round9.py/round10.py/round12.py
and tests/unit/test_reports_round6.py's own repair-script import pattern.

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_reports_round13.py -q``
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from eoa.report import indicators
from eoa.report import product_line as pl

_REPAIR_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "repair_round13.py"
_spec = importlib.util.spec_from_file_location("repair_round13", _REPAIR_SCRIPT_PATH)
repair = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(repair)


# --------------------------------------------------------------------------
# Finding #1 -- pl_mws_eo patent-dedupe kind-code normalization
# (round-12 judge D7 worst #1: US7378626 vs US7378626B2, US20230082239A1 vs US20230082239).
# --------------------------------------------------------------------------


class TestNormalizePubNumber:
    def test_strips_b2_kind_code(self) -> None:
        assert pl._normalize_pub_number("US7378626B2") == "US7378626"

    def test_bare_number_unchanged(self) -> None:
        assert pl._normalize_pub_number("US7378626") == "US7378626"

    def test_strips_a1_kind_code(self) -> None:
        assert pl._normalize_pub_number("US20230082239A1") == "US20230082239"

    def test_bare_long_number_unchanged(self) -> None:
        assert pl._normalize_pub_number("US20230082239") == "US20230082239"

    def test_strips_whitespace_and_slash_formatting(self) -> None:
        # Some upstream sources format as "US 2023/0082239 A1" (spaces, a slash).
        assert pl._normalize_pub_number("US 2023/0082239 A1") == "US20230082239"

    def test_none_input_returns_none(self) -> None:
        assert pl._normalize_pub_number(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert pl._normalize_pub_number("") is None

    def test_lowercase_is_uppercased(self) -> None:
        assert pl._normalize_pub_number("us7378626b2") == "US7378626"

    def test_does_not_strip_a_genuine_trailing_letter_run_not_ending_in_digit(self) -> None:
        # A base number ending in a letter (not a digit) before the "kind code" position means the
        # kind-code regex's own `.+\d` requirement for the base can't be satisfied -- the whole
        # string is returned unchanged rather than mis-stripped.
        assert pl._normalize_pub_number("USABC") == "USABC"


class TestDedupePatentsByPubNumber:
    def _patent(self, n: str, pub_number: str | None) -> dict:
        return {"id": n, "pub_number": pub_number, "title": f"patent {n}"}

    def test_kind_code_variants_collapse_keeping_first_seen(self) -> None:
        # collect_patents orders newest-first, so "first seen" IS the newest -- exactly the
        # round-12 judge's own reproduction case (DB ids 57/55).
        rows = [self._patent("newer-57", "US7378626B2"), self._patent("older-55", "US7378626")]
        kept = pl._dedupe_patents_by_pub_number(rows)
        assert len(kept) == 1
        assert kept[0]["id"] == "newer-57"

    def test_reverse_order_kind_code_variant_also_collapses(self) -> None:
        # The round-12 judge's second reproduction pair (DB ids 45/77) -- the bare-number variant
        # appearing first in this ordering.
        rows = [self._patent("bare-45", "US20230082239"), self._patent("kinded-77", "US20230082239A1")]
        kept = pl._dedupe_patents_by_pub_number(rows)
        assert len(kept) == 1
        assert kept[0]["id"] == "bare-45"

    def test_distinct_patents_all_kept(self) -> None:
        rows = [self._patent("a", "US1111111A1"), self._patent("b", "US2222222B2")]
        kept = pl._dedupe_patents_by_pub_number(rows)
        assert len(kept) == 2

    def test_rows_with_no_pub_number_are_never_collapsed_together(self) -> None:
        rows = [self._patent("x", None), self._patent("y", None), self._patent("z", "")]
        kept = pl._dedupe_patents_by_pub_number(rows)
        assert len(kept) == 3


# --------------------------------------------------------------------------
# Finding #2 -- weekly indicator evidence matching (round-12 judge D6 worst-list weekly-indicator
# item: an Estonia/David's Sling row stayed evidence-less despite a genuinely-matching item).
# --------------------------------------------------------------------------


class TestShortNameNamePhrasesHe:
    def test_extracts_short_two_word_system_name(self) -> None:
        # "קלע דוד" (David's Sling) -- two 3-letter words, neither alone clears the per-word filter.
        text = "החלטת אסטוניה בנוגע לרכש מערכת קלע דוד תתקבל תוך כחודשיים."
        assert "קלע דוד" in indicators._short_name_phrases_he(text)

    def test_stopword_prefix_form_excluded(self) -> None:
        # "בתוך" ("within") strips (via the shared "ב" prefix rule) to "תוך", a real _STOP_HE
        # entry -- the bigram built from it and its short neighbor must not surface as a phrase.
        text = "קלע דוד בתוך מספר חודשים."
        phrases = indicators._short_name_phrases_he(text)
        assert not any("בתוך" in p for p in phrases)

    def test_words_outside_length_range_not_paired(self) -> None:
        # "מערכת" (5 letters) is outside the 3-4 letter short-phrase range on its own -- already
        # handled by the ordinary single-word filter, not this bigram path.
        text = "מערכת קלע דוד החדשה"
        phrases = indicators._short_name_phrases_he(text)
        assert "מערכת קלע" not in phrases

    def test_no_short_adjacent_pair_yields_empty_set(self) -> None:
        text = "להערכתנו מגמת ההשקות תימשך ברבעון הקרוב."
        assert indicators._short_name_phrases_he(text) == set()

    def test_none_input_returns_empty_set(self) -> None:
        assert indicators._short_name_phrases_he(None) == set()


class TestItemMatchesIndicatorShortPhrase:
    def test_estonia_david_sling_row_now_matches_its_own_item(self) -> None:
        # The live round-12 case (weekly indicator id 16 vs item id 6163): the row and the item
        # share "אסטוניה" (1 hit) and "קלע דוד" (now a 2nd hit via the short-phrase widening) --
        # clears the 2-term bar this module has applied since round 9.
        text = "חתימה צפויה על חוזה הצטיידות רשמי של אסטוניה במערכות היירוט קלע דוד בתוך מספר חודשים."
        item = {
            "title": 'אסטוניה שוקלת לרכוש מערכות הגנ"א קלע דוד תוצרת ישראל',
            "summary_he": "אסטוניה שוקלת לרכוש את מערכת ההגנה האווירית קלע דוד תוצרת רפאל.",
            "so_what_he": "",
        }
        assert indicators._item_matches_indicator(text, item) is True

    def test_single_short_phrase_hit_alone_is_still_insufficient(self) -> None:
        # Same precision bar as every other Hebrew-term path in this module: one hit (even a
        # phrase hit) is not enough without a second independent signal.
        text = "קלע דוד יוזכר להערכתנו בכנס הקרוב."
        item = {"title": "קלע דוד הוצג בתערוכה", "summary_he": "", "so_what_he": ""}
        assert indicators._item_matches_indicator(text, item) is False

    def test_unrelated_item_still_does_not_match(self) -> None:
        text = "חתימה צפויה על חוזה הצטיידות רשמי של אסטוניה במערכות היירוט קלע דוד בתוך מספר חודשים."
        item = {"title": "דוח כלכלי כללי לא קשור", "summary_he": "", "so_what_he": ""}
        assert indicators._item_matches_indicator(text, item) is False


# --------------------------------------------------------------------------
# Finding #3 -- scripts/repair_round13.py: 11 in-scope (red/orange/yellow) items missing
# so_what_he (round-12 judge D2/D1 worst #5).
# --------------------------------------------------------------------------


class _FakeCur:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        return None

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self, row_factory=None):
        return _FakeCur(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_connection_returning(rows):
    def _conn(timeout=5):
        return _FakeConn(rows)

    return _conn


class TestFindMissingSoWhatItems:
    def test_returns_rows_from_query(self, monkeypatch) -> None:
        rows = [
            {"id": 23, "level": "yellow", "domain": "out_of_scope", "title": "t23"},
            {"id": 6872, "level": "red", "domain": "air_defense", "title": "t6872"},
        ]
        monkeypatch.setattr(repair, "connection", _fake_connection_returning(rows))
        result = repair.find_missing_so_what_items()
        assert [r["id"] for r in result] == [23, 6872]


class TestRepairSoWhat:
    def _targets(self):
        return [
            {"id": 23, "level": "yellow", "domain": "out_of_scope", "title": "t23"},
            {"id": 6872, "level": "red", "domain": "air_defense", "title": "t6872"},
        ]

    def test_dry_run_lists_targets_without_calling_llm(self, monkeypatch) -> None:
        monkeypatch.setattr(repair, "find_missing_so_what_items", lambda: self._targets())
        called = {"n": 0}

        def _boom(*a, **kw):
            called["n"] += 1
            return "should not be called"

        monkeypatch.setattr(repair, "repair_so_what_text", _boom)
        budget = repair.LLMBudget(15)
        report = repair.repair_so_what(apply=False, budget=budget)
        assert report["target_count"] == 2
        assert called["n"] == 0
        assert budget.used == 0

    def test_apply_writes_valid_repair(self, monkeypatch) -> None:
        monkeypatch.setattr(repair, "find_missing_so_what_items", lambda: self._targets())
        monkeypatch.setattr(repair, "_fetch_item", lambda item_id: {"id": item_id, "summary_he": "s"})
        monkeypatch.setattr(
            repair, "repair_so_what_text", lambda *a, **kw: "להערכתנו רפאל תרוויח נתח שוק נוסף."
        )
        written = {}
        monkeypatch.setattr(
            repair, "update_item_fields", lambda item_id, **fields: written.setdefault(item_id, fields)
        )
        budget = repair.LLMBudget(15)
        report = repair.repair_so_what(apply=True, budget=budget)
        assert len(report["repaired"]) == 2
        assert written[23]["so_what_he"] == "להערכתנו רפאל תרוויח נתח שוק נוסף."
        assert budget.used == 2

    def test_apply_rejects_llm_failure(self, monkeypatch) -> None:
        monkeypatch.setattr(repair, "find_missing_so_what_items", lambda: self._targets()[:1])
        monkeypatch.setattr(repair, "_fetch_item", lambda item_id: {"id": item_id, "summary_he": "s"})
        monkeypatch.setattr(repair, "repair_so_what_text", lambda *a, **kw: None)
        budget = repair.LLMBudget(15)
        report = repair.repair_so_what(apply=True, budget=budget)
        assert report["rejected"] == [{"id": 23, "reason": "llm_repair_failed_or_rejected"}]

    def test_apply_rejects_still_generic_result(self, monkeypatch) -> None:
        monkeypatch.setattr(repair, "find_missing_so_what_items", lambda: self._targets()[:1])
        monkeypatch.setattr(repair, "_fetch_item", lambda item_id: {"id": item_id, "summary_he": "s"})
        monkeypatch.setattr(
            repair, "repair_so_what_text", lambda *a, **kw: "להערכתנו זה מהווה צעד משמעותי לחברה."
        )
        budget = repair.LLMBudget(15)
        report = repair.repair_so_what(apply=True, budget=budget)
        assert report["rejected"][0]["reason"] == "still_generic"

    def test_apply_rejects_wrong_sentence_count(self, monkeypatch) -> None:
        monkeypatch.setattr(repair, "find_missing_so_what_items", lambda: self._targets()[:1])
        monkeypatch.setattr(repair, "_fetch_item", lambda item_id: {"id": item_id, "summary_he": "s"})
        four_sentences = "להערכתנו א. ב. ג. ד."
        monkeypatch.setattr(repair, "repair_so_what_text", lambda *a, **kw: four_sentences)
        budget = repair.LLMBudget(15)
        report = repair.repair_so_what(apply=True, budget=budget)
        assert report["rejected"][0]["reason"].startswith("sentence_count=")

    def test_budget_exhaustion_skips_remaining_targets(self, monkeypatch) -> None:
        monkeypatch.setattr(repair, "find_missing_so_what_items", lambda: self._targets())
        monkeypatch.setattr(repair, "_fetch_item", lambda item_id: {"id": item_id, "summary_he": "s"})
        monkeypatch.setattr(repair, "repair_so_what_text", lambda *a, **kw: "להערכתנו טקסט תקין כאן.")
        monkeypatch.setattr(repair, "update_item_fields", lambda item_id, **fields: None)
        budget = repair.LLMBudget(1)
        report = repair.repair_so_what(apply=True, budget=budget)
        assert len(report["repaired"]) == 1
        assert report["skipped_budget"] == [6872]

    def test_item_vanished_between_query_and_fetch(self, monkeypatch) -> None:
        monkeypatch.setattr(repair, "find_missing_so_what_items", lambda: self._targets()[:1])
        monkeypatch.setattr(repair, "_fetch_item", lambda item_id: None)
        budget = repair.LLMBudget(15)
        report = repair.repair_so_what(apply=True, budget=budget)
        assert report["rejected"] == [{"id": 23, "reason": "item_vanished"}]
