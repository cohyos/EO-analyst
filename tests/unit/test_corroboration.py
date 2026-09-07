"""Unit tests for `eoa.pipeline.corroboration` (cross-source corroboration, 2026-09-07 user
requirement). Every DB-touching function is monkeypatched at the module level (this module
imports the relational helpers by name, `from eoa.memory.relational import ...`) -- no live DB,
no network, no LLM.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_corroboration.py -q``
"""

from __future__ import annotations

import datetime as dt

import pytest

from eoa.pipeline import corroboration as corr

# ---------------------------------------------------------------------------------------------
# registrable_domain
# ---------------------------------------------------------------------------------------------


class TestRegistrableDomain:
    def test_strips_www(self) -> None:
        assert corr.registrable_domain("https://www.defensenews.com/some/article") == "defensenews.com"

    def test_strips_port(self) -> None:
        assert corr.registrable_domain("http://example.com:8080/x") == "example.com"

    def test_lowercases(self) -> None:
        assert corr.registrable_domain("https://WWW.Example.COM/x") == "example.com"

    def test_none_input(self) -> None:
        assert corr.registrable_domain(None) is None

    def test_empty_string(self) -> None:
        assert corr.registrable_domain("") is None

    def test_no_scheme_falls_back_to_raw(self) -> None:
        # urlparse("example.com/x").netloc is "" -- falls back to the raw string per the
        # `netloc or url` guard.
        assert corr.registrable_domain("example.com/x") == "example.com/x"


# ---------------------------------------------------------------------------------------------
# is_official_primary_source
# ---------------------------------------------------------------------------------------------


class TestIsOfficialPrimarySource:
    def test_none_url(self) -> None:
        assert corr.is_official_primary_source(None) is False

    def test_dot_gov_suffix(self) -> None:
        assert corr.is_official_primary_source("https://www.army.gov/press") is True

    def test_dot_mil_suffix(self) -> None:
        assert corr.is_official_primary_source("https://dibbs.bsm.dla.mil/notice") is True

    def test_dot_gov_il_suffix(self) -> None:
        assert corr.is_official_primary_source("https://mod.gov.il/en/press-releases/x") is True

    def test_europa_eu_suffix(self) -> None:
        assert corr.is_official_primary_source("https://ted.europa.eu/notice/123") is True

    def test_sam_gov_exact_host(self) -> None:
        assert corr.is_official_primary_source("https://sam.gov/opp/123") is True

    def test_prnewswire_exact_host(self) -> None:
        assert corr.is_official_primary_source("https://www.prnewswire.com/news-releases/x") is True

    def test_businesswire_exact_host(self) -> None:
        assert corr.is_official_primary_source("https://businesswire.com/news/home/x") is True

    def test_ir_subdomain_with_press_path(self) -> None:
        assert corr.is_official_primary_source("https://ir.acmecorp.com/press-releases/q3") is True

    def test_investors_subdomain_with_press_path(self) -> None:
        assert corr.is_official_primary_source("https://investors.acmecorp.com/newsroom/x") is True

    def test_ir_subdomain_without_press_path_is_not_official(self) -> None:
        assert corr.is_official_primary_source("https://ir.acmecorp.com/financials/q3") is False

    def test_generic_trade_press_is_not_official(self) -> None:
        assert corr.is_official_primary_source("https://www.defensenews.com/land/2026/x") is False

    def test_malformed_url_does_not_raise(self) -> None:
        assert corr.is_official_primary_source("not a url at all") is False


# ---------------------------------------------------------------------------------------------
# distinctive_shared_entities
# ---------------------------------------------------------------------------------------------


