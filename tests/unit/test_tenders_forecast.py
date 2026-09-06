"""Tests for eoa.tenders.forecast (section 5.2 / FR-5.2) -- pure logic, no DB/LLM/network."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest

from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.tenders.forecast import (
    _MAX_CHARS_PER_TRIGGER_ITEM,
    _MAX_DATA_BLOCK_CHARS,
    _MAX_TRIGGER_ITEMS_FOR_RATIONALE,
    ForecastCandidate,
    ForecastStats,
    PlatformSpec,
    _build_candidates,
    _fallback_rationale,
    _item_ids_from_sources,
    _llm_rationale_guarded,
    _platform_type_contradiction,
    _rationale_data_block,
    _rationale_guard_failure,
    _regenerate_flagged_forecasts,
    _resolve_buyer_country,
    _upsert_forecast,
    _window,
    compute_likelihood,
    forecast_tenders,
    load_platform_payloads,
)


def _platform(**overrides):
    base = {
        "key": "male_uav",
        "match": ["MALE UAV", "Reaper", "Heron"],
        "category_he": 'כטב"ם MALE',
        "payload_need_he": "מטע\"ד ג'ימבלי EO/IR",
        "payload_domain": "uav_gimbals",
        "typical_vendors": ["L3Harris", "Elbit", "IAI"],
        "lag_months": {"min": 3, "max": 18},
    }
    base.update(overrides)
    return PlatformSpec(base)


def _candidate(**overrides) -> ForecastCandidate:
    base = dict(
        platform_key="male_uav",
        platform_he='כטב"ם MALE',
        buyer_country="US",
        payload_need_he="מטע\"ד ג'ימבלי EO/IR",
        candidate_vendors=["L3Harris", "Elbit"],
        trigger_event_ids=[1],
        trigger_item_ids=[10],
        trigger_texts=["Reaper UAV contract awarded"],
        lag_min=3,
        lag_max=18,
    )
    base.update(overrides)
    return ForecastCandidate(**base)


# --------------------------------------------------------------------------
# platform_payloads.yaml
# --------------------------------------------------------------------------


class TestLoadPlatformPayloads:
    def test_loads_real_config(self):
        platforms = load_platform_payloads()
        assert len(platforms) >= 8
        keys = {p.key for p in platforms}
        assert "male_uav" in keys
        assert "c_uas_program" in keys
        assert "fighter_jet" in keys

    def test_every_platform_has_vendors_and_lag(self):
        for p in load_platform_payloads():
            assert p.typical_vendors, p.key
            assert p.lag_min <= p.lag_max


class TestPlatformSpecMatches:
    def test_matches_case_insensitive_substring(self):
        p = _platform()
        assert p.matches("A new MQ-9 Reaper was delivered") is True

    def test_no_match(self):
        p = _platform()
        assert p.matches("A new submarine was launched") is False


# --------------------------------------------------------------------------
# candidate building
# --------------------------------------------------------------------------


class TestBuildCandidates:
    def test_single_event_creates_one_candidate(self):
        events = [
            {
                "event_id": 1,
                "item_id": 10,
                "title": "Reaper UAV contract awarded",
                "item_title": None,
                "clean_text": None,
                "summary_he": None,
                "geography": "US",
                "customer": "US Air Force",
            }
        ]
        with patch("eoa.tenders.forecast._watchlist_vendor_names", return_value={"L3Harris", "Elbit"}):
            candidates = _build_candidates([_platform()], events)
        assert len(candidates) == 1
        assert candidates[0].buyer_country == "US"
        assert candidates[0].trigger_event_ids == [1]

    def test_two_events_same_platform_buyer_corroborate_one_candidate(self):
        events = [
            {
                "event_id": 1,
                "item_id": 10,
                "title": "Reaper UAV contract awarded to US Air Force",
                "item_title": None,
                "clean_text": None,
                "summary_he": None,
                "geography": "US",
            },
            {
                "event_id": 2,
                "item_id": 11,
                "title": "Second Reaper UAV deployment for US Air Force",
                "item_title": None,
                "clean_text": None,
                "summary_he": None,
                "geography": "US",
            },
        ]
        with patch("eoa.tenders.forecast._watchlist_vendor_names", return_value=set()):
            candidates = _build_candidates([_platform()], events)
        assert len(candidates) == 1
        assert candidates[0].trigger_event_ids == [1, 2]

    def test_no_match_yields_no_candidates(self):
        events = [
            {
                "event_id": 1,
                "item_id": 10,
                "title": "New office building opened",
                "item_title": None,
                "clean_text": None,
                "summary_he": None,
                "geography": "US",
            }
        ]
        with patch("eoa.tenders.forecast._watchlist_vendor_names", return_value=set()):
            candidates = _build_candidates([_platform()], events)
        assert candidates == []

    def test_vendors_filtered_to_watchlist_when_intersection_nonempty(self):
        events = [
            {
                "event_id": 1,
                "item_id": 10,
                "title": "Reaper UAV contract",
                "item_title": None,
                "clean_text": None,
                "summary_he": None,
                "geography": "US",
            }
        ]
        with patch("eoa.tenders.forecast._watchlist_vendor_names", return_value={"L3Harris"}):
            candidates = _build_candidates([_platform()], events)
        assert candidates[0].candidate_vendors == ["L3Harris"]

    def test_vendors_fallback_to_full_list_when_no_intersection(self):
        events = [
            {
                "event_id": 1,
                "item_id": 10,
                "title": "Reaper UAV contract",
                "item_title": None,
                "clean_text": None,
                "summary_he": None,
                "geography": "US",
            }
        ]
        with patch("eoa.tenders.forecast._watchlist_vendor_names", return_value={"SomeOtherCo"}):
            candidates = _build_candidates([_platform()], events)
        assert candidates[0].candidate_vendors == ["L3Harris", "Elbit", "IAI"]


# --------------------------------------------------------------------------
# likelihood rubric
# --------------------------------------------------------------------------


class TestComputeLikelihood:
    def test_base_score_single_event_no_rfi_no_history(self):
        cand = _candidate(trigger_texts=["Reaper UAV contract awarded, routine procurement"])
        with patch("eoa.tenders.forecast._has_prior_history", return_value=False):
            score = compute_likelihood(cand)
        assert score == 0.30

    def test_corroboration_bonus_capped(self):
        cand = _candidate(trigger_event_ids=[1, 2, 3, 4, 5], trigger_texts=["x"] * 5)
        with patch("eoa.tenders.forecast._has_prior_history", return_value=False):
            score = compute_likelihood(cand)
        # base 0.30 + min(0.10*4, 0.30) = 0.30 + 0.30 = 0.60
        assert score == 0.60

    def test_rfi_mention_bonus(self):
        cand = _candidate(trigger_texts=["The Air Force issued an RFI for gimbal payloads"])
        with patch("eoa.tenders.forecast._has_prior_history", return_value=False):
            score = compute_likelihood(cand)
        assert score == 0.50  # 0.30 + 0.20

    def test_prior_history_bonus(self):
        cand = _candidate(trigger_texts=["routine contract award"])
        with patch("eoa.tenders.forecast._has_prior_history", return_value=True):
            score = compute_likelihood(cand)
        assert score == 0.45  # 0.30 + 0.15

    def test_all_bonuses_stack_below_the_cap(self):
        """base 0.30 + corroboration (capped +0.30) + RFI (+0.20) + prior history (+0.15) = 0.95,
        the maximum the current rubric coefficients can produce -- still under the 1.0 cap."""
        cand = _candidate(
            trigger_event_ids=[1, 2, 3, 4, 5, 6],
            trigger_texts=["RFI request for information sources sought"] * 6,
        )
        with patch("eoa.tenders.forecast._has_prior_history", return_value=True):
            score = compute_likelihood(cand)
        assert score == 0.95

    def test_score_never_exceeds_one(self):
        cand = _candidate(
            trigger_event_ids=list(range(1, 21)),
            trigger_texts=["RFI request for information sources sought"] * 20,
        )
        with patch("eoa.tenders.forecast._has_prior_history", return_value=True):
            score = compute_likelihood(cand)
        assert score <= 1.0

    def test_hebrew_rfi_terms_recognized(self):
        cand = _candidate(trigger_texts=["פורסמה בקשת מידע (RFI) למערכת חדשה"])
        with patch("eoa.tenders.forecast._has_prior_history", return_value=False):
            score = compute_likelihood(cand)
        assert score == 0.50


# --------------------------------------------------------------------------
# window math
# --------------------------------------------------------------------------


class TestWindow:
    def test_window_from_lag_months(self):
        cand = _candidate(lag_min=3, lag_max=18)
        today = dt.date(2026, 9, 4)
        window_from, window_to = _window(cand, today)
        assert window_from == dt.date(2026, 12, 4)
        assert window_to == dt.date(2028, 3, 4)


# --------------------------------------------------------------------------
# fallback rationale
# --------------------------------------------------------------------------


class TestFallbackRationale:
    def test_cites_trigger_items(self):
        cand = _candidate(trigger_item_ids=[10, 11])
        text = _fallback_rationale(cand)
        assert "[item 10]" in text
        assert "[item 11]" in text
        assert 'כטב"ם MALE' in text


# --------------------------------------------------------------------------
# F1: rationale data block caps (root cause -- an over-long prompt got its start truncated by
# Ollama, dropping the task instructions and producing leaked model reasoning in rationale_he)
# --------------------------------------------------------------------------


def _item_row(**overrides):
    base = dict(id=1, title="t", url="https://x/1", clean_text="c" * 50, summary_he="")
    base.update(overrides)
    return base


class TestRationaleDataBlockCaps:
    def test_caps_number_of_trigger_items(self):
        cand = _candidate(trigger_item_ids=list(range(1, 21)))
        items = {i: _item_row(id=i) for i in range(1, 21)}
        block = _rationale_data_block(cand, items)
        cited = [f"[item {i}]" for i in range(1, 21) if f"[item {i}]" in block]
        assert len(cited) <= _MAX_TRIGGER_ITEMS_FOR_RATIONALE

    def test_uses_most_recent_items_first(self):
        """trigger_item_ids is already most-recent-first; the cap must keep the head, not the tail."""
        cand = _candidate(trigger_item_ids=[1, 2, 3, 4, 5, 6, 7])
        items = {i: _item_row(id=i) for i in range(1, 8)}
        block = _rationale_data_block(cand, items)
        assert "[item 1]" in block
        assert "[item 7]" not in block

    def test_caps_chars_per_item(self):
        cand = _candidate(trigger_item_ids=[1])
        items = {1: _item_row(id=1, clean_text="x" * 5000, summary_he="")}
        block = _rationale_data_block(cand, items)
        # the DATA-wrapped text for item 1 must not carry the full 5000-char blob
        assert "x" * (_MAX_CHARS_PER_TRIGGER_ITEM + 1) not in block

    def test_prefers_summary_he_over_clean_text(self):
        cand = _candidate(trigger_item_ids=[1])
        items = {
            1: _item_row(id=1, clean_text="RAW CLEAN TEXT SHOULD NOT APPEAR", summary_he="short summary")
        }
        block = _rationale_data_block(cand, items)
        assert "short summary" in block
        assert "RAW CLEAN TEXT SHOULD NOT APPEAR" not in block

    def test_hard_total_cap_enforced(self):
        cand = _candidate(trigger_item_ids=[1, 2, 3, 4, 5])
        items = {
            i: _item_row(id=i, clean_text="y" * _MAX_CHARS_PER_TRIGGER_ITEM, summary_he="")
            for i in range(1, 6)
        }
        block = _rationale_data_block(cand, items)
        assert len(block) <= _MAX_DATA_BLOCK_CHARS

    def test_missing_items_are_skipped(self):
        cand = _candidate(trigger_item_ids=[1, 2])
        block = _rationale_data_block(cand, {1: _item_row(id=1)})
        assert "[item 1]" in block
        assert "[item 2]" not in block

    def test_no_items_returns_placeholder(self):
        cand = _candidate(trigger_item_ids=[99])
        assert _rationale_data_block(cand, {}) == "(אין פריטי מקור זמינים)"


# --------------------------------------------------------------------------
# F1.c: output guard against leaked model reasoning
# --------------------------------------------------------------------------


class TestRationaleGuardFailure:
    def test_valid_rationale_passes(self):
        assert _rationale_guard_failure("סביר שיתפרסם מכרז [item 10] בהתבסס על האירוע.") is None

    def test_missing_citation_rejected(self):
        assert _rationale_guard_failure("סביר שיתפרסם מכרז בהתבסס על האירוע, ללא הפניה.") == "no_citation"

    def test_empty_text_rejected(self):
        assert _rationale_guard_failure("") == "no_citation"

    def test_hebrew_leak_phrase_rejected(self):
        text = "השאלה מבקשת את התאריך המדויק [item 1]."
        assert _rationale_guard_failure(text) == "reasoning_leak"

    def test_english_leak_phrase_rejected(self):
        text = "The prompt asks for a summary of the text [item 1]."
        assert _rationale_guard_failure(text) == "reasoning_leak"

    def test_too_long_rationale_rejected(self):
        text = "סביר שיתפרסם מכרז [item 1]. " + ("א" * 900)
        assert _rationale_guard_failure(text) == "too_long"

    def test_short_valid_rationale_with_citation_not_flagged_as_leak(self):
        text = "סביר שיתפרסם מכרז/RFI/RFP לרכיב אלקטרו-אופטי [item 42] בשל ביקוש גובר בשוק."
        assert _rationale_guard_failure(text) is None


class TestLlmRationaleGuarded:
    def test_returns_first_attempt_when_it_passes_guard(self):
        with patch("eoa.tenders.forecast._llm_rationale", return_value="נימוק תקין [item 10]."):
            result = _llm_rationale_guarded(
                _candidate(), 0.5, (dt.date(2026, 12, 1), dt.date(2027, 6, 1)), "data", role="resident"
            )
        assert result == "נימוק תקין [item 10]."

    def test_retries_once_with_shorter_block_then_falls_back(self):
        leaked = "The prompt asks for a summary."
        with patch("eoa.tenders.forecast._llm_rationale", return_value=leaked) as mock_llm:
            result = _llm_rationale_guarded(
                _candidate(trigger_item_ids=[10]),
                0.5,
                (dt.date(2026, 12, 1), dt.date(2027, 6, 1)),
                "x" * 4000,
                role="resident",
            )
        assert mock_llm.call_count == 2
        assert "[item 10]" in result  # fell back to the deterministic rationale

    def test_second_attempt_success_is_used(self):
        with patch(
            "eoa.tenders.forecast._llm_rationale",
            side_effect=["The prompt asks...", "נימוק תקין בפעם השנייה [item 10]."],
        ):
            result = _llm_rationale_guarded(
                _candidate(trigger_item_ids=[10]),
                0.5,
                (dt.date(2026, 12, 1), dt.date(2027, 6, 1)),
                "data",
                role="resident",
            )
        assert result == "נימוק תקין בפעם השנייה [item 10]."

    def test_resource_unavailable_propagates_without_retry(self):
        """A genuine availability failure (not a bad-output guard failure) must propagate straight
        through so forecast_tenders's own except ResourceUnavailable branch handles it."""
        with patch(
            "eoa.tenders.forecast._llm_rationale", side_effect=ResourceUnavailable("no vram")
        ) as mock_llm:
            with pytest.raises(ResourceUnavailable):
                _llm_rationale_guarded(
                    _candidate(), 0.5, (dt.date(2026, 12, 1), dt.date(2027, 6, 1)), "data", role="resident"
                )
        assert mock_llm.call_count == 1


