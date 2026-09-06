"""Tests for eoa.qa.bakeoff — candidate swapping, deterministic checks, blind judge harness.

All LLM calls are mocked (docs/CONVENTIONS.md rule 10: unit tests never touch Ollama/a real CLI).
"""

from __future__ import annotations

import random
from unittest.mock import MagicMock, patch

import pytest

from eoa.config import settings
from eoa.pipeline import analyze as analyze_mod
from eoa.pipeline import classify as classify_mod
from eoa.pipeline import triage as triage_mod
from eoa.qa.bakeoff import (
    Candidate,
    JudgeItemScore,
    JudgeVerdict,
    blind_judge_item,
    candidate_by_name,
    candidate_context,
    deterministic_checks_for_candidate,
)

_PIPELINE_MODULES = (classify_mod, triage_mod, analyze_mod)


# ---------------------------------------------------------------------------------------------
# candidate_context: ollama role-remap
# ---------------------------------------------------------------------------------------------


class TestCandidateContextOllama:
    def test_swaps_role_model_and_restores(self):
        s = settings()
        prior = s.models.get("resident")
        candidate = Candidate("fake_ollama", "ollama", "Fake", model_key="gemma4_12b")

        with candidate_context(candidate, role="resident"):
            assert s.models["resident"] == "gemma4_12b"

        assert s.models.get("resident") == prior

    def test_restores_even_on_exception(self):
        s = settings()
        prior = s.models.get("resident")
        candidate = Candidate("fake_ollama", "ollama", "Fake", model_key="gemma4_e4b")

        with pytest.raises(RuntimeError):
            with candidate_context(candidate, role="resident"):
                assert s.models["resident"] == "gemma4_e4b"
                raise RuntimeError("boom")

        assert s.models.get("resident") == prior

    def test_missing_model_key_raises(self):
        candidate = Candidate("bad", "ollama", "Bad")
        with pytest.raises(ValueError, match="model_key"):
            with candidate_context(candidate):
                pass

    def test_unknown_kind_raises(self):
        candidate = Candidate("bad", "weird", "Bad")
        with pytest.raises(ValueError, match="unknown candidate kind"):
            with candidate_context(candidate):
                pass


# ---------------------------------------------------------------------------------------------
# candidate_context: cloud provider injection
# ---------------------------------------------------------------------------------------------


class TestCandidateContextCloud:
    def test_injects_provider_when_caller_omits_one(self):
        candidate = Candidate("fake_cloud", "cloud", "Fake", provider="claude:claude-sonnet-5")
        real_originals = {m: m.chat_structured for m in _PIPELINE_MODULES}
        captured_providers = []

        def fake(role, schema, messages, *, task="classify", interactive=False, options=None, provider=None):
            captured_providers.append(provider)
            return MagicMock()

        for m in _PIPELINE_MODULES:
            m.chat_structured = fake
        fake_ref = classify_mod.chat_structured

        try:
            with candidate_context(candidate):
                # inner call passes provider=None, as every stage wrapper (classify_item etc) does
                classify_mod.chat_structured("resident", object, [], provider=None)
            assert captured_providers == ["claude:claude-sonnet-5"]
            # candidate_context must restore to what was bound when the context was entered
            # (our `fake`), not to the module's original pre-test function.
            assert classify_mod.chat_structured is fake_ref
        finally:
            for m in _PIPELINE_MODULES:
                m.chat_structured = real_originals[m]

    def test_does_not_override_an_explicit_provider(self):
        candidate = Candidate("fake_cloud", "cloud", "Fake", provider="claude:claude-sonnet-5")
        real_original = classify_mod.chat_structured
        captured = []

        def fake(role, schema, messages, *, task="classify", interactive=False, options=None, provider=None):
            captured.append(provider)
            return MagicMock()

        classify_mod.chat_structured = fake
        try:
            with candidate_context(candidate):
                classify_mod.chat_structured("resident", object, [], provider="agy:gemini-3.1-pro-high")
            assert captured == ["agy:gemini-3.1-pro-high"]
        finally:
            classify_mod.chat_structured = real_original

    def test_missing_provider_raises(self):
        candidate = Candidate("bad", "cloud", "Bad")
        with pytest.raises(ValueError, match="provider"):
            with candidate_context(candidate):
                pass

    def test_all_three_pipeline_modules_are_patched(self):
        candidate = Candidate("fake_cloud", "cloud", "Fake", provider="agy:gemini-3.8-flash-high")
        real_originals = {m: m.chat_structured for m in _PIPELINE_MODULES}
        try:
            with candidate_context(candidate):
                for m in _PIPELINE_MODULES:
                    assert m.chat_structured is not real_originals[m]
            for m in _PIPELINE_MODULES:
                assert m.chat_structured is real_originals[m]
        finally:
            for m, orig in real_originals.items():
                m.chat_structured = orig


def test_candidate_by_name_lookup_and_miss():
    c = candidate_by_name("dictalm3_12b")
    assert c.kind == "ollama"
    with pytest.raises(KeyError):
        candidate_by_name("does_not_exist")


# ---------------------------------------------------------------------------------------------
# Deterministic checks (thin reuse wrapper over eoa.qa.d1_classify/d2_summary)
# ---------------------------------------------------------------------------------------------


