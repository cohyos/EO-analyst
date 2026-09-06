"""Round 5 (package P8) D5 chat grounding fixes (docs/qa/loop/round_3_judge.md's D5 section and
worst-list items 3 and 8, plus its own ranked-by-effort item 6).

Round 3's grounding guards (`eoa.api.ask_grounding`) closed the round-2 fabrication shapes (an
invented multi-word proper noun, a real entity cited to the wrong source) -- but the round-3 judge,
re-sampling the same 8 golden questions a *third* time, found three more hallucination shapes that
still slip past every existing guard because -- exactly like round 3's own findings -- the
individual pieces involved are each independently real:

- **Q4 (DROIC):** the answer fabricated plausible ML jargon ("MAEC", "RSPEOT") attributed to
  Leonardo DRS -- a *single-word* invented acronym/product token, never caught by
  `_PROPER_NOUN_RE` (which requires >= 2 capitalised segments). This is round 3's own
  ranked-by-effort item 6.
- **Q7 (EO/IR RFI):** the answer called a Finnish MoD RFI "published by the US government" -- both
  "Finland" and "the US" are real countries, so no existing guard catches a wrong *attribution*.
- **Q5 (Skyranger vs. Israeli C-UAS):** the answer equated Israel's David's Sling with Germany's
  unrelated Skynex -- both real, watchlist-adjacent systems, so no existing guard catches a false
  *equivalence* claim between two real things.

Covers the four additive functions in `eoa.api.ask_grounding` that close these:
1. the single-token ALL-CAPS/CamelCase extension to `_grounding_violation`
   (`ground_and_filter_answer` itself, same public entry point as round 3),
2. `filter_entity_equivalence`,
3. `filter_attribution_mismatches`,
4. `filter_self_contradictions`,
plus their end-to-end wiring in `eoa.api.routes.ask`.

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 .venv\\Scripts\\python -m pytest tests/unit/test_ask_round5.py -q``
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from eoa.api import ask_grounding, services


def _src(id: int, title: str, text: str, **kw: Any) -> dict[str, Any]:
    base = {
        "id": id,
        "title": title,
        "url": f"https://example.test/{id}",
        "clean_text": text,
        "summary_he": "",
        "level": "yellow",
        "source_name": "מקור",
        "report_kind": None,
        "_is_context": False,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------------------------
# 1. Single-token ALL-CAPS acronym / CamelCase product-jargon check
# ---------------------------------------------------------------------------------------------


class TestSingleTokenJargonCheck:
    def test_live_repro_invented_acronyms_attributed_to_leonardo_drs_are_removed(self) -> None:
        """Live-verified pattern (docs/qa/loop/round_3_judge.md worst-list item 3): the model
        invented plausible-sounding ML jargon ("MAEC", "RSPEOT") and attributed it to Leonardo
        DRS -- neither acronym appears in any retrieved source, the question, the taxonomy, or the
        common-acronym allowlist."""
        rows = [
            _src(
                1,
                "Leonardo DRS DROIC roadmap",
                "Leonardo DRS continues DROIC development for infrared readout electronics.",
            )
        ]
        text = (
            "### עובדות מרכזיות\n"
            "- המחקר בוצע על ידי Leonardo DRS תחת שיטת MAEC עם אלגוריתם RSPEOT [1].\n"
            "- Leonardo DRS ממשיכה לפתח את ה-DROIC לצורכי חיישני אינפרה-אדום [1]."
        )
        new_text, removed = ask_grounding.ground_and_filter_answer(
            text, "מהי המגמה הטכנולוגית האחרונה ב-DROIC?", rows
        )
        assert removed == 1
        assert "MAEC" not in new_text
        assert "RSPEOT" not in new_text
        # the second, genuinely grounded bullet must survive untouched
        assert "Leonardo DRS ממשיכה לפתח את ה-DROIC" in new_text

    def test_camelcase_invented_product_name_is_removed(self) -> None:
        rows = [_src(1, "C-UAS market overview", "סקירה כללית של שוק ה-C-UAS.")]
        text = "### עובדות מרכזיות\n- החברה השיקה מוצר בשם SkyDefenderX למערכות C-UAS [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה כלשהי", rows)
        assert removed == 1
        assert "SkyDefenderX" not in new_text

    def test_allowlisted_common_acronym_is_never_flagged_even_if_absent_from_this_retrieval(
        self,
    ) -> None:
        rows = [_src(1, "Generic defense item", "כתבה כללית שאינה מזכירה שום ראשי תיבות ספציפיים.")]
        text = "### עובדות מרכזיות\n- המערכת כוללת יכולות MWIR ו-SWIR מתקדמות [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_taxonomy_vocabulary_term_is_grounded_even_if_absent_from_this_retrieval(self) -> None:
        """ "DIRCM" is the domain taxonomy's own vocabulary (config/taxonomy.yaml's
        `eo_warfare` label: "לוחמה אלקטרו-אופטית (DIRCM, Laser Dazzlers)") -- a real term this
        specific retrieval's sources simply didn't happen to repeat, not an invented one."""
        rows = [_src(1, "Generic EO warfare item", "כתבה כללית על לוחמה אלקטרו-אופטית.")]
        text = "### עובדות מרכזיות\n- המערכת משלבת יכולת DIRCM נגד איומי טילים [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_real_camelcase_watchlist_entity_is_grounded(self) -> None:
        rows = [
            _src(
                1,
                "AeroVironment laser program",
                "AeroVironment announced a new laser interception program.",
            )
        ]
        text = "### עובדות מרכזיות\n- AeroVironment חתמה על חוזה חדש [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_short_acronym_compound_regression_still_holds(self) -> None:
        """Regression guard for the round-3 fix this item extends: a short domain acronym compound
        like "(C-UAS)" must still never be flagged just because this retrieval didn't repeat it."""
        rows = [_src(1, "XM30 program update", "Lynx XM30 prototype delivered to the US Army.")]
        text = "### פערים / מה לא ידוע\n- האם קיימת מערכת אינטגרלית ליירוט רחפנים (C-UAS) ברכב."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "מה קורה עם XM30?", rows)
        assert removed == 0
        assert new_text == text


