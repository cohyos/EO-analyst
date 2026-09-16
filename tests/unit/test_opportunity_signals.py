"""Unit tests for CR-platform-opportunity (2026-09-16,
docs/qa/content_review/CR-platform-opportunity.md): ``eoa.pipeline.opportunity_signals`` -- the
deterministic (no LLM) "does this platform story state/imply an open external EO/IR/sensor/pod
slot" pre-check.

Fixture text below is trimmed from the REAL ``items.clean_text`` of the two live-miss items
(fetched from the DB, DATABASE_URL loaded silently from ``runtime/eoa.env``, never printed) that
motivated this feature:

  - item 22760, TWZ, "YFQ-44A Fury Has Been Fit Checked With Air-To-Ground Munitions" (2026-09-15)
  - item 23252, Breaking Defense, "Anduril officials say company can't start on CCA production
    contract without FY27 budget" (2026-09-15)
  - item 20162, Defense News, "Vengeance and Fury: US Air Force names new CCAs" (2026-09-14) --
    the control case: real "Fury"/"CCA" platform text with NO pod/sensor-specific content beyond
    the bare CCA-class signal (see the calibration note in the module docstring).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_opportunity_signals.py -q``
"""

from __future__ import annotations

from eoa.pipeline.opportunity_signals import TAG, PlatformOpportunityHint, detect_platform_opportunity

# --------------------------------------------------------------------------
# Real fixture text (trimmed to the relevant paragraphs), verbatim from items.clean_text.
# --------------------------------------------------------------------------

TITLE_22760 = "YFQ-44A Fury Has Been Fit Checked With Air-To-Ground Munitions"
TEXT_22760 = (
    "New imagery shared with TWZ by Anduril shows its YFQ-44A Fury Collaborative Combat Aircraft "
    "(CCA) configured with air-to-ground weapons, underscoring the fact that the CCA could take on "
    "an air-to-ground role in the future in addition to its current air-to-air focus. Yesterday, "
    "Anduril executives revealed that the company had begun integration and fit checks for a range "
    "of air-to-ground weapons, although they confirmed to us that those weapons had not yet been "
    "flight-tested aboard Fury.\n"
    "Furthermore, Anduril's plans extend to testing the Fury with other air-to-air weapons and "
    "targeting pods.\n"
    "While the Air Force's initial CCA requirement is heavily focused on air dominance, the same "
    "aircraft could potentially be configured for different missions by changing its external "
    "stores and sensors."
)

TITLE_23252 = "Anduril officials say company can't start on CCA production contract without FY27 budget"
TEXT_23252 = (
    "With no sight of a fiscal 2027 defense budget until December at earliest, Anduril officials "
    "are warning that production work on FQ-44 Fury drones won't be able to commence until funding "
    "is approved by Congress. In June, the Air Force awarded production contracts to Anduril and "
    "General Atomics to build the service's first round of Collaborative Combat Aircraft.\n"
    "\"We've also looked at other air-to-air weapons, as well as begun integration on rocket pods "
    "and targeting pods, so we can perform other missions, whether it's counter-UAS, counter-"
    "cruise missile, or you know air-to-ground or air-to-surface strike,\" he said."
)

TITLE_20162 = "Vengeance and Fury: US Air Force names new CCAs amid ambitious production goal"
TEXT_20162 = (
    "The FQ-42A and FQ-44A, the U.S. Air Force's newest Collaborative Combat Aircraft, have been "
    "dubbed \"Vengeance\" and \"Fury,\" respectively. Secretary of the Air Force Troy Meink used "
    "the names for the first time during a Monday keynote address... the service's fiscal 2027 "
    "budget request included $996.5 million in procurement funding, as well as $150 million in "
    "advanced procurement for the following fiscal year and $1.37 billion in continued research "
    "and development, for a total program request of $2.37 billion."
)


class TestDetectPlatformOpportunity:
    def test_item_22760_matches_targeting_pods_via_platform_and_pod_signal(self) -> None:
        hint = detect_platform_opportunity(TITLE_22760, TEXT_22760)
        assert hint is not None
        assert "targeting_pods" in hint.line_ids
        assert "Fury" in hint.platforms or "YFQ-44A" in hint.platforms
        assert "targeting pod" in hint.signals

    def test_item_23252_matches_targeting_pods_via_platform_and_pod_signal(self) -> None:
        hint = detect_platform_opportunity(TITLE_23252, TEXT_23252)
        assert hint is not None
        assert "targeting_pods" in hint.line_ids
        assert "Fury" in hint.platforms
        assert "targeting pod" in hint.signals

    def test_item_20162_matches_only_via_bare_cca_class_signal(self) -> None:
        """Calibration case: a pure naming/budget story with no pod/sensor content of its own
        still matches, on the platform name + CCA-class signal alone -- accepted by design (see
        module docstring's calibration note); the level floor this feeds
        (eoa.pipeline.triage._apply_platform_opportunity_floor) only guarantees `yellow`, it does
        not inflate the item's importance."""
        hint = detect_platform_opportunity(TITLE_20162, TEXT_20162)
        assert hint is not None
        assert "Fury" in hint.platforms and "Vengeance" in hint.platforms
        assert "targeting pod" not in hint.signals
        assert "CCA" in hint.signals or "collaborative combat aircraft" in hint.signals

    def test_plural_pod_term_still_matches_singular_configured_term(self) -> None:
        """The deterministic tagger's own stricter word-boundary matcher would miss "targeting
        pods" (plural) against the configured singular "targeting pod" -- this module deliberately
        uses plain substring matching instead (see module docstring) so the plural still counts."""
        hint = detect_platform_opportunity("Fury update", "The Fury now carries targeting pods.")
        assert hint is not None
        assert "targeting pod" in hint.signals

    def test_platform_alone_without_signal_does_not_match(self) -> None:
        hint = detect_platform_opportunity(
            "Fury enters service", "The Fury aircraft was delivered to the Air Force today."
        )
        assert hint is None

    def test_signal_alone_without_platform_does_not_match(self) -> None:
        hint = detect_platform_opportunity(
            "Targeting pod market grows", "The global targeting pod market is expected to grow."
        )
        assert hint is None

    def test_unrelated_text_does_not_match(self) -> None:
        assert detect_platform_opportunity("Quarterly earnings", "The company reported earnings.") is None

    def test_empty_text_does_not_match(self) -> None:
        assert detect_platform_opportunity(None, None) is None
        assert detect_platform_opportunity("", "") is None

    def test_case_insensitive_match(self) -> None:
        hint = detect_platform_opportunity("fury news", "the FURY now has a TARGETING POD slot.")
        assert hint is not None

    def test_hint_is_frozen_dataclass_with_deduped_sorted_fields(self) -> None:
        hint = detect_platform_opportunity(TITLE_22760, TEXT_22760)
        assert isinstance(hint, PlatformOpportunityHint)
        assert list(hint.platforms) == sorted(set(hint.platforms))
        assert list(hint.signals) == sorted(set(hint.signals))

    def test_prompt_text_he_is_non_empty_hebrew_string(self) -> None:
        hint = detect_platform_opportunity(TITLE_22760, TEXT_22760)
        assert hint is not None
        text = hint.prompt_text_he()
        assert isinstance(text, str) and text.strip()

    def test_tag_constant_matches_expected_value(self) -> None:
        assert TAG == "platform_integration_opportunity"
