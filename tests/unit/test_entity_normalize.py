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

    @pytest.mark.parametrize(
        "name",
        [
            # Q3-13 r4: singular/unlisted technique-suffix phrases, observed live 2026-09-06.
            "vehicle detection",
            "camouflaged military vehicle detection",
            "infrared vehicle detection",
            "image classification",
            "binary wildfire segmentation",
            "feature-level multimodal fusion",
            # GenAI/LLM/deep-learning prefix phrases.
            "GenAI image editing",
            "LLM-based summarization",
            "deep learning object recognition",
            # lowercase-start, no-proper-noun-token descriptive phrases.
            "potential suppliers",
            "target behaviors",
            "proxy-guided placement",
            "transformer-based architectures",
            "existing C-UAS approaches",
            "future uncrewed ground vehicles (UGVs)",
            "targeted grayscale patch attacks",
            "binary visual question answering",
        ],
    )
    def test_positive_r4_broadened_patterns(self, name: str) -> None:
        assert en.is_technique_like(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            # Real watchlist/curated-org/country/system names that must survive the broadened
            # patterns (Q3-13 r4 regression -- these must never be rejected).
            "Iron Beam",
            "Drone Dome",
            "Sniper ATP",
            "LITENING",
            "US Air Force",
            "Israel",
            "LOCUST",
            # Brand-styled lowercase names that must survive the lowercase-multiword heuristic.
            "ePlane",
            "ePlane Company",
            "e200X",
            "exMHR",
        ],
    )
    def test_negative_r4_real_entities_survive(self, name: str) -> None:
        assert en.is_technique_like(name) is False


class TestIsSourceLikeName:
    @pytest.mark.parametrize(
        "name",
        ["arXiv", "arxiv", "arXiv cs.CV", "IEEE", "SPIE", "Nature", "Reddit", "Wikipedia", "YouTube", "Google Scholar"],
    )
    def test_positive(self, name: str) -> None:
        assert en.is_source_like_name(name) is True

    @pytest.mark.parametrize("name", ["Elbit", "IAI", "Israel", ""])
    def test_negative(self, name: str) -> None:
        assert en.is_source_like_name(name) is False


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
        """ "IAI" (an alias-free watchlist company via `ELTA`) must not match inside an unrelated
        longer word."""
        assert "IAI" not in en.find_watchlist_aliases_in_text("This is trIAIl text with no real hit.")

    def test_finds_curated_org_via_hebrew_alias(self) -> None:
        """Q3-13 r3: the curated defense-org table (US Army/Navy/IDF/NATO/...) is also searched,
        not just the watchlist."""
        assert en.find_watchlist_aliases_in_text('צבא ארה"ב חתם על חוזה.') == ["US Army"]


class TestResolveCountryName:
    @pytest.mark.parametrize(
        ("surface", "expected"),
        [
            ("Iran", "Iran"),
            ("איראן", "Iran"),
            ("United States", "United States"),
            ('ארה"ב', "United States"),
            ("ארצות הברית", "United States"),
            ("Greece", "Greece"),
            ("יוון", "Greece"),
            ("Japan", "Japan"),
            ("יפן", "Japan"),
        ],
    )
    def test_resolves_known_country(self, surface: str, expected: str) -> None:
        assert en.resolve_country_name(surface) == expected

    def test_unknown_name_returns_none(self) -> None:
        assert en.resolve_country_name("Elbit") is None
        assert en.resolve_country_name("") is None


class TestResolveCompanyCountry:
    def test_resolves_known_non_watchlist_company(self) -> None:
        assert en.resolve_company_country("Rolls-Royce") == "UK"
        assert en.resolve_company_country("רולס-רויס") == "UK"
        assert en.resolve_company_country("Baykar") == "TR"

    def test_unknown_company_returns_none(self) -> None:
        assert en.resolve_company_country("Totally Unknown Widgets Inc") is None


class TestIsGenericNonEntity:
    @pytest.mark.parametrize(
        "name",
        [
            "השוק הביטחוני",
            "תעשייה",
            "סטארט-אפים",
            "לקוחות בינלאומיים",
            "תמונות תרמיות",
            "מפעילים בשטח",
            "איומים בקבוצת משקל 3",
            "מלחמת איראן-עיראק",
            "מצר הורמוז",
            "יפן, דנמרק, גרמניה",
        ],
    )
    def test_positive(self, name: str) -> None:
        assert en.is_generic_non_entity(name) is True

    @pytest.mark.parametrize("name", ["Elbit", "IAI", "LOCUST", "US Army", "Iran", 'צבא ארה"ב'])
    def test_negative_known_entities_never_rejected(self, name: str) -> None:
        assert en.is_generic_non_entity(name) is False

    def test_empty_string_is_generic(self) -> None:
        assert en.is_generic_non_entity("") is True