class TestDeterministicChecks:
    def _clean_item(self, **overrides):
        item = {
            "id": 1,
            "domain": "airborne_pods",
            "subdomain": "targeting_pods",
            "entities_mentioned": ["L3Harris"],
            "key_facts": ["fact one", "fact two"],
            "triage_reason": 'פריט משמעותי בתחום מטע"ד עם חוזה ממשלתי גדול.',
            "summary_he": (
                'החברה קיבלה חוזה בסך 310 מיליון דולר לאספקת פודי כיוון (targeting pods) חדשים לצבא. '
                "המערכת כוללת חיישן MWIR משופר."
            ),
            "so_what_he": "להערכתנו החוזה מחזק את מעמדה התחרותי של החברה בשוק פודי הכיוון האמריקאי.",
            "uncertainty_he": "",
            "tech_readiness_note_he": "",
            "score": 8,
            "level": "red",
        }
        item.update(overrides)
        return item

    def test_clean_item_scores_well_on_d1_and_d2(self):
        items = [self._clean_item()]
        result = deterministic_checks_for_candidate(items)
        assert result["D1"].score_0_100 is not None
        assert result["D1"].score_0_100 >= 80
        assert result["D2"].score_0_100 is not None
        assert result["D2"].score_0_100 >= 60

    def test_gershayim_violation_lowers_d1(self):
        bad = self._clean_item(
            triage_reason='זהו נימוק על ארה"ב עם גרש לא תקני במקום גרשיים עבריים כהלכה, לסיום המשפט.'
        )
        good = self._clean_item()
        det_bad = deterministic_checks_for_candidate([bad])
        det_good = deterministic_checks_for_candidate([good])
        assert det_bad["D1"].score_0_100 <= det_good["D1"].score_0_100

    def test_invalid_subdomain_flagged(self):
        bad = self._clean_item(subdomain="not_a_real_subdomain")
        result = deterministic_checks_for_candidate([bad])
        names_failed = [c.name for c in result["D1"].checks if not c.passed]
        assert "subdomain_valid_vs_taxonomy" in names_failed

    def test_empty_sample_returns_none_score(self):
        result = deterministic_checks_for_candidate([])
        assert result["D1"].score_0_100 is None
        assert result["D2"].score_0_100 is None


# ---------------------------------------------------------------------------------------------
# Anonymised blind-judge harness
# ---------------------------------------------------------------------------------------------


class TestBlindJudge:
    def test_shuffle_and_unshuffle_reattributes_scores_correctly(self):
        item = {"id": 1, "title": "t", "clean_text": "some source text"}
        outputs = {
            "model_a": {"summary_he": "a"},
            "model_b": {"summary_he": "b"},
            "model_c": {"summary_he": "c"},
        }
        captured_messages = []

        def fake_chat_structured(role, schema, messages, *, task="judge", interactive=False,
                                  options=None, provider=None):
            captured_messages.append(messages)
            # score CANDIDATE_1/2/3 90/60/30 regardless of which real model each label maps to
            # this run -- that mapping is exactly what blind_judge_item must unshuffle correctly.
            return JudgeVerdict(
                scores=[
                    JudgeItemScore(label="CANDIDATE_1", score_0_100=90, notes="mock"),
                    JudgeItemScore(label="CANDIDATE_2", score_0_100=60, notes="mock"),
                    JudgeItemScore(label="CANDIDATE_3", score_0_100=30, notes="mock"),
                ]
            )

        with patch("eoa.qa.bakeoff.chat_structured", side_effect=fake_chat_structured):
            result = blind_judge_item(
                item, outputs, judge_provider="claude:claude-sonnet-5", rng=random.Random(42)
            )

        assert set(result.keys()) == {"model_a", "model_b", "model_c"}
        scores = {name: s.score_0_100 for name, s in result.items()}
        assert sorted(scores.values()) == [30, 60, 90]

        # the prompt sent to the judge must never contain a real candidate name
        prompt_text = captured_messages[0][0]["content"]
        for real_name in outputs:
            assert real_name not in prompt_text

    def test_unknown_label_in_verdict_is_dropped_not_raised(self):
        item = {"id": 1, "title": "t", "clean_text": "x"}
        outputs = {"model_a": {"x": 1}, "model_b": {"x": 2}}

        def fake_chat_structured(role, schema, messages, *, task="judge", interactive=False,
                                  options=None, provider=None):
            return JudgeVerdict(scores=[JudgeItemScore(label="CANDIDATE_99", score_0_100=50, notes="")])

        with patch("eoa.qa.bakeoff.chat_structured", side_effect=fake_chat_structured):
            result = blind_judge_item(item, outputs, rng=random.Random(1))

        assert result == {}

    def test_shuffle_order_depends_on_seed(self):
        item = {"id": 1, "title": "t", "clean_text": "x"}
        outputs = {f"model_{i}": {"marker": i} for i in range(6)}
        first_positions = []

        def make_fake(store):
            def fake(role, schema, messages, *, task="judge", interactive=False, options=None, provider=None):
                prompt = messages[0]["content"]
                store.append(prompt.index('"marker": 0'))
                return JudgeVerdict(
                    scores=[
                        JudgeItemScore(label=f"CANDIDATE_{i + 1}", score_0_100=50, notes="")
                        for i in range(6)
                    ]
                )

            return fake

        with patch("eoa.qa.bakeoff.chat_structured", side_effect=make_fake(first_positions)):
            blind_judge_item(item, outputs, rng=random.Random(1))
        with patch("eoa.qa.bakeoff.chat_structured", side_effect=make_fake(first_positions)):
            blind_judge_item(item, outputs, rng=random.Random(2))

        # overwhelmingly likely to differ across two different seeds with 6 candidates --
        # guards against candidate_context/blind_judge_item shuffling being a silent no-op.
        assert len(set(first_positions)) == 2
