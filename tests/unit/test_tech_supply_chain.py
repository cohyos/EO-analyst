"""Unit tests for the ``tech_daily`` report's EO/IR supply-chain layer taxonomy
(``eoa.report.tech_supply_chain``) and its LLM classification schema
(``eoa.llm.schemas.tech_daily.TechLayerAssignment``).

Every DB-touching function is out of scope here (pure config/logic only) -- no Postgres, no
Ollama, no network. Mirrors the existing convention in ``tests/unit/test_product_lines.py``.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_tech_supply_chain.py -q``
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from eoa.llm.schemas.tech_daily import LayerRelevance, TechLayerAssignment
from eoa.report import tech_supply_chain as tsc

_SAMPLE_LAYERS_YAML = {
    "layers": [
        {
            "key": "detectors_fpa",
            "label_he": "גלאים ומישורי מוקד (FPA)",
            "description_he": "FPA, InSb, MCT, SWIR",
            "keywords_he": ["מישור מוקד"],
            "keywords_en": ["FPA", "InSb"],
            "domains_hint": ["tech_dev"],
            "patent_cpc_hint": ["H01L27/146"],
        },
        {
            "key": "operational_concepts",
            "label_he": "תפיסות מבצעיות (CONOPS)",
            "description_he": "MUM-T, CCA, kill chains",
            "keywords_he": ["שרשרת הרג"],
            "keywords_en": ["MUM-T", "CCA"],
            "domains_hint": ["c_uas"],
            "patent_cpc_hint": [],
        },
    ]
}


@pytest.fixture(autouse=True)
def _fake_tech_supply_chain_settings(monkeypatch):
    """Every test in this file sees the same small, deterministic two-layer fixture catalog
    rather than the real ``config/tech_supply_chain.yaml`` -- keeps assertions independent of
    future edits to the real config."""
    fake_settings = SimpleNamespace(tech_supply_chain=_SAMPLE_LAYERS_YAML)
    monkeypatch.setattr(tsc, "settings", lambda: fake_settings)
    yield


# ---------------------------------------------------------------------------------------------
# registry / taxonomy loader
# ---------------------------------------------------------------------------------------------


class TestRegistry:
    def test_layer_defs_returns_all_configured_layers_in_order(self):
        defs = tsc.layer_defs()
        assert [d.key for d in defs] == ["detectors_fpa", "operational_concepts"]

    def test_layer_keys(self):
        assert tsc.layer_keys() == ["detectors_fpa", "operational_concepts"]

    def test_get_layer_found(self):
        layer = tsc.get_layer("detectors_fpa")
        assert layer is not None
        assert layer.label_he == "גלאים ומישורי מוקד (FPA)"
        assert layer.keywords_en == ("FPA", "InSb")
        assert layer.patent_cpc_hint == ("H01L27/146",)

    def test_get_layer_not_found_returns_none(self):
        assert tsc.get_layer("does_not_exist") is None

    def test_empty_config_yields_empty_defs(self, monkeypatch):
        monkeypatch.setattr(tsc, "settings", lambda: SimpleNamespace(tech_supply_chain={}))
        assert tsc.layer_defs() == ()

    def test_row_without_key_is_skipped(self, monkeypatch):
        monkeypatch.setattr(
            tsc,
            "settings",
            lambda: SimpleNamespace(tech_supply_chain={"layers": [{"label_he": "no key here"}]}),
        )
        assert tsc.layer_defs() == ()


# ---------------------------------------------------------------------------------------------
# deterministic keyword-based layer assignment
# ---------------------------------------------------------------------------------------------


class TestAssignLayersKeyword:
    def test_hebrew_substring_match(self):
        assert tsc.assign_layers_keyword(text_he="חברה הכריזה על מישור מוקד חדש") == ["detectors_fpa"]

    def test_english_word_boundary_match(self):
        assert tsc.assign_layers_keyword(text_en="a new InSb detector was unveiled") == ["detectors_fpa"]

    def test_english_word_boundary_does_not_match_substring(self):
        # "InSb" must not match inside an unrelated longer token.
        assert tsc.assign_layers_keyword(text_en="the company is InSbX Systems") == []

    def test_conops_layer_matches_on_mumt(self):
        assert tsc.assign_layers_keyword(text_en="a new MUM-T doctrine was published") == [
            "operational_concepts"
        ]

    def test_no_match_returns_empty_list(self):
        assert tsc.assign_layers_keyword(text_he="ידיעה שאינה קשורה", text_en="unrelated news") == []

    def test_item_can_match_multiple_layers(self):
        text_he = "מישור מוקד חדש שילווה שרשרת הרג חדשה"
        assert set(tsc.assign_layers_keyword(text_he=text_he)) == {"detectors_fpa", "operational_concepts"}

    def test_never_raises_on_empty_config(self, monkeypatch):
        monkeypatch.setattr(tsc, "settings", lambda: SimpleNamespace(tech_supply_chain={}))
        assert tsc.assign_layers_keyword(text_he="anything", text_en="anything") == []

    def test_none_inputs_do_not_raise(self):
        assert tsc.assign_layers_keyword(text_he=None, text_en=None) == []


# ---------------------------------------------------------------------------------------------
# TechLayerAssignment schema validation
# ---------------------------------------------------------------------------------------------


class TestTechLayerAssignmentSchema:
    def test_valid_layers_pass_through(self):
        out = TechLayerAssignment(
            layers=[
                {"layer": "detectors_fpa", "relevance": "core"},
                {"layer": "operational_concepts", "relevance": "tangential"},
            ]
        )
        assert [(lr.layer, lr.relevance) for lr in out.layers] == [
            ("detectors_fpa", "core"),
            ("operational_concepts", "tangential"),
        ]

    def test_empty_layers_is_valid(self):
        assert TechLayerAssignment(layers=[]).layers == []

    def test_default_layers_is_empty_list(self):
        assert TechLayerAssignment().layers == []

    def test_unknown_layer_key_is_silently_dropped_not_rejected(self):
        # Fail-safe (never raises on a hallucinated layer key) -- see the schema's own docstring.
        out = TechLayerAssignment(
            layers=[
                {"layer": "detectors_fpa", "relevance": "core"},
                {"layer": "not_a_real_layer", "relevance": "core"},
            ]
        )
        assert [lr.layer for lr in out.layers] == ["detectors_fpa"]

    def test_all_unknown_layers_yields_empty_list(self):
        out = TechLayerAssignment(
            layers=[{"layer": "bogus_one", "relevance": "core"}, {"layer": "bogus_two", "relevance": "core"}]
        )
        assert out.layers == []

    def test_invalid_relevance_value_raises(self):
        with pytest.raises(Exception):
            TechLayerAssignment(layers=[{"layer": "detectors_fpa", "relevance": "maybe"}])

    def test_layer_relevance_model_directly(self):
        lr = LayerRelevance(layer="detectors_fpa", relevance="tangential")
        assert lr.layer == "detectors_fpa"
        assert lr.relevance == "tangential"