# --------------------------------------------------------------------------
# forecast_tenders orchestration (fully mocked DB/LLM)
# --------------------------------------------------------------------------


class TestForecastTendersOrchestration:
    def test_no_events_no_candidates(self):
        with patch("eoa.tenders.forecast._recent_trigger_events", return_value=[]):
            stats = forecast_tenders()
        assert isinstance(stats, ForecastStats)
        assert stats.candidates == 0
        assert stats.upserted == 0

    def test_llm_success_counted(self):
        events = [
            {
                "event_id": 1,
                "item_id": 10,
                "title": "Reaper UAV contract awarded",
                "item_title": None,
                "clean_text": None,
                "summary_he": None,
                "geography": "US",
            }
        ]
        with (
            patch("eoa.tenders.forecast._recent_trigger_events", return_value=events),
            patch("eoa.tenders.forecast._fetchall", return_value=[]),
            patch("eoa.tenders.forecast._watchlist_vendor_names", return_value=set()),
            patch("eoa.tenders.forecast._has_prior_history", return_value=False),
            patch("eoa.tenders.forecast._llm_rationale", return_value="נימוק בעברית"),
            patch("eoa.tenders.forecast._upsert_forecast", return_value=1) as mock_upsert,
        ):
            stats = forecast_tenders()

        assert stats.candidates == 1
        assert stats.upserted == 1
        assert stats.llm_used == 1
        mock_upsert.assert_called_once()

    def test_llm_deferred_falls_back_to_deterministic_rationale(self):
        events = [
            {
                "event_id": 1,
                "item_id": 10,
                "title": "Reaper UAV contract awarded",
                "item_title": None,
                "clean_text": None,
                "summary_he": None,
                "geography": "US",
            }
        ]
        with (
            patch("eoa.tenders.forecast._recent_trigger_events", return_value=events),
            patch("eoa.tenders.forecast._fetchall", return_value=[]),
            patch("eoa.tenders.forecast._watchlist_vendor_names", return_value=set()),
            patch("eoa.tenders.forecast._has_prior_history", return_value=False),
            patch("eoa.tenders.forecast._llm_rationale", side_effect=ResourceUnavailable("no vram")),
            patch("eoa.tenders.forecast._upsert_forecast", return_value=1) as mock_upsert,
        ):
            stats = forecast_tenders()

        assert stats.llm_deferred == 1
        assert stats.upserted == 1  # deterministic upsert still happens
        rationale_used = mock_upsert.call_args[0][3]
        assert "[item 10]" in rationale_used

    def test_llm_failure_falls_back_to_deterministic_rationale(self):
        events = [
            {
                "event_id": 1,
                "item_id": 10,
                "title": "Reaper UAV contract awarded",
                "item_title": None,
                "clean_text": None,
                "summary_he": None,
                "geography": "US",
            }
        ]
        with (
            patch("eoa.tenders.forecast._recent_trigger_events", return_value=events),
            patch("eoa.tenders.forecast._fetchall", return_value=[]),
            patch("eoa.tenders.forecast._watchlist_vendor_names", return_value=set()),
            patch("eoa.tenders.forecast._has_prior_history", return_value=False),
            patch("eoa.tenders.forecast._llm_rationale", side_effect=LLMOutputError("bad json")),
            patch("eoa.tenders.forecast._upsert_forecast", return_value=1),
        ):
            stats = forecast_tenders()

        assert stats.llm_failed == 1
        assert stats.upserted == 1


