"""Unit tests for the LLM evaluation harness (no GPU, no DB)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eoa.llm.schemas.analysis import ClassifyOut, EntityMention, TriageOut
from evals.judge_hebrew import score_hebrew
from evals.run_evals import LEVEL_ORDINAL, compute_metrics, load_golden_set


class TestHebrewScoring:
    """Test Hebrew text quality heuristics."""

    def test_hebrew_ratio_all_hebrew(self) -> None:
        text = "זה טקסט בעברית טהורה"
        metrics = score_hebrew(text)
        assert metrics.hebrew_ratio > 0.8, "Should be mostly Hebrew"
        assert not metrics.telegraphic_flag

    def test_hebrew_ratio_mixed(self) -> None:
        text = "זה טקסט עם (English) מילים"
        metrics = score_hebrew(text)
        assert 0.4 < metrics.hebrew_ratio < 0.9, "Should be mixed"

    def test_latin_in_parens(self) -> None:
        text = "מערכה משלבת (sensor) וגם (algorithm) חדשות"
        metrics = score_hebrew(text)
        assert metrics.latin_terms_in_parens_ratio == 1.0, "All parens are Latin"

    def test_telegraphic_detection(self) -> None:
        text = "קצר. מאוד. קצר. מאוד. קצר."
        metrics = score_hebrew(text)
        assert metrics.telegraphic_flag, "Should detect telegraphic style"

    def test_empty_text(self) -> None:
        metrics = score_hebrew("")
        assert metrics.hebrew_ratio == 0
        assert metrics.latin_terms_in_parens_ratio == 0
        assert metrics.avg_sentence_len == 0


class TestMetricsComputation:
    """Test metric calculation functions (no LLM)."""

    def test_classify_domain_match(self) -> None:
        pred = ClassifyOut(
            domain="airborne_pods",
            subdomain="targeting_pods",
            report_kind="company_pr",
            entities=[EntityMention(name="Elbit", kind="company")],
            one_line_he="בדיקה",
        )
        expected = {
            "domain": "airborne_pods",
            "subdomain": "targeting_pods",
            "report_kind": "company_pr",
            "entities": ["Elbit"],
        }
        metrics = compute_metrics(pred, expected, "classify")
        assert metrics["domain_match"] is True
        assert metrics["report_kind_match"] is True

    def test_classify_domain_mismatch(self) -> None:
        pred = ClassifyOut(
            domain="land_surveillance",
            subdomain="border_towers",
            report_kind="company_pr",
            entities=[],
            one_line_he="בדיקה",
        )
        expected = {
            "domain": "airborne_pods",
            "report_kind": "company_pr",
            "entities": [],
        }
        metrics = compute_metrics(pred, expected, "classify")
        assert metrics["domain_match"] is False

    def test_entity_recall_perfect(self) -> None:
        pred = ClassifyOut(
            domain="airborne_pods",
            subdomain="",
            report_kind="company_pr",
            entities=[
                EntityMention(name="Elbit", kind="company"),
                EntityMention(name="F-16I", kind="system"),
            ],
            one_line_he="בדיקה",
        )
        expected = {"domain": "airborne_pods", "entities": ["Elbit", "F-16I"]}
        metrics = compute_metrics(pred, expected, "classify")
        assert metrics["entity_recall"] == 1.0

    def test_entity_recall_partial(self) -> None:
        pred = ClassifyOut(
            domain="airborne_pods",
            subdomain="",
            report_kind="company_pr",
            entities=[EntityMention(name="Elbit", kind="company")],
            one_line_he="בדיקה",
        )
        expected = {
            "domain": "airborne_pods",
            "entities": ["Elbit", "IAI", "Raytheon"],
        }
        metrics = compute_metrics(pred, expected, "classify")
        assert 0 < metrics["entity_recall"] < 1.0

    def test_entity_recall_none_expected(self) -> None:
        pred = ClassifyOut(
            domain="airborne_pods",
            subdomain="",
            report_kind="company_pr",
            entities=[],
            one_line_he="בדיקה",
        )
        expected = {"domain": "airborne_pods", "entities": []}
        metrics = compute_metrics(pred, expected, "classify")
        assert metrics["entity_recall"] == 1.0

    def test_triage_level_exact_match(self) -> None:
        pred = TriageOut(
            score=9,
            level="red",
            novelty=4,
            magnitude=5,
            core_relevance=5,
            reason_he="חשוב",
        )
        expected = {"level": "red", "score_range": [8, 10]}
        metrics = compute_metrics(pred, expected, "triage")
        assert metrics["level_match"] is True
        assert metrics["level_ordinal_match"] is True
        assert metrics["score_in_range"] is True

    def test_triage_level_ordinal_distance(self) -> None:
        pred = TriageOut(
            score=7,
            level="orange",
            novelty=3,
            magnitude=3,
            core_relevance=3,
            reason_he="טוב",
        )
        expected = {"level": "red", "score_range": [1, 10]}
        metrics = compute_metrics(pred, expected, "triage")
        assert metrics["level_match"] is False
        assert metrics["level_ordinal_match"] is True  # orange and red are 1 apart
        assert metrics["score_in_range"] is True

    def test_triage_level_out_of_range(self) -> None:
        pred = TriageOut(
            score=2,
            level="archive",
            novelty=1,
            magnitude=1,
            core_relevance=1,
            reason_he="זניח",
        )
        expected = {"level": "red", "score_range": [8, 10]}
        metrics = compute_metrics(pred, expected, "triage")
        assert metrics["score_in_range"] is False


class TestGoldenSetValidation:
    """Test that golden items are well-formed and taxonomy-compliant."""

    def test_golden_items_exist(self) -> None:
        golden_path = Path(__file__).parent.parent.parent / "evals" / "golden" / "classify_triage.jsonl"
        assert golden_path.exists(), f"Golden set not found at {golden_path}"

    def test_golden_items_parseable(self) -> None:
        golden_path = Path(__file__).parent.parent.parent / "evals" / "golden" / "classify_triage.jsonl"
        items = []
        with open(golden_path) as f:
            for line_no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    items.append(obj)
                except json.JSONDecodeError as exc:
                    pytest.fail(f"Line {line_no} is not valid JSON: {exc}")
        assert len(items) > 0, "No items in golden set"
        assert len(items) == 40, f"Expected 40 items, found {len(items)}"

    def test_golden_items_have_required_fields(self) -> None:
        golden_path = Path(__file__).parent.parent.parent / "evals" / "golden" / "classify_triage.jsonl"
        with open(golden_path) as f:
            for line in f:
                if not line.strip():
                    continue
                obj = json.loads(line)
                assert "id" in obj, "Missing id"
                assert "title" in obj, "Missing title"
                assert "text" in obj, "Missing text"
                assert "lang" in obj, "Missing lang"
                assert "source_kind" in obj, "Missing source_kind"
                assert "expected" in obj, "Missing expected"
                assert "human_verified" in obj, "Missing human_verified"

    def test_golden_expected_fields(self) -> None:
        golden_path = Path(__file__).parent.parent.parent / "evals" / "golden" / "classify_triage.jsonl"
        valid_domains = {
            "airborne_pods",
            "land_surveillance",
            "naval_surveillance",
            "air_defense",
            "c_uas",
            "computer_vision",
            "secondary",
            "out_of_scope",
        }
        valid_report_kinds = {
            "verified_report",
            "company_pr",
            "rumor_speculation",
            "academic",
            "tender",
            "patent",
            "regulatory",
        }
        valid_levels = {"red", "orange", "yellow", "archive"}

        with open(golden_path) as f:
            for line_no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                obj = json.loads(line)
                exp = obj.get("expected", {})

                domain = exp.get("domain")
                assert domain in valid_domains, f"Line {line_no}: invalid domain '{domain}'"

                report_kind = exp.get("report_kind")
                assert report_kind in valid_report_kinds, f"Line {line_no}: invalid report_kind '{report_kind}'"

                level = exp.get("level")
                assert level in valid_levels, f"Line {line_no}: invalid level '{level}'"

                score_range = exp.get("score_range", [1, 10])
                assert isinstance(score_range, list) and len(score_range) == 2, f"Line {line_no}: invalid score_range"
                assert 1 <= score_range[0] <= score_range[1] <= 10, f"Line {line_no}: score_range out of bounds"

    def test_golden_text_length(self) -> None:
        """Verify text is within 120–250 word range."""
        golden_path = Path(__file__).parent.parent.parent / "evals" / "golden" / "classify_triage.jsonl"
        with open(golden_path) as f:
            for line_no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                obj = json.loads(line)
                text = obj.get("text", "")
                word_count = len(text.split())
                # Allow some flexibility: 100–300 words
                assert 100 <= word_count <= 300, (
                    f"Line {line_no}: text has {word_count} words, expected 120–250"
                )

    def test_golden_distribution(self) -> None:
        """Check distribution across source_kind, lang, expected.level."""
        golden_path = Path(__file__).parent.parent.parent / "evals" / "golden" / "classify_triage.jsonl"
        items = []
        with open(golden_path) as f:
            for line in f:
                if line.strip():
                    items.append(json.loads(line))

        # Count by source_kind
        source_kinds = {}
        langs = {}
        levels = {}
        for item in items:
            sk = item.get("source_kind", "?")
            source_kinds[sk] = source_kinds.get(sk, 0) + 1
            lang = item.get("lang", "?")
            langs[lang] = langs.get(lang, 0) + 1
            level = item.get("expected", {}).get("level", "?")
            levels[level] = levels.get(level, 0) + 1

        # Verify we have reasonable diversity
        assert len(source_kinds) >= 5, f"Expected >= 5 source_kinds, got {len(source_kinds)}: {source_kinds}"
        assert len(langs) >= 2, f"Expected >= 2 languages, got {len(langs)}: {langs}"
        assert "he" in langs, "Expected Hebrew items"
        assert len(levels) == 4, f"Expected 4 levels, got {len(levels)}: {levels}"

        # Verify counts align with task spec
        assert langs.get("he", 0) >= 3, f"Expected >= 3 Hebrew items, got {langs.get('he', 0)}"
        assert levels.get("red", 0) >= 4, f"Expected >= 4 red items, got {levels.get('red', 0)}"
        assert levels.get("archive", 0) >= 4, f"Expected >= 4 archive items, got {levels.get('archive', 0)}"


class TestLevelOrdinal:
    """Test triage level ordinal mapping."""

    def test_ordinal_values(self) -> None:
        assert LEVEL_ORDINAL["red"] > LEVEL_ORDINAL["orange"]
        assert LEVEL_ORDINAL["orange"] > LEVEL_ORDINAL["yellow"]
        assert LEVEL_ORDINAL["yellow"] > LEVEL_ORDINAL["archive"]

    def test_ordinal_distances(self) -> None:
        assert abs(LEVEL_ORDINAL["red"] - LEVEL_ORDINAL["orange"]) == 1
        assert abs(LEVEL_ORDINAL["orange"] - LEVEL_ORDINAL["yellow"]) == 1
        assert abs(LEVEL_ORDINAL["yellow"] - LEVEL_ORDINAL["archive"]) == 1
