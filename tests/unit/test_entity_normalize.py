"""Unit tests for Q3-13 (docs/qa/findings_Q3_r1.md): ``eoa.pipeline.entity_normalize`` and the
``eoa.memory.relational.upsert_entity`` integration built on top of it.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_entity_normalize.py -q``
"""

from __future__ import annotations

import sys
import types

import pytest

if "eoa.db" not in sys.modules:
    try:
        import eoa.db  # noqa: F401
    except ImportError:
        fake_db = types.ModuleType("eoa.db")
        fake_db.connection = lambda: None  # type: ignore[attr-defined]
        fake_db.get_pool = lambda: None  # type: ignore[attr-defined]
        sys.modules["eoa.db"] = fake_db

from eoa.pipeline import entity_normalize as en


class TestNormalizeNameKey:
    def test_case_insensitive(self) -> None:
        assert en.normalize_name_key("Elbit Systems") == en.normalize_name_key("elbit systems")

    def test_punctuation_stripped(self) -> None:
        assert en.normalize_name_key("Elbit-Systems, Inc.") == en.normalize_name_key("Elbit Systems Inc")

    def test_whitespace_collapsed(self) -> None:
        assert en.normalize_name_key("Elbit   Systems") == en.normalize_name_key("Elbit Systems")

    def test_empty_string(self) -> None:
        assert en.normalize_name_key("") == ""


class TestResolveCanonical:
    def test_resolves_canonical_name(self) -> None:
        rec = en.resolve_canonical("Elbit")
        assert rec is not None
        assert rec["name"] == "Elbit"
        assert rec["kind"] == "company"
        assert rec["country"] == "IL"

    def test_resolves_via_english_alias(self) -> None:
        rec = en.resolve_canonical("Elbit Systems")
        assert rec is not None
        assert rec["name"] == "Elbit"

    def test_resolves_via_hebrew_alias(self) -> None:
        rec = en.resolve_canonical("אלביט מערכות")
        assert rec is not None
        assert rec["name"] == "Elbit"

    def test_resolves_via_program_alias(self) -> None:
        rec = en.resolve_canonical("Iron Beam")
        assert rec is not None
        assert rec["name"] == "Iron Beam"
        assert rec["kind"] == "program"

    def test_unknown_name_returns_none(self) -> None:
        assert en.resolve_canonical("Totally Unknown Widgets Inc") is None

    def test_case_and_punctuation_insensitive_alias_match(self) -> None:
        rec = en.resolve_canonical("elbit-systems")
        assert rec is not None
        assert rec["name"] == "Elbit"


class TestIsTechniqueLike:
    @pytest.mark.parametrize(
        "name",
        ["image captioning", "RF-DETR vehicle detectors", "Object Detection", "semantic segmentation"],
    )
    def test_positive(self, name: str) -> None:
        assert en.is_technique_like(name) is True

    @pytest.mark.parametrize("name", ["Elbit Systems", "IAI", "LOCUST", "Iron Beam", ""])
    def test_negative(self, name: str) -> None:
        assert en.is_technique_like(name) is False


class TestNormalizeKind:
    def test_watchlist_company_kind_wins_over_generic_guess(self) -> None:
        assert en.normalize_kind("Elbit Systems", "org") == "company"

    def test_explicit_system_kind_kept_even_for_watchlist_company(self) -> None:
        # a caller who already knows it's a specific system designation is trusted over the
        # watchlist's own company-level record.
        assert en.normalize_kind("Elbit Systems", "system") == "system"

    def test_known_system_designation(self) -> None:
        assert en.normalize_kind("LOCUST", "company") == "system"
        assert en.normalize_kind("SMASH", "company") == "system"

    def test_government_body_maps_to_org(self) -> None:
        assert en.normalize_kind("US Air Force", "company") == "org"
        assert en.normalize_kind("Ministry of Defense", "company") == "org"

    def test_genuine_country_kind_kept(self) -> None:
        """Since migration 0007, the `entities` table's CHECK constraint does allow 'country' --
        a genuine country name keeps that kind."""
        assert en.normalize_kind("Israel", "country") == "country"

    def test_government_body_kind_wins_over_country_label(self) -> None:
        """A government/military body mislabeled "country" by the caller is still an org, not a
        country -- "US Air Force" is not a country."""
        assert en.normalize_kind("US Air Force", "country") == "org"

    def test_unknown_kind_defaults_to_org(self) -> None:
        assert en.normalize_kind("Some New Thing", "widget") == "org"

    def test_valid_kind_kept_when_not_watchlisted(self) -> None:
        assert en.normalize_kind("Acme Corp", "company") == "company"
        assert en.normalize_kind("John Smith", "person") == "person"


class TestCanonicalNameAndKind:
    def test_alias_resolves_to_canonical_name_and_kind(self) -> None:
        name, kind = en.canonical_name_and_kind("Elbit Systems", "company")
        assert name == "Elbit"
        assert kind == "company"

    def test_non_watchlisted_name_unchanged(self) -> None:
        name, kind = en.canonical_name_and_kind("Acme Corp", "company")
        assert name == "Acme Corp"
        assert kind == "company"


class TestFindWatchlistAliasesInText:
    def test_finds_canonical_name_directly(self) -> None:
        assert en.find_watchlist_aliases_in_text("Elbit announced a new contract.") == ["Elbit"]

    def test_finds_via_alias(self) -> None:
        assert en.find_watchlist_aliases_in_text("Elbit Systems UK signed a deal.") == ["Elbit"]

    def test_finds_via_hebrew_alias(self) -> None:
        assert en.find_watchlist_aliases_in_text("אלביט מערכות זכתה בחוזה.") == ["Elbit"]

    def test_no_match_returns_empty(self) -> None:
        assert en.find_watchlist_aliases_in_text("A completely unrelated sentence.") == []

    def test_empty_text(self) -> None:
        assert en.find_watchlist_aliases_in_text("") == []

    def test_deduplicates_multiple_alias_hits_for_same_company(self) -> None:
        text = "Elbit Systems and Elbit both refer to the same company."
        assert en.find_watchlist_aliases_in_text(text) == ["Elbit"]

    def test_word_boundary_avoids_partial_match(self) -> None:
        """"IAI" (an alias-free watchlist company via `ELTA`) must not match inside an unrelated
        longer word."""
        assert "IAI" not in en.find_watchlist_aliases_in_text("This is trIAIl text with no real hit.")
