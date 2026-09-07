"""Unit tests for the R8-tagging round (product-line tagging coverage, 2026-09-07):
``ConditionalKeyword``/``llm_tagging_enabled`` in ``eoa.product_lines.registry``, the new rule-6
conditional-match branch in ``eoa.product_lines.tagging``, ``eoa.product_lines.llm_tagging``
(LLM-assisted fallback), and ``eoa.llm.schemas.product_line.ProductLineTagResult``.

Every DB/LLM-touching function is monkeypatched (no Postgres, no Ollama/cloud LLM, no network) --
mirrors the existing convention in ``tests/unit/test_product_lines.py``. This file is additive: it
does not modify or duplicate that file's own coverage of the original registry/tagging/stats
surface, only the new R8-tagging surface layered on top of it.

Run with:
    PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_product_lines_round8.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from eoa.llm.schemas.product_line import ProductLineTagResult
from eoa.product_lines import llm_tagging as pl_llm_tagging
from eoa.product_lines import registry as pl_registry
from eoa.product_lines import tagging as pl_tagging

# ---------------------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------------------

_SAMPLE_PRODUCT_LINES_YAML = {
    "llm_tagging": True,
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
            "our_products": [],
            "conditional_keywords_en": [
                {
                    "term": "DIRCM",
                    "context": ["self-protection", "self protection", "להגנה עצמית"],
                }
            ],
        },
    ],
}


@pytest.fixture(autouse=True)
def _fake_product_lines_settings(monkeypatch):
    """Every test in this file sees the same small, deterministic two-line fixture catalog
    (including one line's ``conditional_keywords_en``) rather than the real
    ``config/product_lines.yaml`` -- keeps assertions independent of future edits to the real
    config, same convention as ``tests/unit/test_product_lines.py``'s own fixture."""
    fake_settings = SimpleNamespace(product_lines=_SAMPLE_PRODUCT_LINES_YAML)
    monkeypatch.setattr(pl_registry, "settings", lambda: fake_settings)
    yield


# ---------------------------------------------------------------------------------------------
# registry -- ConditionalKeyword parsing / llm_tagging_enabled
# ---------------------------------------------------------------------------------------------


class TestConditionalKeywordParsing:
    def test_conditional_keywords_parsed_from_yaml(self):
        pl = pl_registry.get_product_line("mws_eo")
        assert len(pl.conditional_keywords_en) == 1
        ck = pl.conditional_keywords_en[0]
        assert ck.term == "DIRCM"
        assert "self-protection" in ck.context

    def test_conditional_keywords_default_empty_when_absent(self):
        pl = pl_registry.get_product_line("targeting_pods")
        assert pl.conditional_keywords_en == ()

    def test_conditional_keywords_row_without_term_skipped(self, monkeypatch):
        monkeypatch.setattr(
            pl_registry,
            "settings",
            lambda: SimpleNamespace(
                product_lines={
                    "product_lines": [
                        {
                            "id": "x",
                            "conditional_keywords_en": [{"context": ["a"]}, {"term": "OK", "context": ["b"]}],
                        }
                    ]
                }
            ),
        )
        pl = pl_registry.get_product_line("x")
        assert [c.term for c in pl.conditional_keywords_en] == ["OK"]

    def test_conditional_keywords_non_list_value_yields_empty(self, monkeypatch):
        monkeypatch.setattr(
            pl_registry,
            "settings",
            lambda: SimpleNamespace(
                product_lines={"product_lines": [{"id": "x", "conditional_keywords_en": None}]}
            ),
        )
        pl = pl_registry.get_product_line("x")
        assert pl.conditional_keywords_en == ()


class TestLlmTaggingEnabled:
    def test_llm_tagging_enabled_true(self):
        assert pl_registry.llm_tagging_enabled() is True

    def test_llm_tagging_enabled_false_when_key_false(self, monkeypatch):
        monkeypatch.setattr(
            pl_registry, "settings", lambda: SimpleNamespace(product_lines={"llm_tagging": False})
        )
        assert pl_registry.llm_tagging_enabled() is False

    def test_llm_tagging_enabled_defaults_false_when_key_absent(self, monkeypatch):
        monkeypatch.setattr(pl_registry, "settings", lambda: SimpleNamespace(product_lines={}))
        assert pl_registry.llm_tagging_enabled() is False


# ---------------------------------------------------------------------------------------------
# tagging -- rule 6 (conditional keyword match)
# ---------------------------------------------------------------------------------------------


class TestConditionalMatch:
    def test_term_alone_is_not_enough(self):
        assert pl_tagging.tag_product_lines(text_en="a DIRCM pod was installed") == []

    def test_context_alone_is_not_enough(self):
        assert pl_tagging.tag_product_lines(text_en="a self-protection upgrade was announced") == []

    def test_term_plus_context_tags_the_line(self):
        result = pl_tagging.tag_product_lines(text_en="the new DIRCM self-protection system entered service")
        assert result == ["mws_eo"]

    def test_context_may_appear_in_hebrew_text(self):
        result = pl_tagging.tag_product_lines(
            text_he="המערכת סופקה להגנה עצמית", text_en="a DIRCM turret was fitted"
        )
        assert result == ["mws_eo"]

    def test_term_word_boundary_still_applies(self):
        # "DIRCM" must not match inside a longer token even with context present.
        assert pl_tagging.tag_product_lines(text_en="a DIRCMONSTER self-protection device was tested") == []

    def test_line_without_conditional_keywords_unaffected(self):
        assert pl_tagging.tag_product_lines(text_en="a self-protection briefing was held") == []

    def test_conditional_match_combines_with_other_rules(self):
        # keyword hit (rule 1/2) plus an unrelated conditional term both present -- still exactly
        # one tag for mws_eo (no double-counting/error).
        result = pl_tagging.tag_product_lines(text_en="missile warning and DIRCM self-protection suite")
        assert result == ["mws_eo"]


# ---------------------------------------------------------------------------------------------
# eoa.llm.schemas.product_line.ProductLineTagResult
# ---------------------------------------------------------------------------------------------


class TestProductLineTagResultSchema:
    def test_defaults_are_empty_and_zero(self):
        result = ProductLineTagResult()
        assert result.line_ids == []
        assert result.confidence == 0.0

    def test_dedupes_line_ids_preserving_order(self):
        result = ProductLineTagResult(line_ids=["mws_eo", "targeting_pods", "mws_eo"], confidence=0.8)
        assert result.line_ids == ["mws_eo", "targeting_pods"]

    def test_strips_blank_entries(self):
        result = ProductLineTagResult(line_ids=["mws_eo", "  ", ""], confidence=0.9)
        assert result.line_ids == ["mws_eo"]

    def test_confidence_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            ProductLineTagResult(confidence=-0.1)

    def test_confidence_above_one_rejected(self):
        with pytest.raises(ValidationError):
            ProductLineTagResult(confidence=1.1)


# ---------------------------------------------------------------------------------------------
# eoa.product_lines.llm_tagging
# ---------------------------------------------------------------------------------------------


class TestLlmTagBatch:
    def test_empty_items_returns_empty_without_calling_llm(self, monkeypatch):
        called = {"n": 0}

        def boom(*a, **k):
            called["n"] += 1
            raise AssertionError("should not be called")

        monkeypatch.setattr(pl_llm_tagging, "chat_structured_batch", boom)
        assert pl_llm_tagging.llm_tag_batch([]) == {}
        assert called["n"] == 0

    def test_accepts_high_confidence_result(self, monkeypatch):
        def fake_batch(role, schema, prompts, *, system, task):
            return {prompts[0][0]: ProductLineTagResult(line_ids=["mws_eo"], confidence=0.9)}

        monkeypatch.setattr(pl_llm_tagging, "chat_structured_batch", fake_batch)
        result = pl_llm_tagging.llm_tag_batch([{"id": 1, "title": "t", "summary_he": "s"}])
        assert result == {1: ["mws_eo"]}

    def test_rejects_result_below_min_confidence(self, monkeypatch):
        def fake_batch(role, schema, prompts, *, system, task):
            return {prompts[0][0]: ProductLineTagResult(line_ids=["mws_eo"], confidence=0.59)}

        monkeypatch.setattr(pl_llm_tagging, "chat_structured_batch", fake_batch)
        result = pl_llm_tagging.llm_tag_batch([{"id": 1, "title": "t"}])
        assert result == {}

    def test_accepts_result_exactly_at_min_confidence(self, monkeypatch):
        def fake_batch(role, schema, prompts, *, system, task):
            return {
                prompts[0][0]: ProductLineTagResult(
                    line_ids=["mws_eo"], confidence=pl_llm_tagging.LLM_TAG_MIN_CONFIDENCE
                )
            }

        monkeypatch.setattr(pl_llm_tagging, "chat_structured_batch", fake_batch)
        result = pl_llm_tagging.llm_tag_batch([{"id": 1, "title": "t"}])
        assert result == {1: ["mws_eo"]}

    def test_drops_line_ids_outside_the_closed_catalog(self, monkeypatch):
        def fake_batch(role, schema, prompts, *, system, task):
            return {
                prompts[0][0]: ProductLineTagResult(line_ids=["mws_eo", "not_a_real_line"], confidence=0.9)
            }

        monkeypatch.setattr(pl_llm_tagging, "chat_structured_batch", fake_batch)
        result = pl_llm_tagging.llm_tag_batch([{"id": 1, "title": "t"}])
        assert result == {1: ["mws_eo"]}

    def test_item_omitted_when_only_invalid_line_ids(self, monkeypatch):
        def fake_batch(role, schema, prompts, *, system, task):
            return {prompts[0][0]: ProductLineTagResult(line_ids=["not_real"], confidence=0.9)}

        monkeypatch.setattr(pl_llm_tagging, "chat_structured_batch", fake_batch)
        result = pl_llm_tagging.llm_tag_batch([{"id": 1, "title": "t"}])
        assert result == {}

    def test_item_missing_from_llm_response_is_simply_absent(self, monkeypatch):
        def fake_batch(role, schema, prompts, *, system, task):
            return {}  # model omitted this item entirely

        monkeypatch.setattr(pl_llm_tagging, "chat_structured_batch", fake_batch)
        result = pl_llm_tagging.llm_tag_batch([{"id": 1, "title": "t"}])
        assert result == {}

    def test_exception_from_chat_structured_batch_degrades_to_empty(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("llm chain exhausted")

        monkeypatch.setattr(pl_llm_tagging, "chat_structured_batch", boom)
        result = pl_llm_tagging.llm_tag_batch([{"id": 1, "title": "t"}])
        assert result == {}

    def test_items_without_id_are_excluded_from_the_batch(self, monkeypatch):
        seen_prompts = {}

        def fake_batch(role, schema, prompts, *, system, task):
            seen_prompts["prompts"] = prompts
            return {}

        monkeypatch.setattr(pl_llm_tagging, "chat_structured_batch", fake_batch)
        pl_llm_tagging.llm_tag_batch([{"title": "no id here"}, {"id": 2, "title": "has id"}])
        assert [pid for pid, _ in seen_prompts["prompts"]] == [2]

    def test_no_configured_product_lines_returns_empty_without_calling_llm(self, monkeypatch):
        monkeypatch.setattr(pl_registry, "settings", lambda: SimpleNamespace(product_lines={}))
        called = {"n": 0}

        def boom(*a, **k):
            called["n"] += 1
            return {}

        monkeypatch.setattr(pl_llm_tagging, "chat_structured_batch", boom)
        result = pl_llm_tagging.llm_tag_batch([{"id": 1, "title": "t"}])
        assert result == {}
        assert called["n"] == 0

    def test_multiple_items_in_one_batch_call(self, monkeypatch):
        def fake_batch(role, schema, prompts, *, system, task):
            return {
                1: ProductLineTagResult(line_ids=["mws_eo"], confidence=0.9),
                2: ProductLineTagResult(line_ids=["targeting_pods"], confidence=0.7),
            }

        monkeypatch.setattr(pl_llm_tagging, "chat_structured_batch", fake_batch)
        result = pl_llm_tagging.llm_tag_batch([{"id": 1, "title": "a"}, {"id": 2, "title": "b"}])
        assert result == {1: ["mws_eo"], 2: ["targeting_pods"]}


class TestCatalogAndPromptHelpers:
    def test_catalog_text_lists_every_configured_line(self):
        text = pl_llm_tagging._catalog_text()
        assert 'id="targeting_pods"' in text
        assert 'id="mws_eo"' in text

    def test_valid_ids_matches_registry(self):
        assert pl_llm_tagging._valid_ids() == {"targeting_pods", "mws_eo"}

    def test_item_prompt_includes_title_and_summary(self):
        prompt = pl_llm_tagging._item_prompt(
            {"id": 5, "title": "New DIRCM pod unveiled", "summary_he": "תקציר בעברית"}
        )
        assert "New DIRCM pod unveiled" in prompt
        assert "תקציר בעברית" in prompt

    def test_item_prompt_falls_back_to_summary_field(self):
        prompt = pl_llm_tagging._item_prompt({"id": 5, "title": "t", "summary": "english summary"})
        assert "english summary" in prompt
