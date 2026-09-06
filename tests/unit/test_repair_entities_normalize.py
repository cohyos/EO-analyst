"""Unit tests for scripts/repair_entities_normalize.py's pure grouping logic (Q3-13, docs/qa/
findings_Q3_r1.md). No DB -- only ``_merge_key_and_target``, the function that decides which
entities are merge candidates and what name they should end up under.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_repair_entities_normalize.py -q``
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

if "eoa.db" not in sys.modules:
    try:
        import eoa.db  # noqa: F401
    except ImportError:
        fake_db = types.ModuleType("eoa.db")
        fake_db.connection = lambda: None  # type: ignore[attr-defined]
        fake_db.get_pool = lambda: None  # type: ignore[attr-defined]
        sys.modules["eoa.db"] = fake_db

_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "repair_entities_normalize.py"
_spec = importlib.util.spec_from_file_location("repair_entities_normalize", _SCRIPT_PATH)
ren = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(ren)


class TestMergeKeyAndTarget:
    def test_watchlist_alias_groups_under_canonical_name(self) -> None:
        key1, target1 = ren._merge_key_and_target({"name": "Elbit Systems", "kind": "company"})
        key2, target2 = ren._merge_key_and_target({"name": "אלביט מערכות", "kind": "company"})
        key3, target3 = ren._merge_key_and_target({"name": "Elbit", "kind": "company"})
        assert key1 == key2 == key3
        assert target1 == target2 == target3 == "Elbit"

    def test_system_designation_never_folds_into_owning_company(self) -> None:
        """Regression: LOCUST/TITAN are BlueHalo's own product names listed as watchlist
        aliases, purely for search-relevance matching -- they must NOT be grouped with (and
        merged/renamed into) "BlueHalo" itself."""
        locust_key, locust_target = ren._merge_key_and_target({"name": "LOCUST", "kind": "company"})
        bluehalo_key, _bluehalo_target = ren._merge_key_and_target({"name": "BlueHalo", "kind": "company"})
        assert locust_target is None
        assert locust_key != bluehalo_key

    def test_system_designation_case_variants_still_group_together(self) -> None:
        """Two spellings of the *same* system name (not the company) still merge with each other,
        just not with the company -- normalize_name_key still applies."""
        key1, target1 = ren._merge_key_and_target({"name": "LOCUST X3", "kind": "company"})
        key2, target2 = ren._merge_key_and_target({"name": "Locust X3", "kind": "company"})
        assert key1 == key2
        assert target1 is None and target2 is None

    def test_non_watchlisted_names_group_by_case_insensitive_spelling(self) -> None:
        key1, target1 = ren._merge_key_and_target({"name": "Acme Corp", "kind": "company"})
        key2, target2 = ren._merge_key_and_target({"name": "acme corp", "kind": "company"})
        assert key1 == key2
        assert target1 is None and target2 is None

    def test_unrelated_names_get_different_keys(self) -> None:
        key1, _ = ren._merge_key_and_target({"name": "Elbit", "kind": "company"})
        key2, _ = ren._merge_key_and_target({"name": "IAI", "kind": "company"})
        assert key1 != key2

    def test_country_hebrew_english_variants_group_under_english_name(self) -> None:
        """Q3-13 r3 (docs/qa/findings_Q3_r2.md): "יפן" and "Japan" (or "ארה\"ב"/"ארצות הברית" and
        "United States") are the same country entity, however spelled/language."""
        key1, target1 = ren._merge_key_and_target({"name": "יפן", "kind": "company"})
        key2, target2 = ren._merge_key_and_target({"name": "Japan", "kind": "country"})
        assert key1 == key2
        assert target1 == target2 == "Japan"

    def test_curated_org_hebrew_english_variants_group_together(self) -> None:
        key1, target1 = ren._merge_key_and_target({"name": 'צבא ארה"ב', "kind": "company"})
        key2, target2 = ren._merge_key_and_target({"name": "צבא ארצות הברית", "kind": "company"})
        key3, target3 = ren._merge_key_and_target({"name": "US Army", "kind": "org"})
        assert key1 == key2 == key3
        assert target1 == target2 == target3 == "US Army"


class TestRejectJunk:
    def test_generic_non_entities_rejected(self) -> None:
        entities = [
            {"id": 1, "name": "השוק הביטחוני"},
            {"id": 2, "name": "Elbit"},
            {"id": 3, "name": "image captioning"},
        ]
        report = ren._reject_junk(entities, dry_run=True)
        rejected_ids = {r["id"] for r in report}
        assert rejected_ids == {1, 3}


class TestBackfillCountry:
    def test_watchlist_and_static_map_both_fill_country(self) -> None:
        entities = [
            {"id": 1, "name": "Elbit", "kind": "company", "country": None},
            {"id": 2, "name": "Rolls-Royce", "kind": "company", "country": None},
            {"id": 3, "name": "Acme Corp", "kind": "company", "country": None},
            {"id": 4, "name": "Iran", "kind": "country", "country": None},
        ]
        report = ren._backfill_country(entities, dry_run=True)
        by_id = {r["id"]: r["country"] for r in report}
        assert by_id[1] == "IL"
        assert by_id[2] == "UK"
        assert 3 not in by_id  # unknown company -- nothing to backfill from
        assert 4 not in by_id  # a country-kind row's own `country` column is untouched


class TestMergeDuplicatesReportShape:
    def test_singleton_groups_produce_no_merge_report(self) -> None:
        entities = [
            {"id": 1, "name": "Elbit", "kind": "company", "country": "IL"},
            {"id": 2, "name": "IAI", "kind": "company", "country": "IL"},
        ]
        report = ren._merge_duplicates(entities, dry_run=True)
        assert report == []

    def test_duplicate_group_picks_lowest_id_as_winner(self) -> None:
        entities = [
            {"id": 50, "name": "Elbit Systems", "kind": "company", "country": None},
            {"id": 5, "name": "Elbit", "kind": "company", "country": "IL"},
            {"id": 90, "name": "אלביט מערכות", "kind": "company", "country": None},
        ]
        report = ren._merge_duplicates(entities, dry_run=True)
        assert len(report) == 1
        group = report[0]
        assert group["winner_id"] == 5
        assert group["winner_name"] == "Elbit"
        merged_ids = {m["id"] for m in group["merged"]}
        assert merged_ids == {50, 90}

    def test_system_designation_group_excluded_from_company_merge(self) -> None:
        entities = [
            {"id": 9, "name": "BlueHalo", "kind": "company", "country": "US"},
            {"id": 615, "name": "LOCUST", "kind": "system", "country": "US"},
            {"id": 177, "name": "TITAN", "kind": "system", "country": "US"},
        ]
        report = ren._merge_duplicates(entities, dry_run=True)
        assert report == []  # no group has 2+ members once system designations are excluded