# ---------------------------------------------------------------------------------------------
# 2. Entity-equivalence guard
# ---------------------------------------------------------------------------------------------


class TestEntityEquivalenceGuard:
    def test_live_repro_davids_sling_equated_with_skynex_is_dropped_with_a_gap_note(self) -> None:
        """Live-verified pattern (docs/qa/loop/round_3_judge.md worst-list item 8): Israel's
        David's Sling wrongly equated with Germany's Rheinmetall Skynex -- both real, but no source
        mentions them together."""
        rows = [
            _src(
                1,
                "Rheinmetall Skynex counter-drone system",
                "Rheinmetall's Skynex system is an air-defense solution developed for Germany.",
            )
        ]
        text = "### עובדות מרכזיות\n- David's Sling הוא Skynex, מערכת יירוט גרמנית דומה [1]."
        new_text, removed = ask_grounding.filter_entity_equivalence(text, rows)
        assert removed == 1
        assert "David's Sling הוא" not in new_text
        assert "לא ניתן לאשר זהות בין David's Sling ל-Skynex" in new_text

    def test_known_as_phrasing_is_also_caught(self) -> None:
        rows = [_src(1, "Skynex system", "Rheinmetall Skynex is a German air-defense system.")]
        text = "### עובדות מרכזיות\n- David's Sling, הידוע גם כ-Skynex, נפרס בכמה מדינות [1]."
        new_text, removed = ask_grounding.filter_entity_equivalence(text, rows)
        assert removed == 1
        assert "Skynex" not in new_text or "לא ניתן לאשר" in new_text

    def test_legitimate_alias_gloss_of_the_same_watchlist_company_is_kept(self) -> None:
        """ "Rafael" and "Rafael Advanced Defense Systems" resolve to the *same* canonical watchlist
        record -- a legitimate full-name gloss, not an equivalence claim between two things."""
        rows = [_src(1, "Rafael profile", "Rafael Advanced Defense Systems is an Israeli company.")]
        text = "### עובדות מרכזיות\n- Rafael (Rafael Advanced Defense Systems) פיתחה את המערכת [1]."
        new_text, removed = ask_grounding.filter_entity_equivalence(text, rows)
        assert removed == 0
        assert new_text == text

    def test_equivalence_grounded_by_a_source_mentioning_both_names_is_kept(self) -> None:
        """The same claim as the live-repro case above, but this time a retrieved source actually
        does mention both names together -- a genuinely sourced comparison, not a fabrication."""
        rows = [
            _src(
                1,
                "Comparison: David's Sling vs Skynex",
                "David's Sling and Rheinmetall's Skynex are both mid-range air-defense interceptors.",
            )
        ]
        text = "### עובדות מרכזיות\n- David's Sling דומה במיקומה התפקודי ל-Skynex [1]."
        new_text, removed = ask_grounding.filter_entity_equivalence(text, rows)
        assert removed == 0
        assert new_text == text

    def test_no_retrieved_sources_is_a_no_op(self) -> None:
        text = "David's Sling הוא Skynex."
        assert ask_grounding.filter_entity_equivalence(text, []) == (text, 0)