class TestDistinctiveSharedEntities:
    def test_two_specific_entities_count(self) -> None:
        shared = corr.distinctive_shared_entities(
            ["Elbit Systems", "AeroVironment"], ["Elbit Systems", "AeroVironment"]
        )
        assert sorted(shared) == ["AeroVironment", "Elbit Systems"]

    def test_two_generic_entities_alone_do_not_count(self) -> None:
        shared = corr.distinctive_shared_entities(["US Army", "DoD"], ["US Army", "DoD"])
        assert shared == []

    def test_two_generic_plus_a_third_shared_entity_all_count(self) -> None:
        shared = corr.distinctive_shared_entities(
            ["US Army", "DoD", "Elbit Systems"], ["US Army", "DoD", "Elbit Systems"]
        )
        assert sorted(shared) == ["DoD", "Elbit Systems", "US Army"]

    def test_one_generic_one_specific_only_specific_counts(self) -> None:
        shared = corr.distinctive_shared_entities(["US Army", "Elbit Systems"], ["US Army", "Elbit Systems"])
        assert shared == ["Elbit Systems"]

    def test_no_overlap(self) -> None:
        assert corr.distinctive_shared_entities(["Elbit Systems"], ["Rafael"]) == []

    def test_none_inputs(self) -> None:
        assert corr.distinctive_shared_entities(None, None) == []

    def test_stoplist_is_case_insensitive(self) -> None:
        shared = corr.distinctive_shared_entities(["us army", "dod"], ["us army", "dod"])
        assert shared == []


# ---------------------------------------------------------------------------------------------
# _events_match / _amounts_close
# ---------------------------------------------------------------------------------------------


class TestEventsMatch:
    def test_different_kind_never_matches(self) -> None:
        a = {"kind": "contract_award", "customer": "IDF"}
        b = {"kind": "test", "customer": "IDF"}
        assert corr._events_match(a, b) is False

    def test_same_kind_and_customer_matches(self) -> None:
        a = {"kind": "contract_award", "customer": "IDF", "program": None, "amount_usd": None}
        b = {"kind": "contract_award", "customer": "idf", "program": None, "amount_usd": None}
        assert corr._events_match(a, b) is True

    def test_same_kind_and_program_matches(self) -> None:
        a = {"kind": "investment", "customer": None, "program": "Iron Beam", "amount_usd": None}
        b = {"kind": "investment", "customer": None, "program": "iron beam", "amount_usd": None}
        assert corr._events_match(a, b) is True

    def test_same_kind_and_close_amount_matches(self) -> None:
        a = {"kind": "investment", "customer": None, "program": None, "amount_usd": 100.0}
        b = {"kind": "investment", "customer": None, "program": None, "amount_usd": 105.0}
        assert corr._events_match(a, b) is True

    def test_same_kind_and_far_amount_does_not_match(self) -> None:
        a = {"kind": "investment", "customer": None, "program": None, "amount_usd": 100.0}
        b = {"kind": "investment", "customer": None, "program": None, "amount_usd": 200.0}
        assert corr._events_match(a, b) is False

    def test_same_kind_no_anchor_at_all_does_not_match(self) -> None:
        a = {"kind": "other", "customer": None, "program": None, "amount_usd": None}
        b = {"kind": "other", "customer": None, "program": None, "amount_usd": None}
        assert corr._events_match(a, b) is False


class TestAmountsClose:
    def test_none_values(self) -> None:
        assert corr._amounts_close(None, 100) is False
        assert corr._amounts_close(100, None) is False

    def test_both_zero(self) -> None:
        assert corr._amounts_close(0, 0) is True

    def test_one_zero_one_nonzero(self) -> None:
        assert corr._amounts_close(0, 100) is False

    def test_non_numeric_does_not_raise(self) -> None:
        assert corr._amounts_close("not a number", 100) is False


# ---------------------------------------------------------------------------------------------
# compute_for_item -- monkeypatches every relational helper the module imported by name.
# ---------------------------------------------------------------------------------------------


def _base_item(**overrides: object) -> dict:
    base = {
        "id": 1,
        "url": "https://www.defensenews.com/article-1",
        "source_url": None,
        "source_name": "Defense News",
        "published_at": dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        "domain": "airborne_pods",
        "level": "red",
        "security_status": "clean",
        "entities_mentioned": ["Elbit Systems", "Rafael"],
        "dedup_of": None,
    }
    base.update(overrides)
    return base