# --------------------------------------------------------------------------
# Q3-11 (docs/qa/findings_Q3_r1.md): buyer_country derivation
# --------------------------------------------------------------------------


class TestResolveBuyerCountry:
    def test_known_country_returned_unchanged(self):
        cand = _candidate(buyer_country="US")
        with patch("eoa.tenders.forecast._country_from_entities", return_value=None):
            assert _resolve_buyer_country(cand) == "US"

    def test_unknown_falls_back_to_entities_country(self):
        cand = _candidate(buyer_country="other")
        with patch("eoa.tenders.forecast._country_from_entities", return_value="IL"):
            assert _resolve_buyer_country(cand) == "IL"

    def test_unknown_falls_back_to_trigger_text_mention(self):
        cand = _candidate(buyer_country="other", trigger_texts=["Air Force of Israel selects new gimbal"])
        with patch("eoa.tenders.forecast._country_from_entities", return_value=None):
            assert _resolve_buyer_country(cand) == "IL"

    def test_unknown_falls_back_to_rationale_mention(self):
        cand = _candidate(buyer_country="other", trigger_texts=["a contract was signed"])
        with patch("eoa.tenders.forecast._country_from_entities", return_value=None):
            result = _resolve_buyer_country(
                cand, rationale_he="החוזה נחתם עבור חיל האוויר של גרמניה [item 10]."
            )
        assert result == "DE"

    def test_still_unknown_stays_other(self):
        cand = _candidate(buyer_country="other", trigger_texts=["a contract was signed"])
        with patch("eoa.tenders.forecast._country_from_entities", return_value=None):
            result = _resolve_buyer_country(cand, rationale_he="נימוק כללי ללא אזכור מדינה [item 10].")
        assert result == "other"