# ---------------------------------------------------------------------------------------------
# 3. Attribution-consistency guard
# ---------------------------------------------------------------------------------------------


class TestAttributionConsistencyGuard:
    def test_live_repro_finnish_rfi_called_published_by_the_us_government_drops_the_clause(
        self,
    ) -> None:
        """Live-verified pattern (docs/qa/loop/round_3_judge.md worst-list item 8): a Finnish MoD
        RFI wrongly called "published by the US government" -- the underlying fact (an RFI exists)
        must survive; only the wrong attribution clause is dropped."""
        rows = [
            _src(
                1,
                "Finland MoD RFI",
                "משרד ההגנה של פינלנד פרסם קול קורא (RFI) בשנת 2026 לחיישני EO/IR.",
            )
        ]
        text = '### עובדות מרכזיות\n- פורסם RFI חדש בתחום EO/IR, שפורסם על ידי ממשלת ארה"ב [1].'
        new_text, removed = ask_grounding.filter_attribution_mismatches(text, rows)
        assert removed == 1
        assert 'ארה"ב' not in new_text
        assert "RFI" in new_text  # the underlying fact is kept, not the whole sentence dropped

    def test_correctly_attributed_publisher_is_kept_unchanged(self) -> None:
        rows = [
            _src(
                1,
                "Finland MoD RFI",
                "משרד ההגנה של פינלנד פרסם קול קורא (RFI) בשנת 2026 לחיישני EO/IR.",
            )
        ]
        text = "### עובדות מרכזיות\n- פורסם RFI חדש בתחום EO/IR, שפורסם על ידי ממשלת פינלנד [1]."
        new_text, removed = ask_grounding.filter_attribution_mismatches(text, rows)
        assert removed == 0
        assert new_text == text

    def test_unverifiable_publisher_is_left_alone(self) -> None:
        """A publisher clause that resolves to neither a known country nor a watchlist/curated-org
        record is not guessed at -- this guard only ever acts on a claim it can actually check."""
        rows = [_src(1, "Some tender", "כתבה על מכרז כלשהו.")]
        text = "### עובדות מרכזיות\n- פורסם מכרז חדש, מטעם גורם לא ידוע [1]."
        new_text, removed = ask_grounding.filter_attribution_mismatches(text, rows)
        assert removed == 0
        assert new_text == text

    def test_no_retrieved_sources_is_a_no_op(self) -> None:
        text = 'RFI פורסם על ידי ממשלת ארה"ב.'
        assert ask_grounding.filter_attribution_mismatches(text, []) == (text, 0)


# ---------------------------------------------------------------------------------------------
# 4. Self-contradiction pass
# ---------------------------------------------------------------------------------------------


class TestSelfContradictionGuard:
    def test_same_year_attributed_to_two_conflicting_countries_keeps_the_grounded_one(self) -> None:
        rows = [
            _src(1, "Finland MoD RFI", "משרד ההגנה של פינלנד פרסם קול קורא בשנת 2026 לחיישני EO/IR."),
            _src(2, "Unrelated general defense news", "כתבה כללית על תעשיית הביטחון, ללא תאריך ספציפי."),
        ]
        text = (
            "### עובדות מרכזיות\n"
            "- ה-RFI פורסם על ידי פינלנד בשנת 2026 [1].\n"
            '- אותו ה-RFI פורסם על ידי ארה"ב בשנת 2026 [2].'
        )
        new_text, removed = ask_grounding.filter_self_contradictions(text, rows)
        assert removed == 1
        assert "פינלנד" in new_text
        assert 'ארה"ב' not in new_text

    def test_ambiguous_pair_where_neither_side_is_grounded_takes_no_action(self) -> None:
        """Neither unit's own cited source actually contains the shared year -- this guard only
        acts when it can tell which sentence is right, never to arbitrarily pick one."""
        rows = [
            _src(1, "Finland general news", "כתבה כללית על פינלנד ללא תאריך ספציפי."),
            _src(2, "US general news", 'כתבה כללית על ארה"ב ללא תאריך ספציפי.'),
        ]
        text = (
            "### עובדות מרכזיות\n"
            "- ה-RFI פורסם על ידי פינלנד בשנת 2026 [1].\n"
            '- אותו ה-RFI פורסם על ידי ארה"ב בשנת 2026 [2].'
        )
        new_text, removed = ask_grounding.filter_self_contradictions(text, rows)
        assert removed == 0
        assert new_text == text

    def test_corroborating_sentences_sharing_a_subject_are_left_alone(self) -> None:
        rows = [_src(1, "Finland MoD RFI", "משרד ההגנה של פינלנד פרסם קול קורא בשנת 2026.")]
        text = (
            "### עובדות מרכזיות\n"
            "- ה-RFI הפינלנדי פורסם בשנת 2026 [1].\n"
            "- פינלנד ציינה כי ה-RFI מתמקד בחיישני EO/IR [1]."
        )
        new_text, removed = ask_grounding.filter_self_contradictions(text, rows)
        assert removed == 0
        assert new_text == text

    def test_no_retrieved_sources_is_a_no_op(self) -> None:
        text = "טקסט כלשהו."
        assert ask_grounding.filter_self_contradictions(text, []) == (text, 0)