class TestIsJunkEntity:
    def test_technique_like_is_junk(self) -> None:
        assert en.is_junk_entity("image captioning") is True

    def test_generic_non_entity_is_junk(self) -> None:
        assert en.is_junk_entity("השוק הביטחוני") is True

    def test_source_like_is_junk(self) -> None:
        assert en.is_junk_entity("arXiv") is True

    def test_real_entity_not_junk(self) -> None:
        assert en.is_junk_entity("Elbit") is False
        assert en.is_junk_entity('צבא ארה"ב') is False


class TestCuratedOrgResolution:
    def test_hebrew_variants_resolve_to_same_canonical_org(self) -> None:
        rec1 = en.resolve_canonical('צבא ארה"ב')
        rec2 = en.resolve_canonical("צבא ארצות הברית")
        rec3 = en.resolve_canonical("US Army")
        assert rec1 and rec2 and rec3
        assert rec1["name"] == rec2["name"] == rec3["name"] == "US Army"
        assert rec1["kind"] == "org"
        assert rec1["country"] == "US"

    def test_navy_hebrew_variants_resolve_together(self) -> None:
        rec1 = en.resolve_canonical("הצי האמריקאי")
        rec2 = en.resolve_canonical("חיל הים האמריקאי")
        assert rec1 and rec2
        assert rec1["name"] == rec2["name"] == "US Navy"


class TestNormalizeKindCountryOverride:
    def test_country_typed_as_company_fixed(self) -> None:
        assert en.normalize_kind("איראן", "company") == "country"
        assert en.normalize_kind("ארצות הברית", "company") == "country"
        assert en.normalize_kind("יוון", "company") == "country"

    def test_curated_org_kind(self) -> None:
        assert en.normalize_kind('צבא ארה"ב', "company") == "org"


class TestCanonicalNameAndKindCountryAndOrg:
    def test_country_name_canonicalized_to_english(self) -> None:
        name, kind = en.canonical_name_and_kind("יפן", "company")
        assert name == "Japan"
        assert kind == "country"

    def test_curated_org_alias_canonicalized(self) -> None:
        name, kind = en.canonical_name_and_kind('צבא ארה"ב', "company")
        assert name == "US Army"
        assert kind == "org"


class TestPersonTransliterationDedup:
    """Round-2 (2026-09-06, judge D3 item 3): the same person's name across a Hebrew<->English
    transliteration (item 81's Anduril-Israel appointee, four spellings) must be recognised as a
    match by :func:`en.is_likely_same_person`, without falsely matching unrelated names."""

    NORKIN_SPELLINGS = ("Amikam Norkin", "Amiram Norkin", "עמירם נורקין", "אמירם נורקין")

    @pytest.mark.parametrize("a", NORKIN_SPELLINGS)
    @pytest.mark.parametrize("b", NORKIN_SPELLINGS)
    def test_all_norkin_spellings_match_each_other(self, a: str, b: str) -> None:
        assert en.is_likely_same_person(a, b) is True

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("Amikam Norkin", "John Smith"),
            ("Elbit", "Rafael"),
            ("Amikam Norkin", "Elbit Systems"),
        ],
    )
    def test_unrelated_names_do_not_match(self, a: str, b: str) -> None:
        assert en.is_likely_same_person(a, b) is False

    @pytest.mark.parametrize("name", ["Amikam Norkin", "עמירם נורקין", "Greg Malandrino"])
    def test_looks_like_person_name_positive(self, name: str) -> None:
        assert en.looks_like_person_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "Elbit Systems",  # company-suffix word
            "US Army",  # curated org
            "Israel",  # country
            "Elbit",  # watchlist company
            "Anduril Industries",  # company-like word ("industries")
            "IAI",  # single word, not 2-4 words
        ],
    )
    def test_looks_like_person_name_negative(self, name: str) -> None:
        assert en.looks_like_person_name(name) is False