# --------------------------------------------------------------------------
# Q3-11: platform-type sanity check
# --------------------------------------------------------------------------


class TestPlatformTypeContradiction:
    def _all_platforms(self):
        return load_platform_payloads()

    def test_no_contradiction_for_clean_match(self):
        cand = _candidate(platform_key="male_uav", trigger_texts=["Reaper UAV delivered to customer"])
        assert _platform_type_contradiction(cand, self._all_platforms()) is None

    def test_helicopter_wording_contradicts_fixed_wing_uas(self):
        cand = _candidate(
            platform_key="male_uav",
            trigger_texts=["Reaper UAV program compared against an Apache attack helicopter bid"],
        )
        contradiction = _platform_type_contradiction(cand, self._all_platforms())
        assert contradiction is not None
        assert contradiction.key == "attack_helicopter"

    def test_unmapped_platform_key_never_flagged(self):
        cand = _candidate(
            platform_key="c_uas_program", trigger_texts=["Apache attack helicopter also mentioned"]
        )
        assert _platform_type_contradiction(cand, self._all_platforms()) is None


class TestCandidateBuyerCountryNormalizedInBuildCandidates:
    def test_unrecognized_geography_normalizes_to_other(self):
        events = [
            {
                "event_id": 1,
                "item_id": 10,
                "title": "Reaper UAV contract",
                "item_title": None,
                "clean_text": None,
                "summary_he": None,
                "geography": "Neverland",
            }
        ]
        with patch("eoa.tenders.forecast._watchlist_vendor_names", return_value=set()):
            candidates = _build_candidates([_platform()], events)
        assert candidates[0].buyer_country == "other"

    def test_different_spellings_of_same_country_corroborate_one_candidate(self):
        events = [
            {
                "event_id": 1,
                "item_id": 10,
                "title": "Reaper UAV contract",
                "item_title": None,
                "clean_text": None,
                "summary_he": None,
                "geography": "United States",
            },
            {
                "event_id": 2,
                "item_id": 11,
                "title": "Second Reaper UAV deployment",
                "item_title": None,
                "clean_text": None,
                "summary_he": None,
                "geography": "USA",
            },
        ]
        with patch("eoa.tenders.forecast._watchlist_vendor_names", return_value=set()):
            candidates = _build_candidates([_platform()], events)
        assert len(candidates) == 1
        assert candidates[0].buyer_country == "US"
        assert candidates[0].trigger_event_ids == [1, 2]