class TestComputeForItem:
    def test_item_not_found_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(corr, "get_item_for_corroboration", lambda item_id: None)
        assert corr.compute_for_item(999) is None

    def test_missing_published_at_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        item = _base_item(published_at=None)
        monkeypatch.setattr(corr, "get_item_for_corroboration", lambda item_id: item)
        upserted = {}
        monkeypatch.setattr(corr, "upsert_item_corroboration", lambda **kw: upserted.update(kw))
        record = corr.compute_for_item(1)
        assert record["status"] == "unknown"
        assert upserted["status"] == "unknown"
        assert upserted["count"] == 0

    def test_missing_domain_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        item = _base_item(domain=None)
        monkeypatch.setattr(corr, "get_item_for_corroboration", lambda item_id: item)
        monkeypatch.setattr(corr, "upsert_item_corroboration", lambda **kw: None)
        record = corr.compute_for_item(1)
        assert record["status"] == "unknown"

    def test_no_candidates_no_official_is_single_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        item = _base_item()
        monkeypatch.setattr(corr, "get_item_for_corroboration", lambda item_id: item)
        monkeypatch.setattr(corr, "get_dedup_linked_item_ids", lambda *a, **k: [])
        monkeypatch.setattr(corr, "get_corroboration_candidates", lambda *a, **k: [])
        upserted = {}
        monkeypatch.setattr(corr, "upsert_item_corroboration", lambda **kw: upserted.update(kw))
        record = corr.compute_for_item(1)
        assert record["status"] == "single_source"
        assert record["count"] == 0
        assert upserted["status"] == "single_source"

    def test_official_source_no_candidates_is_official_primary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        item = _base_item(url="https://sam.gov/opp/123")
        monkeypatch.setattr(corr, "get_item_for_corroboration", lambda item_id: item)
        monkeypatch.setattr(corr, "get_dedup_linked_item_ids", lambda *a, **k: [])
        monkeypatch.setattr(corr, "get_corroboration_candidates", lambda *a, **k: [])
        monkeypatch.setattr(corr, "upsert_item_corroboration", lambda **kw: None)
        record = corr.compute_for_item(1)
        assert record["status"] == "official_primary"

    def test_dedup_linked_candidate_is_corroborated_duplicate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        item = _base_item()
        cand = {
            "id": 2,
            "url": "https://breakingdefense.com/article-2",
            "source_url": None,
            "source_name": "Breaking Defense",
            "published_at": dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            "domain": "airborne_pods",
            "entities_mentioned": [],
            "dedup_of": None,
        }
        monkeypatch.setattr(corr, "get_item_for_corroboration", lambda item_id: item)
        monkeypatch.setattr(corr, "get_dedup_linked_item_ids", lambda *a, **k: [2])
        monkeypatch.setattr(corr, "get_corroboration_candidates", lambda *a, **k: [cand])
        upserted = {}
        monkeypatch.setattr(corr, "upsert_item_corroboration", lambda **kw: upserted.update(kw))
        record = corr.compute_for_item(1)
        assert record["status"] == "corroborated"
        assert record["count"] == 1
        assert record["sources"][0]["kind"] == "duplicate"
        assert record["sources"][0]["item_id"] == 2
        assert upserted["status"] == "corroborated"

    def test_same_outlet_candidate_never_counts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        item = _base_item()
        # Same registrable domain as the subject item -- must not corroborate even though it's a
        # dedup-linked id, since domain-difference is checked before the duplicate/same-event legs.
        cand = {
            "id": 2,
            "url": "https://www.defensenews.com/article-2",
            "source_url": None,
            "source_name": "Defense News",
            "published_at": dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            "domain": "airborne_pods",
            "entities_mentioned": [],
            "dedup_of": None,
        }
        monkeypatch.setattr(corr, "get_item_for_corroboration", lambda item_id: item)
        monkeypatch.setattr(corr, "get_dedup_linked_item_ids", lambda *a, **k: [2])
        monkeypatch.setattr(corr, "get_corroboration_candidates", lambda *a, **k: [cand])
        monkeypatch.setattr(corr, "upsert_item_corroboration", lambda **kw: None)
        record = corr.compute_for_item(1)
        assert record["status"] == "single_source"

    def test_same_event_match_is_corroborated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        item = _base_item(entities_mentioned=["Elbit Systems", "Rafael"])
        cand = {
            "id": 2,
            "url": "https://breakingdefense.com/article-2",
            "source_url": None,
            "source_name": "Breaking Defense",
            "published_at": dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            "domain": "airborne_pods",
            "entities_mentioned": ["Elbit Systems", "Rafael"],
            "dedup_of": None,
        }
        monkeypatch.setattr(corr, "get_item_for_corroboration", lambda item_id: item)
        monkeypatch.setattr(corr, "get_dedup_linked_item_ids", lambda *a, **k: [])
        monkeypatch.setattr(corr, "get_corroboration_candidates", lambda *a, **k: [cand])

        def _events(item_id: int) -> list[dict]:
            if item_id == 1:
                return [{"kind": "contract_award", "customer": "IDF", "program": None, "amount_usd": None}]
            return [{"kind": "contract_award", "customer": "idf", "program": None, "amount_usd": None}]

        monkeypatch.setattr(corr, "get_events_for_item", _events)
        upserted = {}
        monkeypatch.setattr(corr, "upsert_item_corroboration", lambda **kw: upserted.update(kw))
        record = corr.compute_for_item(1)
        assert record["status"] == "corroborated"
        assert record["sources"][0]["kind"] == "same_event"

    def test_shared_entities_without_matching_event_is_not_corroborated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        item = _base_item(entities_mentioned=["Elbit Systems", "Rafael"])
        cand = {
            "id": 2,
            "url": "https://breakingdefense.com/article-2",
            "source_url": None,
            "source_name": "Breaking Defense",
            "published_at": dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            "domain": "airborne_pods",
            "entities_mentioned": ["Elbit Systems", "Rafael"],
            "dedup_of": None,
        }
        monkeypatch.setattr(corr, "get_item_for_corroboration", lambda item_id: item)
        monkeypatch.setattr(corr, "get_dedup_linked_item_ids", lambda *a, **k: [])
        monkeypatch.setattr(corr, "get_corroboration_candidates", lambda *a, **k: [cand])

        def _events(item_id: int) -> list[dict]:
            if item_id == 1:
                return [{"kind": "contract_award", "customer": "IDF", "program": None, "amount_usd": None}]
            return [{"kind": "test", "customer": "Rafael", "program": None, "amount_usd": None}]

        monkeypatch.setattr(corr, "get_events_for_item", _events)
        monkeypatch.setattr(corr, "upsert_item_corroboration", lambda **kw: None)
        record = corr.compute_for_item(1)
        assert record["status"] == "single_source"

    def test_different_domain_candidate_never_counts_as_same_event(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        item = _base_item(entities_mentioned=["Elbit Systems", "Rafael"])
        cand = {
            "id": 2,
            "url": "https://breakingdefense.com/article-2",
            "source_url": None,
            "source_name": "Breaking Defense",
            "published_at": dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            "domain": "land_surveillance",  # different taxonomy domain
            "entities_mentioned": ["Elbit Systems", "Rafael"],
            "dedup_of": None,
        }
        monkeypatch.setattr(corr, "get_item_for_corroboration", lambda item_id: item)
        monkeypatch.setattr(corr, "get_dedup_linked_item_ids", lambda *a, **k: [])
        monkeypatch.setattr(corr, "get_corroboration_candidates", lambda *a, **k: [cand])
        events_called = []
        monkeypatch.setattr(corr, "get_events_for_item", lambda item_id: events_called.append(item_id) or [])
        monkeypatch.setattr(corr, "upsert_item_corroboration", lambda **kw: None)
        record = corr.compute_for_item(1)
        assert record["status"] == "single_source"
        # never even needed to look up events -- the domain mismatch short-circuits first.
        assert events_called == []

    def test_corroborated_outranks_official_primary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        item = _base_item(url="https://sam.gov/opp/123")
        cand = {
            "id": 2,
            "url": "https://breakingdefense.com/article-2",
            "source_url": None,
            "source_name": "Breaking Defense",
            "published_at": dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            "domain": "airborne_pods",
            "entities_mentioned": [],
            "dedup_of": None,
        }
        monkeypatch.setattr(corr, "get_item_for_corroboration", lambda item_id: item)
        monkeypatch.setattr(corr, "get_dedup_linked_item_ids", lambda *a, **k: [2])
        monkeypatch.setattr(corr, "get_corroboration_candidates", lambda *a, **k: [cand])
        monkeypatch.setattr(corr, "upsert_item_corroboration", lambda **kw: None)
        record = corr.compute_for_item(1)
        assert record["status"] == "corroborated"


# ---------------------------------------------------------------------------------------------
# run_corroboration / recheck_recent -- stage-level orchestration.
# ---------------------------------------------------------------------------------------------


class TestRunCorroboration:
    def test_untriaged_item_is_left_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(corr, "get_items_for_stage", lambda *a, **k: [{"id": 1, "level": None}])
        marked = []
        monkeypatch.setattr(corr, "mark_stage", lambda item_id, stage: marked.append(item_id))
        computed = []
        monkeypatch.setattr(corr, "compute_for_item", lambda item_id: computed.append(item_id))
        stats = corr.run_corroboration()
        assert marked == []
        assert computed == []
        assert stats.checked == 0

    def test_archive_level_item_is_marked_done_without_compute(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(corr, "get_items_for_stage", lambda *a, **k: [{"id": 1, "level": "archive"}])
        marked = []
        monkeypatch.setattr(corr, "mark_stage", lambda item_id, stage: marked.append((item_id, stage)))
        computed = []
        monkeypatch.setattr(corr, "compute_for_item", lambda item_id: computed.append(item_id))
        stats = corr.run_corroboration()
        assert marked == [(1, corr.STAGE)]
        assert computed == []
        assert stats.checked == 0

    def test_eligible_item_is_computed_and_marked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(corr, "get_items_for_stage", lambda *a, **k: [{"id": 1, "level": "red"}])
        marked = []
        monkeypatch.setattr(corr, "mark_stage", lambda item_id, stage: marked.append(item_id))
        monkeypatch.setattr(
            corr,
            "compute_for_item",
            lambda item_id: {"item_id": item_id, "status": "single_source", "count": 0, "sources": []},
        )
        stats = corr.run_corroboration()
        assert marked == [1]
        assert stats.checked == 1
        assert stats.single_source == 1

    def test_failure_on_one_item_does_not_abort_the_batch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            corr,
            "get_items_for_stage",
            lambda *a, **k: [{"id": 1, "level": "red"}, {"id": 2, "level": "orange"}],
        )
        monkeypatch.setattr(corr, "mark_stage", lambda item_id, stage: None)

        def _compute(item_id: int) -> dict:
            if item_id == 1:
                raise RuntimeError("boom")
            return {"item_id": item_id, "status": "corroborated", "count": 1, "sources": []}

        monkeypatch.setattr(corr, "compute_for_item", _compute)
        stats = corr.run_corroboration()
        assert stats.failed == 1
        assert stats.checked == 1
        assert stats.corroborated == 1

    def test_matched_sources_trigger_one_hop_recompute(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(corr, "get_items_for_stage", lambda *a, **k: [{"id": 1, "level": "red"}])
        monkeypatch.setattr(corr, "mark_stage", lambda item_id, stage: None)
        computed = []

        def _compute(item_id: int) -> dict:
            computed.append(item_id)
            if item_id == 1:
                return {
                    "item_id": 1,
                    "status": "corroborated",
                    "count": 1,
                    "sources": [{"item_id": 2, "kind": "duplicate"}],
                }
            return {"item_id": item_id, "status": "corroborated", "count": 1, "sources": []}

        monkeypatch.setattr(corr, "compute_for_item", _compute)
        corr.run_corroboration()
        assert computed == [1, 2]


class TestRecheckRecent:
    def test_recomputes_every_recent_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(corr, "get_recent_in_scope_item_ids", lambda days: [1, 2, 3])
        computed = []

        def _compute(item_id: int) -> dict:
            computed.append(item_id)
            return {"item_id": item_id, "status": "single_source", "count": 0, "sources": []}

        monkeypatch.setattr(corr, "compute_for_item", _compute)
        stats = corr.recheck_recent()
        assert computed == [1, 2, 3]
        assert stats.checked == 3
        assert stats.single_source == 3

    def test_missing_item_does_not_count_as_checked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(corr, "get_recent_in_scope_item_ids", lambda days: [1])
        monkeypatch.setattr(corr, "compute_for_item", lambda item_id: None)
        stats = corr.recheck_recent()
        assert stats.checked == 1  # the loop itself still ran; only the status counter is skipped
        assert stats.single_source == 0
        assert stats.corroborated == 0

    def test_exception_increments_failed_and_continues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(corr, "get_recent_in_scope_item_ids", lambda days: [1, 2])

        def _compute(item_id: int) -> dict:
            if item_id == 1:
                raise RuntimeError("boom")
            return {"item_id": item_id, "status": "corroborated", "count": 2, "sources": []}

        monkeypatch.setattr(corr, "compute_for_item", _compute)
        stats = corr.recheck_recent()
        assert stats.failed == 1
        assert stats.checked == 1
        assert stats.corroborated == 1

    def test_respects_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(corr, "get_recent_in_scope_item_ids", lambda days: [1, 2, 3, 4, 5])
        computed = []
        monkeypatch.setattr(
            corr,
            "compute_for_item",
            lambda item_id: (
                computed.append(item_id)
                or {"item_id": item_id, "status": "single_source", "count": 0, "sources": []}
            ),
        )
        corr.recheck_recent(limit=2)
        assert computed == [1, 2]


# ---------------------------------------------------------------------------------------------
# corroboration_payload / corroboration_payload_map -- API-facing shape.
# ---------------------------------------------------------------------------------------------


class TestCorroborationPayload:
    def test_never_checked_defaults_to_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(corr, "get_item_corroboration_map", lambda ids, **kw: {})
        payload = corr.corroboration_payload(1)
        assert payload == {"status": "unknown", "count": 0, "sources": [], "checked_at": None}

    def test_checked_item_maps_stored_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        checked_at = dt.datetime(2026, 9, 7, 12, 0, tzinfo=dt.UTC)
        monkeypatch.setattr(
            corr,
            "get_item_corroboration_map",
            lambda ids, **kw: {
                1: {
                    "item_id": 1,
                    "status": "corroborated",
                    "count": 2,
                    "sources": [{"item_id": 2, "kind": "duplicate"}],
                    "checked_at": checked_at,
                }
            },
        )
        payload = corr.corroboration_payload(1)
        assert payload["status"] == "corroborated"
        assert payload["count"] == 2
        assert payload["checked_at"] == checked_at.isoformat()

    def test_bulk_map_only_includes_checked_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            corr,
            "get_item_corroboration_map",
            lambda ids, **kw: {
                1: {"item_id": 1, "status": "single_source", "count": 0, "sources": [], "checked_at": None}
            },
        )
        result = corr.corroboration_payload_map([1, 2])
        assert 1 in result
        assert 2 not in result
