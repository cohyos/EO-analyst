"""Vocabulary-loader tests for the product dossier's fixed specification vocabulary (PD-vocab-
extract, docs/PLAN_SPEC_VOCABULARY.md lane a/e): ``eoa.dossier.vocabulary`` -- typed access to
``config/spec_vocabulary.yaml``, the closed, stable-``key`` parameter list the extraction/diff
stages now write to instead of free-named ``SpecRow.parameter_he``/``PerformanceRow.metric_he``.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_dossier_vocabulary.py -q``
"""

from __future__ import annotations

from eoa.dossier import vocabulary

# --------------------------------------------------------------------------
# section 7 acceptance check 1: 121 keys, 0 duplicates, every group_he in the fixed 8-value set,
# every enum entry carries enum_values -- a standing regression test, not just a one-off check.
# --------------------------------------------------------------------------

_EXPECTED_BLOCK_COUNTS = {
    "common": 24,
    "targeting_pods": 19,
    "mws_eo": 17,
    "lorop_pods": 16,
    "eo_air_defense_warning": 15,
    "ball_gimbals_16in": 15,
    "border_long_range_eo": 15,
}


def test_all_blocks_load_with_expected_counts() -> None:
    vocab = vocabulary.load_vocabulary()
    assert {k: len(v) for k, v in vocab.items()} == _EXPECTED_BLOCK_COUNTS


def test_total_key_count_is_121_and_all_unique() -> None:
    vocab = vocabulary.load_vocabulary()
    all_keys = [p.key for params in vocab.values() for p in params]
    assert len(all_keys) == 121
    assert len(set(all_keys)) == 121


def test_every_group_he_is_one_of_the_fixed_eight() -> None:
    vocab = vocabulary.load_vocabulary()
    for params in vocab.values():
        for p in params:
            assert p.group_he in vocabulary.GROUP_ORDER_HE, (p.key, p.group_he)


def test_every_enum_param_carries_enum_values() -> None:
    vocab = vocabulary.load_vocabulary()
    for params in vocab.values():
        for p in params:
            if p.value_type == "enum":
                assert p.enum_values, p.key


def test_every_param_has_a_table_of_specifications_or_performance() -> None:
    vocab = vocabulary.load_vocabulary()
    for params in vocab.values():
        for p in params:
            assert p.table in ("specifications", "performance"), (p.key, p.table)


# --------------------------------------------------------------------------
# effective_vocabulary / param_by_key
# --------------------------------------------------------------------------


def test_effective_vocabulary_with_no_product_line_is_common_only() -> None:
    params = vocabulary.effective_vocabulary(None)
    assert len(params) == 24
    assert {p.key for p in params} == {p.key for p in vocabulary.load_vocabulary()["common"]}


def test_effective_vocabulary_with_unknown_product_line_falls_back_to_common() -> None:
    params = vocabulary.effective_vocabulary("not_a_real_line")
    assert len(params) == 24


def test_effective_vocabulary_concatenates_common_then_line() -> None:
    params = vocabulary.effective_vocabulary("targeting_pods")
    assert len(params) == 24 + 19
    # common params come first, in common's own declaration order.
    assert params[0].key == "field_of_view"
    # then targeting_pods' own params, in its own declaration order.
    assert params[24].key == "laser_designation_accuracy"


def test_param_by_key_indexes_effective_vocabulary() -> None:
    by_key = vocabulary.param_by_key("targeting_pods")
    assert "weight" in by_key  # common
    assert "pod_class_diameter" in by_key  # targeting_pods
    assert "gsd" not in by_key  # lorop_pods-only, not in targeting_pods' effective vocabulary


def test_all_params_by_key_covers_every_block() -> None:
    by_key = vocabulary.all_params_by_key()
    assert len(by_key) == 121
    assert "gsd" in by_key
    assert "pod_class_diameter" in by_key


# --------------------------------------------------------------------------
# the exact id=1/2/3 drift this vocabulary exists to fix (docs/PLAN_SPEC_VOCABULARY.md's own
# opening section): the "20-inch-class performance in a 15-inch envelope" fact.
# --------------------------------------------------------------------------


