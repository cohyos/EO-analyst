"""Unit tests for the product-line status & business-development feature (PL-backend, user request
2026-09-07): ``eoa.product_lines.registry``/``tagging``/``stats``, ``eoa.llm.schemas.product_line``,
``eoa.report.product_line`` and the ``eoa.qa.d7_bd_report`` extension.

Every DB-touching function is monkeypatched (module-level ``_fetchall``/``_fetchone``, or the
module's own ``settings`` reference) -- no Postgres, no Ollama, no network. Mirrors the existing
convention in ``tests/unit/test_bd_round4b.py``/``test_report_bd_territory.py``.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_product_lines.py -q``
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from eoa.llm.schemas.analysis import Sentence
from eoa.llm.schemas.product_line import (
    ProductLineAssumption,
    ProductLineRecommendedAction,
    ProductLineReportDraft,
)
from eoa.product_lines import registry as pl_registry
from eoa.product_lines import stats as pl_stats
from eoa.product_lines import tagging as pl_tagging
from eoa.qa import d7_bd_report
from eoa.report import product_line as pl_report

# ---------------------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------------------

_SAMPLE_PRODUCT_LINES_YAML = {
    "product_lines": [
        {
            "id": "targeting_pods",
            "name_he": "פודי ציון מטרות",
            "name_en": "Targeting Pods",
            "keywords_he": ["פוד ציון מטרות"],
            "keywords_en": ["targeting pod"],
            "aliases": ["ATP"],
            "subdomains": ["airborne_pods.targeting_pods"],
            "exemplar_systems": ["Litening", "Sniper ATP"],
            "competitors": ["Rafael", "Lockheed Martin"],
            "our_products": [],
        },
        {
            "id": "mws_eo",
            "name_he": "מערכות התראת טילים",
            "name_en": "MWS",
            "keywords_he": ["מערכת התראת טילים"],
            "keywords_en": ["missile warning"],
            "aliases": ["MWS"],
            "subdomains": ["airborne_pods.eo_warfare", "airborne_pods.mws"],
            "exemplar_systems": ["PAWS"],
            "competitors": ["Elbit"],
            "our_products": ["Our MWS Product"],
        },
    ]
}


@pytest.fixture(autouse=True)
def _fake_product_lines_settings(monkeypatch):
    """Every test in this file sees the same small, deterministic two-line fixture catalog rather
    than the real ``config/product_lines.yaml`` -- keeps assertions independent of future edits to
    the real config."""
    fake_settings = SimpleNamespace(product_lines=_SAMPLE_PRODUCT_LINES_YAML)
    monkeypatch.setattr(pl_registry, "settings", lambda: fake_settings)
    yield


# ---------------------------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------------------------


class TestRegistry:
    def test_product_line_defs_returns_all_configured_lines(self):
        defs = pl_registry.product_line_defs()
        assert len(defs) == 2

    def test_product_line_ids(self):
        assert pl_registry.product_line_ids() == ["targeting_pods", "mws_eo"]

    def test_get_product_line_found(self):
        pl = pl_registry.get_product_line("targeting_pods")
        assert pl is not None
        assert pl.name_he == "פודי ציון מטרות"
        assert pl.exemplar_systems == ("Litening", "Sniper ATP")

    def test_get_product_line_not_found_returns_none(self):
        assert pl_registry.get_product_line("does_not_exist") is None

    def test_our_products_parsed(self):
        pl = pl_registry.get_product_line("mws_eo")
        assert pl.our_products == ("Our MWS Product",)

    def test_empty_config_yields_empty_defs(self, monkeypatch):
        monkeypatch.setattr(pl_registry, "settings", lambda: SimpleNamespace(product_lines={}))
        assert pl_registry.product_line_defs() == ()

    def test_row_without_id_is_skipped(self, monkeypatch):
        monkeypatch.setattr(
            pl_registry,
            "settings",
            lambda: SimpleNamespace(product_lines={"product_lines": [{"name_he": "no id here"}]}),
        )
        assert pl_registry.product_line_defs() == ()

    def test_non_dict_row_is_skipped(self, monkeypatch):
        monkeypatch.setattr(
            pl_registry, "settings", lambda: SimpleNamespace(product_lines={"product_lines": ["not-a-dict"]})
        )
        assert pl_registry.product_line_defs() == ()


# ---------------------------------------------------------------------------------------------
# tagging
# ---------------------------------------------------------------------------------------------


class TestTagging:
    def test_hebrew_keyword_match_tags_the_line(self):
        assert pl_tagging.tag_product_lines(text_he="נחשף פוד ציון מטרות חדש") == ["targeting_pods"]

    def test_english_keyword_word_boundary_match(self):
        assert pl_tagging.tag_product_lines(text_en="new targeting pod unveiled") == ["targeting_pods"]

    def test_alias_word_boundary_match(self):
        assert pl_tagging.tag_product_lines(text_en="the new ATP variant") == ["targeting_pods"]

    def test_alias_does_not_match_inside_longer_word(self):
        # "ATP" must not match inside "CATPILLAR"-shaped tokens -- word-boundary regex.
        assert pl_tagging.tag_product_lines(text_en="a CATPULT was tested") == []

    def test_exemplar_system_text_match(self):
        assert pl_tagging.tag_product_lines(text_en="Litening pod delivered to customer") == [
            "targeting_pods"
        ]

    def test_exemplar_system_entity_match(self):
        assert pl_tagging.tag_product_lines(text_en="", entities=["Sniper ATP"]) == ["targeting_pods"]

    def test_subdomain_alone_tags_the_line(self):
        assert pl_tagging.tag_product_lines(subdomain="airborne_pods.targeting_pods") == ["targeting_pods"]

    def test_competitor_alone_is_not_enough(self):
        assert pl_tagging.tag_product_lines(text_en="Rafael announced a naval radar upgrade") == []

    def test_competitor_entity_alone_is_not_enough(self):
        assert pl_tagging.tag_product_lines(entities=["Rafael"]) == []

    def test_competitor_plus_subdomain_tags_the_line(self):
        result = pl_tagging.tag_product_lines(
            text_en="Rafael won a contract", subdomain="airborne_pods.targeting_pods"
        )
        assert result == ["targeting_pods"]

    def test_no_signal_returns_empty(self):
        assert pl_tagging.tag_product_lines(text_he="כתבה שאינה קשורה לשום קו מוצר") == []

    def test_multiple_lines_can_match_at_once(self):
        text_en = "targeting pod and missile warning system unveiled at show"
        result = pl_tagging.tag_product_lines(text_en=text_en)
        assert set(result) == {"targeting_pods", "mws_eo"}

    def test_none_inputs_do_not_raise(self):
        assert pl_tagging.tag_product_lines(text_he=None, text_en=None, entities=None, subdomain=None) == []

    def test_mws_alias_short_acronym_word_boundary(self):
        assert pl_tagging.tag_product_lines(text_en="the MWS was installed") == ["mws_eo"]

    def test_mws_alias_does_not_match_inside_word(self):
        assert pl_tagging.tag_product_lines(text_en="a MWSOMETHING device") == []


# ---------------------------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------------------------


class TestStats:
    def test_product_line_stats_happy_path(self, monkeypatch):
        counters = iter([7, 30, 4, 2, 5, 3])

        def fake_fetchone(query, params=None):
            return {"n": next(counters)}

        monkeypatch.setattr(pl_stats, "_fetchone", fake_fetchone)
        monkeypatch.setattr(pl_stats, "_active_competitors_count", lambda line_id, since: 9)

        result = pl_stats.product_line_stats("targeting_pods")
        assert result == {
            "items_7d": 7,
            "items_30d": 30,
            "events_30d": 4,
            "open_tenders": 2,
            "forecasts": 5,
            "patents_90d": 3,
            "active_competitors": 9,
        }

    def test_product_line_stats_degrades_to_zero_on_db_failure(self, monkeypatch):
        def boom(query, params=None):
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(pl_stats, "_fetchone", boom)
        result = pl_stats.product_line_stats("targeting_pods")
        assert result == {
            "items_7d": 0,
            "items_30d": 0,
            "events_30d": 0,
            "open_tenders": 0,
            "forecasts": 0,
            "patents_90d": 0,
            "active_competitors": 0,
        }

    def test_active_competitors_count_zero_when_no_competitors_configured(self):
        assert pl_stats._active_competitors_count("does_not_exist", dt.date.today()) == 0

    def test_active_competitors_count_queries_when_competitors_configured(self, monkeypatch):
        seen = {}

        def fake_fetchone(query, params=None):
            seen["names"] = params["names"]
            return {"n": 2}

        monkeypatch.setattr(pl_stats, "_fetchone", fake_fetchone)
        result = pl_stats._active_competitors_count("targeting_pods", dt.date.today())
        assert result == 2
        assert set(seen["names"]) == {"Rafael", "Lockheed Martin"}


# ---------------------------------------------------------------------------------------------
# schema validation
# ---------------------------------------------------------------------------------------------


class TestSchema:
    def test_sentence_requires_non_empty_cites(self):
        with pytest.raises(ValidationError):
            Sentence(text_he="עובדה כלשהי", cites=[])

    def test_sentence_rejects_inline_citation_marker(self):
        with pytest.raises(ValidationError):
            Sentence(text_he="עובדה עם [1] מוטבע", cites=[1])

    def test_recommended_action_requires_rationale(self):
        with pytest.raises(ValidationError):
            ProductLineRecommendedAction(
                action_he="פעולה",
                priority="H",
                rationale=[],
                owner_role_he="מכירות",
                timing_he="מיידי",
            )

    def test_recommended_action_valid(self):
        action = ProductLineRecommendedAction(
            action_he="לפנות ללקוח",
            priority="H",
            rationale=[Sentence(text_he="עובדה תומכת", cites=[1])],
            owner_role_he="מכירות",
            timing_he="מיידי",
        )
        assert action.priority == "H"

    def test_assumption_allows_empty_cites(self):
        assumption = ProductLineAssumption(assumption_he="הנחה", falsifier_he="הפרכה", cites=[])
        assert assumption.cites == []

    def test_bluf_over_two_sentences_rejected(self):
        with pytest.raises(ValidationError):
            ProductLineReportDraft(
                bluf=[
                    Sentence(text_he="משפט אחד", cites=[1]),
                    Sentence(text_he="משפט שני", cites=[1]),
                    Sentence(text_he="משפט שלישי", cites=[1]),
                ]
            )

    def test_draft_defaults_are_empty(self):
        draft = ProductLineReportDraft()
        assert draft.exec_summary == []
        assert draft.recommended_actions == []
        assert draft.system_note_he == ""


# ---------------------------------------------------------------------------------------------
# eoa.report.product_line -- tier scoring / pipeline / tables / QA (deterministic helpers only)
# ---------------------------------------------------------------------------------------------


class TestTierScoring:
    def test_magnitude_score_by_amount(self):
        assert pl_report._magnitude_score(amount_usd=100_000_000) == 3
        assert pl_report._magnitude_score(amount_usd=10_000_000) == 2
        assert pl_report._magnitude_score(amount_usd=100) == 1
        assert pl_report._magnitude_score(amount_usd=0) == 0

    def test_magnitude_score_by_likelihood(self):
        assert pl_report._magnitude_score(likelihood=0.9) == 3
        assert pl_report._magnitude_score(likelihood=0.4) == 2
        assert pl_report._magnitude_score(likelihood=0.01) == 1

    def test_magnitude_score_by_level_fallback(self):
        assert pl_report._magnitude_score(level="red") == 3
        assert pl_report._magnitude_score() == 1

    def test_recency_score_buckets(self):
        today = dt.date(2026, 9, 7)
        assert pl_report._recency_score(today, today=today) == 2
        assert pl_report._recency_score(today - dt.timedelta(days=60), today=today) == 1
        assert pl_report._recency_score(today - dt.timedelta(days=200), today=today) == 0
        assert pl_report._recency_score(None, today=today) == 0

    def test_tier_label_thresholds(self):
        assert pl_report.tier_label(4) == "A"
        assert pl_report.tier_label(2) == "B"
        assert pl_report.tier_label(0) == "C"

    def test_pipeline_table_none_when_no_rows(self):
        assert pl_report.pipeline_table([]) is None

    def test_pipeline_table_sorted_by_stage_then_tier(self):
        today = dt.date(2026, 9, 7)
        rows = [
            pl_report.PipelineRow(
                opportunity_he="B",
                stage="לאחר-זכייה",
                buyer_he="—",
                target_date_he="—",
                n=1,
                amount_usd=100_000_000,
                reference_date=today,
            ),
            pl_report.PipelineRow(
                opportunity_he="A",
                stage="RFI",
                buyer_he="—",
                target_date_he="—",
                n=2,
            ),
        ]
        table = pl_report.pipeline_table(rows, today=today)
        assert table is not None
        assert table["rows"][0][0] == "A"  # RFI sorts before לאחר-זכייה


class TestDeterministicTables:
    def test_market_items_table_none_when_empty(self):
        assert pl_report.market_items_table([]) is None

    def test_market_items_table_renders_rows(self):
        items = [
            {
                "n": 1,
                "title": "כותרת",
                "domain": "airborne_pods",
                "source_name": "Janes",
                "published_at": dt.date(2026, 9, 1),
                "level": "red",
            }
        ]
        table = pl_report.market_items_table(items)
        assert table["rows"] == [[1, "כותרת", "airborne_pods", "Janes", "2026-09-01", "red"]]

    def test_events_table_none_when_empty(self):
        assert pl_report.events_table([]) is None

    def test_competitors_table_none_when_empty(self):
        assert pl_report.competitors_table([]) is None

    def test_competitors_table_renders_israeli_flag(self):
        competitors = [{"name": "Elbit", "mentions": 3, "is_israeli_industry": True, "recent_wins": []}]
        table = pl_report.competitors_table(competitors)
        assert table["rows"][0][2] == "כן"

    def test_tenders_table_none_when_empty(self):
        assert pl_report.tenders_table({"tenders": []}) is None

    def test_forecasts_table_none_when_empty(self):
        assert pl_report.forecasts_table({"forecasts": []}) is None

    def test_patents_table_none_when_empty(self):
        assert pl_report.patents_table([]) is None

    def test_israel_positioning_table_none_when_nothing_to_show(self):
        assert pl_report.israel_positioning_table("targeting_pods", []) is None

    def test_israel_positioning_table_lists_our_products(self, monkeypatch):
        table = pl_report.israel_positioning_table("mws_eo", [])
        assert table is not None
        assert table["rows"][0][0] == "Our MWS Product"

    def test_recommended_actions_table_none_when_empty(self):
        draft = ProductLineReportDraft()
        assert pl_report.recommended_actions_table(draft) is None

    def test_recommended_actions_table_sorted_by_priority(self):
        draft = ProductLineReportDraft(
            recommended_actions=[
                ProductLineRecommendedAction(
                    action_he="נמוכה",
                    priority="L",
                    rationale=[Sentence(text_he="x", cites=[1])],
                    owner_role_he="מכירות",
                    timing_he="מיידי",
                ),
                ProductLineRecommendedAction(
                    action_he="גבוהה",
                    priority="H",
                    rationale=[Sentence(text_he="y", cites=[1])],
                    owner_role_he="מכירות",
                    timing_he="מיידי",
                ),
            ]
        )
        table = pl_report.recommended_actions_table(draft)
        assert table["rows"][0][1] == "גבוהה"


class TestNoItemsAndTablesOnlyDrafts:
    def test_no_items_draft_has_empty_line_marker(self):
        draft = pl_report._no_items_draft()
        assert pl_report.PL_EMPTY_LINE_MARKER_HE in draft.system_note_he
        assert draft.exec_summary == []

    def test_tables_only_draft_mentions_table_counts(self):
        counts = pl_report.TableCounts(events=2, tenders=1)
        draft = pl_report._tables_only_draft("targeting_pods", counts)
        assert "אירועים עסקיים" in draft.system_note_he

    def test_table_counts_context_he_empty(self):
        assert pl_report.TableCounts().context_he() == "אין (כל הטבלאות ריקות בתקופה זו)."

    def test_table_counts_context_he_with_content(self):
        counts = pl_report.TableCounts(events=1, tenders=2, patents=1)
        text = counts.context_he()
        assert "1 אירועים עסקיים" in text
        assert "2 מכרזים" in text


class TestRunQA:
    def test_qa_passes_with_valid_citations(self):
        citation_items = [{"id": 1, "n": 1}]
        draft = ProductLineReportDraft(
            exec_summary=[Sentence(text_he="עובדה", cites=[1])],
            market_bullets=[Sentence(text_he="בולט", cites=[1])],
        )
        qa = pl_report._run_qa(draft, citation_items, has_items=True)
        assert qa.passed

    def test_qa_fails_on_bad_reference(self):
        citation_items = [{"id": 1, "n": 1}]
        draft = ProductLineReportDraft(
            exec_summary=[Sentence(text_he="עובדה", cites=[1])],
            market_bullets=[Sentence(text_he="בולט עם רפרנס לא קיים", cites=[99])],
        )
        qa = pl_report._run_qa(draft, citation_items, has_items=True)
        assert not qa.passed
        assert 99 in qa.bad_refs

    def test_qa_fails_when_exec_summary_empty_but_has_items(self):
        qa = pl_report._run_qa(ProductLineReportDraft(), [{"id": 1, "n": 1}], has_items=True)
        assert not qa.passed

    def test_qa_passes_empty_draft_when_no_items(self):
        qa = pl_report._run_qa(ProductLineReportDraft(), [], has_items=False)
        assert qa.passed


class TestLineLabel:
    def test_line_label_known_line(self):
        assert pl_report.line_label("targeting_pods") == "פודי ציון מטרות"

    def test_line_label_unknown_line_falls_back_to_id(self):
        assert pl_report.line_label("nope") == "nope"


class TestLookbackRange:
    def test_lookback_range_defaults(self):
        end = dt.date(2026, 9, 7)
        start, computed_end = pl_report.lookback_range(90, end)
        assert computed_end == end
        assert (end - start).days == 90


# ---------------------------------------------------------------------------------------------
# eoa.qa.d7_bd_report extension (PL_NO_ACTIVITY_MARKER_HE / PL_EMPTY_LINE_MARKER_HE recognition)
# ---------------------------------------------------------------------------------------------


class TestD7Extension:
    def test_is_no_activity_recognizes_pl_marker(self):
        text = f"{pl_report.PL_NO_ACTIVITY_MARKER_HE}. מתחרים שנבדקו: Rafael."
        assert d7_bd_report._is_no_activity_actions_text(text)

    def test_is_no_activity_still_recognizes_bd_marker(self):
        from eoa.report.bd_territory import NO_ACTIVITY_MARKER_HE

        assert d7_bd_report._is_no_activity_actions_text(NO_ACTIVITY_MARKER_HE)

    def test_is_no_activity_false_for_unrelated_text(self):
        assert not d7_bd_report._is_no_activity_actions_text("טקסט רגיל ללא סימון")

    def test_score_d7_scores_pl_report_file(self, tmp_path):
        md = tmp_path / "pl_targeting_pods_2026-09-07.md"
        md.write_text(
            "# שורה תחתונה\n\nעובדה חשובה. [1]\n\n"
            "# תקציר מנהלים\n\nתקציר. [1]\n\n"
            "# פעולות מומלצות\n\n"
            "| עדיפות | פעולה | נימוק | אחראי | תזמון |\n|---|---|---|---|---|\n"
            "| גבוהה | פעולה | נימוק [1] | מכירות | מיידי |\n\n"
            "# מפת קונים / צינור הזדמנויות\n\n"
            "| הזדמנות | שלב | גורם רוכש | תאריך יעד | דרג | מקור |\n|---|---|---|---|---|---|\n"
            "| הזדמנות | RFI | לקוח | 01.01.2027 | A | [1] |\n\n"
            "# הנחות והפרכות\n\n- הנחה -- יופרך אם: תרחיש נגדי\n",
            encoding="utf-8",
        )
        score = d7_bd_report.score_D7([md], conn=None)
        assert score.n == 1
        assert score.score_0_100 is not None
