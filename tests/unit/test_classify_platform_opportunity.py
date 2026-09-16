"""Unit tests for CR-platform-opportunity (2026-09-16): ``eoa.pipeline.classify.
apply_platform_opportunity_gate`` -- the deterministic override that pulls a platform-integration
business-opportunity story back in scope even when the model (or the two conservative gates that
run before it in ``run_classify``) classified it ``out_of_scope``.

Regression fixtures: the real (trimmed) text of items 22760/23252 -- see
``tests/unit/test_opportunity_signals.py``'s module docstring for provenance.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_classify_platform_opportunity.py -q``
"""

from __future__ import annotations

from tests.unit.test_opportunity_signals import (
    TEXT_20162,
    TEXT_22760,
    TEXT_23252,
    TITLE_20162,
    TITLE_22760,
    TITLE_23252,
)

from eoa.llm.schemas.analysis import ClassifyOut
from eoa.pipeline.classify import apply_platform_opportunity_gate
from eoa.pipeline.opportunity_signals import TAG


def _item(item_id: int, title: str, clean_text: str) -> dict:
    return {"id": item_id, "title": title, "clean_text": clean_text}


class TestApplyPlatformOpportunityGate:
    def test_22760_style_out_of_scope_is_pulled_in_scope(self) -> None:
        """Regression for the live miss: the model archived this as a pure platform-weapons story
        ("no substantive EO/IR/CV payload detail") -- the gate must force it in scope as a business
        opportunity, tagged, with `business` in dimensions."""
        out = ClassifyOut(
            domain="out_of_scope",
            subdomain="",
            report_kind="verified_report",
            relevance_note="Platform weapons integration, no substantive EO/IR/CV payload detail",
            one_line_he="Anduril fit-checked the Fury CCA with air-to-ground munitions.",
        )
        gated = apply_platform_opportunity_gate(_item(22760, TITLE_22760, TEXT_22760), out)
        assert gated.domain != "out_of_scope"
        assert gated.domain == "airborne_pods"
        assert gated.subdomain == "targeting_pods"
        assert "business" in gated.dimensions
        assert TAG in gated.tags

    def test_23252_style_out_of_scope_is_pulled_in_scope(self) -> None:
        out = ClassifyOut(
            domain="out_of_scope",
            subdomain="",
            report_kind="verified_report",
            relevance_note="Platform/budget/production story; only passing mention of targeting pods",
            one_line_he="Anduril warns FY27 budget delay will block Fury CCA production.",
        )
        gated = apply_platform_opportunity_gate(_item(23252, TITLE_23252, TEXT_23252), out)
        assert gated.domain == "airborne_pods"
        assert gated.subdomain == "targeting_pods"
        assert "business" in gated.dimensions
        assert TAG in gated.tags

    def test_20162_style_naming_story_also_tagged_but_still_reversible_by_design(self) -> None:
        """Calibration case (see eoa.pipeline.opportunity_signals module docstring): a pure naming/
        budget story with no pod/sensor content still matches the deterministic pre-check via the
        bare CCA-class signal, so it is also pulled in scope and tagged -- the triage-stage floor
        (not this gate) is what keeps its importance appropriately low, not exclusion here."""
        out = ClassifyOut(
            domain="out_of_scope",
            subdomain="",
            report_kind="verified_report",
            relevance_note="Autonomous aircraft platform naming/production, no EO/IR/CV content",
            one_line_he="USAF named its new CCAs Vengeance and Fury.",
        )
        gated = apply_platform_opportunity_gate(_item(20162, TITLE_20162, TEXT_20162), out)
        assert gated.domain == "airborne_pods"
        assert TAG in gated.tags

    def test_no_hint_is_a_noop(self) -> None:
        out = ClassifyOut(
            domain="out_of_scope",
            subdomain="",
            report_kind="rumor_speculation",
            relevance_note="unrelated",
            one_line_he="x",
        )
        item = _item(1, "Quarterly earnings report", "The company reported quarterly earnings.")
        gated = apply_platform_opportunity_gate(item, out)
        assert gated.domain == "out_of_scope"
        assert TAG not in gated.tags
        assert gated.relevance_note == "unrelated"

    def test_already_in_scope_item_keeps_its_own_domain_but_still_gets_tag(self) -> None:
        """When the model already classified the item in scope on its own merits, the gate must not
        override its domain/subdomain choice -- only add the tag/dimension."""
        out = ClassifyOut(
            domain="airborne_pods",
            subdomain="isr_pods",
            report_kind="verified_report",
            one_line_he="x",
        )
        gated = apply_platform_opportunity_gate(_item(22760, TITLE_22760, TEXT_22760), out)
        assert gated.domain == "airborne_pods"
        assert gated.subdomain == "isr_pods"  # untouched -- the model's own choice, not overridden
        assert TAG in gated.tags
        assert "business" in gated.dimensions

    def test_tag_not_duplicated_when_already_present(self) -> None:
        out = ClassifyOut(
            domain="out_of_scope",
            subdomain="",
            report_kind="verified_report",
            tags=[TAG],
            one_line_he="x",
        )
        gated = apply_platform_opportunity_gate(_item(22760, TITLE_22760, TEXT_22760), out)
        assert gated.tags.count(TAG) == 1

    def test_tags_list_never_exceeds_eight_entries(self) -> None:
        out = ClassifyOut(
            domain="out_of_scope",
            subdomain="",
            report_kind="verified_report",
            tags=["t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8"],
            one_line_he="x",
        )
        gated = apply_platform_opportunity_gate(_item(22760, TITLE_22760, TEXT_22760), out)
        assert len(gated.tags) <= 8
        assert TAG in gated.tags