# --------------------------------------------------------------------------
# Q3-11: needs_regen
# --------------------------------------------------------------------------


class TestItemIdsFromSources:
    def test_parses_item_prefixed_entries(self):
        assert _item_ids_from_sources(["item:10", "item:22"]) == [10, 22]

    def test_ignores_malformed_entries(self):
        assert _item_ids_from_sources(["item:10", "not-an-item", "item:abc", None]) == [10]

    def test_none_input_returns_empty(self):
        assert _item_ids_from_sources(None) == []


class TestRegenerateFlaggedForecasts:
    def test_no_flagged_rows_returns_zero(self):
        with patch("eoa.tenders.forecast._fetchall", return_value=[]):
            assert _regenerate_flagged_forecasts("resident") == 0

    def test_successful_regen_clears_flag(self):
        flagged_row = {
            "id": 5,
            "platform": 'כטב"ם MALE',
            "buyer_country": "US",
            "trigger_event_id": 1,
            "trigger_item_id": 10,
            "payload_need": 'מטע"ד',
            "candidate_vendors": ["Elbit"],
            "likelihood": 0.4,
            "window_from": dt.date(2026, 1, 1),
            "window_to": dt.date(2026, 6, 1),
            "sources": ["item:10"],
        }

        def fake_fetchall(query, params=None):
            if "needs_regen" in query:
                return [flagged_row]
            return [{"id": 10, "title": "t", "url": "u", "clean_text": "c", "summary_he": "s"}]

        executed = []

        class _FakeConnCtx:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def cursor(self, row_factory=None):
                class _Cur:
                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False

                    def execute(self, q, p=None):
                        executed.append((q, p))

                return _Cur()

        with (
            patch("eoa.tenders.forecast._fetchall", side_effect=fake_fetchall),
            patch("eoa.tenders.forecast._llm_rationale", return_value="נימוק אמיתי [item 10]."),
            patch("eoa.tenders.forecast.connection", return_value=_FakeConnCtx()),
        ):
            regenerated = _regenerate_flagged_forecasts("resident")

        assert regenerated == 1
        assert any("needs_regen=false" in q for q, _ in executed)

    def test_still_unavailable_leaves_flag(self):
        flagged_row = {
            "id": 5,
            "platform": 'כטב"ם MALE',
            "buyer_country": "US",
            "trigger_event_id": 1,
            "trigger_item_id": 10,
            "payload_need": 'מטע"ד',
            "candidate_vendors": [],
            "likelihood": 0.4,
            "window_from": dt.date(2026, 1, 1),
            "window_to": dt.date(2026, 6, 1),
            "sources": ["item:10"],
        }

        def fake_fetchall(query, params=None):
            if "needs_regen" in query:
                return [flagged_row]
            return []

        with (
            patch("eoa.tenders.forecast._fetchall", side_effect=fake_fetchall),
            patch("eoa.tenders.forecast._llm_rationale", side_effect=ResourceUnavailable("no vram")),
        ):
            regenerated = _regenerate_flagged_forecasts("resident")

        assert regenerated == 0

    def test_row_with_no_recoverable_item_ids_skipped(self):
        flagged_row = {
            "id": 5,
            "platform": "x",
            "buyer_country": "US",
            "trigger_event_id": None,
            "trigger_item_id": None,
            "payload_need": "x",
            "candidate_vendors": [],
            "likelihood": 0.4,
            "window_from": dt.date(2026, 1, 1),
            "window_to": dt.date(2026, 6, 1),
            "sources": None,
        }
        with patch("eoa.tenders.forecast._fetchall", return_value=[flagged_row]):
            assert _regenerate_flagged_forecasts("resident") == 0


