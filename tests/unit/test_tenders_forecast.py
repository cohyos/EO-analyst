"""Tests for eoa.tenders.forecast (section 5.2 / FR-5.2) -- pure logic, no DB/LLM/network."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.tenders.forecast import (
    ForecastCandidate,
    ForecastStats,
    PlatformSpec,
    _build_candidates,
    _fallback_rationale,
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
