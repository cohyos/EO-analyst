"""Unit tests for Q3-8/Q3-9 (docs/qa/findings_Q3_r1.md): analyze-stage post-processing --
deduplicating `key_facts` and deterministically backfilling an empty `entities_mentioned` from a
watchlist alias match found in the item's own text.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_analyze_key_facts_entities.py -q``
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

from eoa.llm.schemas.analysis import AnalyzeOut
from eoa.pipeline.analyze import _backfill_entities_from_watchlist, _dedupe_key_facts, persist_analysis


class TestDedupeKeyFacts:
    def test_exact_duplicates_removed(self) -> None:
        facts = ["עובדה אחת.", "עובדה אחת.", "עובדה שתיים."]
        assert _dedupe_key_facts(facts) == ["עובדה אחת.", "עובדה שתיים."]

    def test_whitespace_and_case_insensitive_dedup(self) -> None:
        facts = ["Fact One.", "fact   one.", "  FACT ONE.  "]
        assert _dedupe_key_facts(facts) == ["Fact One."]

    def test_empty_and_blank_entries_dropped(self) -> None:
        assert _dedupe_key_facts(["", "  ", "עובדה."]) == ["עובדה."]

    def test_distinct_facts_all_kept(self) -> None:
        facts = ["עובדה א.", "עובדה ב.", "עובדה ג."]
        assert _dedupe_key_facts(facts) == facts

    def test_empty_list(self) -> None:
        assert _dedupe_key_facts([]) == []

    # -- D1 round-1 fix (docs/qa/loop/round_1_fixes.md, key_facts_no_duplicates) -----------------

    def test_punctuation_insensitive_dedup(self) -> None:
        """Two entries differing only in trailing punctuation are the same fact."""
        facts = [
            "המחקר עשוי לכלול ספינות מיפן, קוריאה וטורקיה.",
            "המחקר עשוי לכלול ספינות מיפן קוריאה וטורקיה",
        ]
        assert _dedupe_key_facts(facts) == ["המחקר עשוי לכלול ספינות מיפן, קוריאה וטורקיה."]

    def test_near_duplicate_regression_item5(self) -> None:
        """Regression for item 5: two entries restate the same fact with a couple of words
        swapped ("שתיים מהספינות" -> "ספינות", "בארה\"ב" -> "בארה\"ס") -- not an exact match even
        after punctuation/case normalisation, but a near-duplicate (ratio >= 0.9)."""
        facts = [
            'המחקר עשוי לכלול בניית שתיים מהספינות בחו"ל לפני מעבר לייצור בארה"ב.',
            'המחקר עשוי לכלול בניית ספינות בחו"ל לפני מעבר לייצור בארה"ס.',
        ]
        result = _dedupe_key_facts(facts)
        assert len(result) == 1
        assert result[0] == facts[0]

    def test_item10_style_exact_triplicate_collapsed_but_short_variant_kept(self) -> None:
        """Regression for item 10's actual data: entries 2-4 are an exact triplicate (collapsed to
        one by the plain exact-match path), while entry 1 -- a *shorter* restatement missing the
        appended clause -- scores just under the 0.9 near-duplicate threshold (~0.884) against it,
        so it is kept as a distinct fact rather than force-merged."""
        short = 'החוזה נועד לאפשר לכוחות הצבא להפיל כלי טיס בלתי מאוישים (כטב"מים) באמצעות לייזר'
        long_ = 'החוזה נועד לאפשר לכוחות הצבא להפיל כלי טיס בלתי מאוישים (כטב"מים) באמצעות לייזר, במקום להשתמש בטילים'
        facts = [short, long_, long_, long_]
        result = _dedupe_key_facts(facts)
        assert result == [short, long_]

    def test_distinct_facts_below_threshold_both_kept(self) -> None:
        """Two facts that share vocabulary but describe different things must not be merged."""
        facts = ["המטוס החדש יפעל לצד המטוסים הקיימים.", "הצי האמריקאי מבקש לפתח את המטוס במהירות."]
        assert _dedupe_key_facts(facts) == facts


class TestBackfillEntitiesFromWatchlist:
    def test_no_backfill_when_already_populated(self) -> None:
        item = {
            "id": 1,
            "title": "x",
            "clean_text": "Elbit Systems won a contract.",
            "entities_mentioned": ["Something"],
        }
        assert _backfill_entities_from_watchlist(item) is None

    def test_backfill_from_title_and_text(self) -> None:
        item = {
            "id": 2,
            "title": "Elbit Systems wins new pod contract",
            "clean_text": "The company announced a new electro-optical pod deal.",
            "entities_mentioned": [],
        }
        result = _backfill_entities_from_watchlist(item)
        assert result == ["Elbit"]

    def test_backfill_via_alias(self) -> None:
        item = {
            "id": 3,
            "title": "עדכון",
            "clean_text": "אלביט מערכות זכתה בחוזה חדש.",
            "entities_mentioned": None,
        }
        result = _backfill_entities_from_watchlist(item)
        assert result == ["Elbit"]

    def test_no_backfill_when_no_alias_found(self) -> None:
        item = {
            "id": 4,
            "title": "Unrelated news",
            "clean_text": "Nothing to see here.",
            "entities_mentioned": [],
        }
        assert _backfill_entities_from_watchlist(item) is None


class TestPersistAnalysisIntegration:
    def _stub_graph(self) -> None:
        fake_graph = types.ModuleType("eoa.memory.graph")
        fake_graph.merge_entity = lambda *a, **k: None  # type: ignore[attr-defined]
        fake_graph.add_edge = lambda *a, **k: None  # type: ignore[attr-defined]
        sys.modules["eoa.memory.graph"] = fake_graph

    def test_persist_analysis_dedupes_key_facts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = []
        monkeypatch.setattr("eoa.pipeline.analyze.update_item_fields", lambda item_id, **kw: calls.append(kw))
        monkeypatch.setattr("eoa.pipeline.analyze.insert_event", lambda **kw: None)
        monkeypatch.setattr("eoa.pipeline.analyze.upsert_entity", lambda **kw: 1)

        out = AnalyzeOut(
            summary_he="תקציר",
            so_what_he="להערכתנו, השלכה",
            key_facts=["עובדה חוזרת.", "עובדה חוזרת.", "עובדה ייחודית."],
            events=[],
            edges=[],
        )
        item = {"id": 700, "title": "Test", "clean_text": "", "entities_mentioned": ["Elbit"]}
        persist_analysis(item, out)

        assert calls[0]["key_facts"] == ["עובדה חוזרת.", "עובדה ייחודית."]

    def test_persist_analysis_backfills_entities_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = []
        monkeypatch.setattr("eoa.pipeline.analyze.update_item_fields", lambda item_id, **kw: calls.append(kw))
        monkeypatch.setattr("eoa.pipeline.analyze.insert_event", lambda **kw: None)
        monkeypatch.setattr("eoa.pipeline.analyze.upsert_entity", lambda **kw: 1)

        out = AnalyzeOut(summary_he="תקציר", so_what_he="להערכתנו, השלכה", key_facts=[], events=[], edges=[])
        item = {
            "id": 701,
            "title": "Elbit Systems announces new sensor",
            "clean_text": "",
            "entities_mentioned": [],
        }
        persist_analysis(item, out)

        assert calls[0]["entities_mentioned"] == ["Elbit"]

    def test_persist_analysis_does_not_touch_entities_when_already_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []
        monkeypatch.setattr("eoa.pipeline.analyze.update_item_fields", lambda item_id, **kw: calls.append(kw))
        monkeypatch.setattr("eoa.pipeline.analyze.insert_event", lambda **kw: None)
        monkeypatch.setattr("eoa.pipeline.analyze.upsert_entity", lambda **kw: 1)

        out = AnalyzeOut(summary_he="תקציר", so_what_he="להערכתנו, השלכה", key_facts=[], events=[], edges=[])
        item = {
            "id": 702,
            "title": "Elbit Systems announces new sensor",
            "clean_text": "",
            "entities_mentioned": ["Rafael"],
        }
        persist_analysis(item, out)

        assert "entities_mentioned" not in calls[0]

    def test_persist_analysis_skips_edge_when_entity_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Q3-13 integration: an edge endpoint upsert_entity rejects (returns None, e.g. a
        technique-like name) must not be written as an edge, and must not crash."""
        from eoa.llm.schemas.analysis import EdgeOut

        self._stub_graph()
        monkeypatch.setattr("eoa.pipeline.analyze.update_item_fields", lambda item_id, **kw: None)
        monkeypatch.setattr("eoa.pipeline.analyze.insert_event", lambda **kw: None)

        def fake_upsert(*, name, kind, first_seen_item=None):
            return None if name == "image captioning" else 1

        monkeypatch.setattr("eoa.pipeline.analyze.upsert_entity", fake_upsert)

        out = AnalyzeOut(
            summary_he="תקציר",
            so_what_he="להערכתנו, השלכה",
            key_facts=[],
            events=[],
            edges=[EdgeOut(src="image captioning", dst="Elbit", label="DERIVED_FROM", evidence_he="x")],
        )
        item = {"id": 703, "title": "x", "clean_text": "", "entities_mentioned": ["Elbit"]}
        _n_events, n_edges = persist_analysis(item, out)
        assert n_edges == 0