class TestForecastTendersNeedsRegenWiring:
    def test_fallback_rationale_marks_needs_regen_on_upsert(self):
        events = [
            {
                "event_id": 1,
                "item_id": 10,
                "title": "Reaper UAV contract awarded",
                "item_title": None,
                "clean_text": None,
                "summary_he": None,
                "geography": "US",
            }
        ]
        with (
            patch("eoa.tenders.forecast._recent_trigger_events", return_value=events),
            patch("eoa.tenders.forecast._fetchall", return_value=[]),
            patch("eoa.tenders.forecast._watchlist_vendor_names", return_value=set()),
            patch("eoa.tenders.forecast._has_prior_history", return_value=False),
            patch("eoa.tenders.forecast._llm_rationale", side_effect=ResourceUnavailable("no vram")),
            patch("eoa.tenders.forecast._upsert_forecast", return_value=1) as mock_upsert,
        ):
            stats = forecast_tenders()

        assert stats.needs_regen == 1
        assert mock_upsert.call_args.kwargs.get("needs_regen") is True

    def test_llm_success_does_not_mark_needs_regen(self):
        events = [
            {
                "event_id": 1,
                "item_id": 10,
                "title": "Reaper UAV contract awarded",
                "item_title": None,
                "clean_text": None,
                "summary_he": None,
                "geography": "US",
            }
        ]
        with (
            patch("eoa.tenders.forecast._recent_trigger_events", return_value=events),
            patch("eoa.tenders.forecast._fetchall", return_value=[]),
            patch("eoa.tenders.forecast._watchlist_vendor_names", return_value=set()),
            patch("eoa.tenders.forecast._has_prior_history", return_value=False),
            patch("eoa.tenders.forecast._llm_rationale", return_value="נימוק תקין [item 10]."),
            patch("eoa.tenders.forecast._upsert_forecast", return_value=1) as mock_upsert,
        ):
            stats = forecast_tenders()

        assert stats.needs_regen == 0
        assert mock_upsert.call_args.kwargs.get("needs_regen") is False


