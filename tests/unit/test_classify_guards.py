"""Unit tests for Q3-2/Q3-3 (docs/qa/findings_Q3_r1.md): the classify-stage deterministic guards.

- Q3-3: ``ClassifyOut``'s subdomain-vs-taxonomy model validator (schemas/analysis.py).
- Q3-2: ``eoa.pipeline.classify.apply_no_eoir_gate`` -- the "no EO/IR/CV vocabulary" gate that
  forces ``domain=out_of_scope`` when nothing ties the item to the domain.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_classify_guards.py -q``
"""

from __future__ import annotations

from eoa.llm.schemas.analysis import ClassifyOut, EntityMention
from eoa.pipeline.classify import _has_eoir_vocabulary, _watchlist_alias_hit, apply_no_eoir_gate


class TestSubdomainTaxonomyValidator:
    def test_valid_subdomain_kept(self) -> None:
        out = ClassifyOut(
            domain="airborne_pods", subdomain="targeting_pods", report_kind="company_pr", one_line_he="x"
        )
        assert out.subdomain == "targeting_pods"

    def test_invalid_subdomain_cleared(self) -> None:
        out = ClassifyOut(
            domain="airborne_pods", subdomain="not_a_real_subdomain", report_kind="company_pr", one_line_he="x"
        )
        assert out.subdomain == ""

    def test_subdomain_valid_for_wrong_domain_is_cleared(self) -> None:
        """A subdomain that exists, but under a *different* domain, is still invalid."""
        out = ClassifyOut(
            domain="c_uas", subdomain="targeting_pods", report_kind="company_pr", one_line_he="x"
        )
        assert out.subdomain == ""

    def test_empty_subdomain_untouched(self) -> None:
        out = ClassifyOut(domain="out_of_scope", subdomain="", report_kind="rumor_speculation", one_line_he="x")
        assert out.subdomain == ""

    def test_out_of_scope_domain_with_empty_subdomain(self) -> None:
        out = ClassifyOut(domain="out_of_scope", subdomain="", report_kind="rumor_speculation", one_line_he="x")
        assert out.domain == "out_of_scope"


class TestHasEoirVocabulary:
    def test_english_keyword_hit(self) -> None:
        assert _has_eoir_vocabulary("The new electro-optical targeting pod was unveiled.") is True

    def test_hebrew_keyword_hit(self) -> None:
        assert _has_eoir_vocabulary("הפוד האלקטרו-אופטי החדש כולל חיישן תרמי.") is True

    def test_no_hit_on_unrelated_text(self) -> None:
        assert _has_eoir_vocabulary("A new deepfake AI video has gone viral on social media.") is False

    def test_short_taxonomy_acronym_does_not_false_positive_inside_word(self) -> None:
        """Regression: a short taxonomy-mined term (e.g. "SoC") must not match as a substring of
        an unrelated English word (e.g. "social") -- word-boundary matching required."""
        assert _has_eoir_vocabulary("Many people shared it on social media today.") is False

    def test_empty_text(self) -> None:
        assert _has_eoir_vocabulary("") is False


class TestWatchlistAliasHit:
    def test_hits_canonical_name(self) -> None:
        assert _watchlist_alias_hit("Elbit Systems named a new CFO today.") is True

    def test_hits_alias(self) -> None:
        assert _watchlist_alias_hit("אלביט מערכות הודיעה על מינוי חדש.") is True

    def test_no_hit(self) -> None:
        assert _watchlist_alias_hit("A completely unrelated company made an announcement.") is False


class TestApplyNoEoirGate:
    def _item(self, **overrides: object) -> dict:
        base = {"id": 117, "title": "AI deepfake video fools viewers", "clean_text": "A deepfake AI video went viral on social media."}
        return {**base, **overrides}

    def test_item_117_style_gated_to_out_of_scope(self) -> None:
        """Regression for item 117: an AI/deepfake story with no EO/IR content, classified
        c_uas with zero entities, must be forced to out_of_scope."""
        out = ClassifyOut(domain="c_uas", subdomain="detect_track", report_kind="verified_report", one_line_he="x")
        gated = apply_no_eoir_gate(self._item(), out)
        assert gated.domain == "out_of_scope"
        assert gated.subdomain == ""
        assert gated.relevance_note == "gate:no_eoir_vocabulary"

    def test_not_gated_when_entities_present(self) -> None:
        out = ClassifyOut(
            domain="c_uas",
            subdomain="",
            report_kind="verified_report",
            entities=[EntityMention(name="Elbit", kind="company")],
            one_line_he="x",
        )
        gated = apply_no_eoir_gate(self._item(), out)
        assert gated.domain == "c_uas"

    def test_not_gated_when_eoir_vocabulary_present(self) -> None:
        item = self._item(
            title="New targeting pod",
            clean_text="The electro-optical targeting pod features a FLIR sensor.",
        )
        out = ClassifyOut(domain="airborne_pods", subdomain="targeting_pods", report_kind="verified_report", one_line_he="x")
        gated = apply_no_eoir_gate(item, out)
        assert gated.domain == "airborne_pods"

    def test_not_gated_when_watchlist_alias_present(self) -> None:
        item = self._item(title="Elbit Systems appoints new CFO", clean_text="Elbit Systems named a new CFO.")
        out = ClassifyOut(domain="secondary", subdomain="", report_kind="verified_report", one_line_he="x")
        gated = apply_no_eoir_gate(item, out)
        assert gated.domain == "secondary"

    def test_already_out_of_scope_is_a_noop(self) -> None:
        out = ClassifyOut(domain="out_of_scope", subdomain="", report_kind="rumor_speculation", relevance_note="already out", one_line_he="x")
        gated = apply_no_eoir_gate(self._item(), out)
        assert gated.relevance_note == "already out"