def test_size_to_performance_ratio_routes_to_performance_table() -> None:
    by_key = vocabulary.all_params_by_key()
    assert by_key["size_to_performance_ratio"].table == "performance"


def test_pod_class_diameter_routes_to_specifications_table() -> None:
    by_key = vocabulary.all_params_by_key()
    assert by_key["pod_class_diameter"].table == "specifications"


def test_size_to_performance_ratio_synonyms_include_live_drift_strings() -> None:
    """The exact Hebrew strings the live SPECTRO XR dossiers (id=1/2/3) wrote for this one fact
    under four different names/placements -- see config/spec_vocabulary.yaml's own header comment."""
    param = vocabulary.all_params_by_key()["size_to_performance_ratio"]
    assert "עומס אופטי במארז קומפקטי" in param.synonyms
    assert "ביצועי אופטיקה" in param.synonyms


# --------------------------------------------------------------------------
# performance-routing coverage (docs/PLAN_SPEC_VOCABULARY.md section 3.4's explicit key list)
# --------------------------------------------------------------------------

_EXPECTED_PERFORMANCE_KEYS = {
    "detection_range_dri",
    "size_to_performance_ratio",
    "adverse_weather_performance",
    "false_alarm_rate",
    "declaration_time",
    "probability_of_detection",
    "revisit_rate",
    "detection_range_small_uas",
    "false_alarm_rate_ad",
    "gsd",
    "standoff_range",
    "dri_at_long_range",
    "slew_rate",
    "stabilization_class_microrad",
}


def test_exactly_the_planned_keys_route_to_performance() -> None:
    by_key = vocabulary.all_params_by_key()
    performance_keys = {k for k, p in by_key.items() if p.table == "performance"}
    assert performance_keys == _EXPECTED_PERFORMANCE_KEYS


# --------------------------------------------------------------------------
# synonym matcher (section 3.2) -- word-boundary-safe reuse of eoa.pipeline.text_match
# --------------------------------------------------------------------------


def test_match_key_by_synonym_finds_hebrew_synonym() -> None:
    assert vocabulary.match_key_by_synonym("המוצר סובל מ-עומס אופטי במארז קומפקטי", None) == (
        "size_to_performance_ratio"
    )


def test_match_key_by_synonym_no_match_returns_none() -> None:
    assert vocabulary.match_key_by_synonym("טקסט שלא קשור לשום פרמטר במאגר", None) is None


def test_match_key_by_synonym_empty_text_returns_none() -> None:
    assert vocabulary.match_key_by_synonym("", None) is None


def test_vocabulary_prompt_block_he_includes_key_label_and_unit() -> None:
    params = [p for p in vocabulary.effective_vocabulary(None) if p.key == "weight"]
    block = vocabulary.vocabulary_prompt_block_he(params)
    assert "weight" in block
    assert "משקל" in block
    assert "ק״ג" in block


# --------------------------------------------------------------------------
# PD-fix-4 (2026-09-09, item 1): additive synonyms added for real, previously-unmatched SPECTRO XR
# run-8 overflow facts (envelope diameter/height, laser designator/rangefinder English glosses,
# per-channel FOV counts).
# --------------------------------------------------------------------------


def test_envelope_dimensions_matches_diameter_and_height_hebrew_synonyms() -> None:
    assert vocabulary.match_key_by_synonym("קוטר המערכת.", None) == "envelope_dimensions"
    assert vocabulary.match_key_by_synonym("גובה המערכת.", None) == "envelope_dimensions"


def test_laser_designator_illuminator_matches_designator_gloss() -> None:
    assert (
        vocabulary.match_key_by_synonym("סוג הלייזר של מציין הלייזר (Designator).", None)
        == "laser_designator_illuminator"
    )


def test_laser_rangefinder_matches_rangefinder_gloss() -> None:
    assert (
        vocabulary.match_key_by_synonym("סוג הלייזר של מד הטווח (Rangefinder).", None)
        == "laser_rangefinder"
    )


def test_field_of_view_matches_definite_article_fov_count_phrasing() -> None:
    assert (
        vocabulary.match_key_by_synonym("מספר שדות הראייה בערוץ SWIR.", None) == "field_of_view"
    )