# ---------------------------------------------------------------------------------------------
# 5. End-to-end SSE wiring -- all four round-5 guards chained after the round-3 guard
# ---------------------------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from eoa import db

    monkeypatch.setattr(db, "get_pool", lambda: object())
    monkeypatch.setattr(db, "close_pool", lambda: None)

    from eoa.api.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _sse_events(body: str) -> list[dict]:
    events = []
    for chunk in body.split("\n\n"):
        line = next((ln for ln in chunk.split("\n") if ln.startswith("data:")), None)
        if not line:
            continue
        events.append(json.loads(line[len("data:") :].strip()))
    return events


def _row(id: int, **kw: Any) -> dict[str, Any]:
    base = {
        "id": id,
        "title": "כותרת",
        "url": "https://example.com",
        "clean_text": "טקסט",
        "summary_he": "תקציר",
        "key_facts": [],
        "level": "yellow",
        "source_name": "מקור",
        "report_kind": None,
        "entities_mentioned": [],
        "_is_context": False,
    }
    base.update(kw)
    return base


def _mock_ask_with_real_sources(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    chunks: list[str],
    *,
    question: str,
) -> str:
    from eoa.llm import ollama_client

    monkeypatch.setattr(services, "ask_retrieve", lambda *a, **k: rows)
    monkeypatch.setattr(ollama_client, "resolve_provider_info", lambda provider: ("ollama", "resident"))
    monkeypatch.setattr(ollama_client, "chat_stream", lambda *a, **k: iter(chunks))
    monkeypatch.setattr(ollama_client, "chat", lambda *a, **k: type("R", (), {"content": ""})())
    return question


class TestRound5GuardsEndToEnd:
    def test_entity_equivalence_is_stripped_and_reported_end_to_end(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [
            _row(
                1,
                title="Rheinmetall Skynex counter-drone system",
                clean_text="Rheinmetall's Skynex system is an air-defense solution for Germany.",
            )
        ]
        question = _mock_ask_with_real_sources(
            monkeypatch,
            rows,
            ["### עובדות מרכזיות\n- David's Sling הוא Skynex, מערכת יירוט גרמנית דומה [1]."],
            question="מה הקשר בין David's Sling ל-Skynex?",
        )
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        finals = [e for e in events if e["type"] == "answer_final" and "ungrounded_removed" in e]
        assert finals, "expected a grounding-guard answer_final event"
        assert finals[0]["ungrounded_removed"] >= 1
        assert finals[0].get("removed_by_guard", {}).get("entity_equivalence") == 1
        assert events[-1]["type"] == "done"

    def test_clean_grounded_answer_never_triggers_any_round5_guard(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [
            _row(
                1,
                title="XM30 program update",
                clean_text="Lynx XM30 is the GDLS-built Bradley replacement program.",
            )
        ]
        question = _mock_ask_with_real_sources(
            monkeypatch,
            rows,
            ["### עובדות מרכזיות\n- תוכנית ה-XM30 מבוססת על GDLS Lynx [1]."],
            question="מה קורה עם XM30 (Bradley הבא)?",
        )
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        assert not [e for e in events if e["type"] == "answer_final" and "ungrounded_removed" in e]