class TestUpsertForecastSourcesDedup:
    """Q3-11b (docs/qa/findings_Q3_r2.md): `tender_forecasts.sources` must not contain the same
    "item:N" entry more than once, even when `candidate.trigger_item_ids` itself has repeats
    (multiple triggering events landing on the same item)."""

    def test_duplicate_trigger_item_ids_produce_deduped_sources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        class _FakeCursor:
            def execute(self, query, params):
                captured["params"] = params

            def fetchone(self):
                return {"id": 1}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _FakeConnection:
            def cursor(self, row_factory=None):
                return _FakeCursor()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr("eoa.tenders.forecast.connection", lambda: _FakeConnection())

        candidate = _candidate(trigger_item_ids=[105, 105, 105, 58])
        _upsert_forecast(candidate, 0.7, (dt.date(2026, 1, 1), dt.date(2026, 6, 1)), "rationale")

        assert captured["params"]["sources"] == ["item:105", "item:58"]

    def test_single_trigger_item_id_unaffected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        class _FakeCursor:
            def execute(self, query, params):
                captured["params"] = params

            def fetchone(self):
                return {"id": 1}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _FakeConnection:
            def cursor(self, row_factory=None):
                return _FakeCursor()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr("eoa.tenders.forecast.connection", lambda: _FakeConnection())

        candidate = _candidate(trigger_item_ids=[10])
        _upsert_forecast(candidate, 0.7, (dt.date(2026, 1, 1), dt.date(2026, 6, 1)), "rationale")

        assert captured["params"]["sources"] == ["item:10"]
