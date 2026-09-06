"""Unit tests for Round-4b W21/W24 (docs/REVIEW_2026-09-06_evening.md): taxonomy terminology
review and the new `directed_energy` (HEL/DEW) domain, plus the watchlist/sources/tenders config
that back it.

Pure config/YAML checks -- no DB, no Ollama, no network (CONVENTIONS.md rule 10).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_taxonomy_round4b.py -q``
"""

from __future__ import annotations

import pytest
import yaml

from eoa.config import CONFIG_DIR
from eoa.fetch.sources_loader import load_sources


@pytest.fixture(scope="module")
def taxonomy() -> dict:
    with (CONFIG_DIR / "taxonomy.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def watchlist() -> dict:
    with (CONFIG_DIR / "watchlist.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def tenders_config() -> dict:
    with (CONFIG_DIR / "tenders.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class TestW21LabelRenames:
    """W21: professional-terminology fixes -- keys unchanged, only label text/wording."""

    def test_targeting_pods_key_unchanged(self, taxonomy: dict) -> None:
        """The DB/classification key must survive the rename untouched."""
        assert "targeting_pods" in taxonomy["domains"]["airborne_pods"]["sub"]

    def test_targeting_pods_label_no_longer_says_old_phrase(self, taxonomy: dict) -> None:
        label = taxonomy["domains"]["airborne_pods"]["sub"]["targeting_pods"]
        assert "פודי כיוון" not in label
        assert "פודי ציון מטרות" in label
        assert "Targeting Pod" in label

    def test_vehicle_sights_key_unchanged(self, taxonomy: dict) -> None:
        assert "vehicle_sights" in taxonomy["domains"]["land_surveillance"]["sub"]

    def test_vehicle_sights_label_uses_koveinet_not_matad(self, taxonomy: dict) -> None:
        """'מטע"ד מפקד/תותחן' was imprecise (מטע"ד = a standalone payload/pod); the fixed AFV
        commander's/gunner's sighting device has its own term, 'כוונת'."""
        label = taxonomy["domains"]["land_surveillance"]["sub"]["vehicle_sights"]
        assert "כוונ" in label  # "כוונת"/"כוונות" (sight/sights)
        assert "Commander/Gunner Sights" in label

    def test_all_domain_and_subdomain_labels_are_nonempty_strings(self, taxonomy: dict) -> None:
        for domain_key, domain in taxonomy["domains"].items():
            assert isinstance(domain.get("label"), str) and domain["label"].strip(), domain_key
            for sub_key, sub_label in (domain.get("sub") or {}).items():
                assert isinstance(sub_label, str) and sub_label.strip(), f"{domain_key}.{sub_key}"

    def test_matad_terminology_still_used_where_correct(self, taxonomy: dict) -> None:
        """מטע"ד (מטען ייעודי, "designated/mission payload") is the correct term for pods/gimbals
        -- the review only asked to fix its *misuse* for a fixed AFV sight, not to remove it
        everywhere. Confirms it is still present on labels where it is legitimate."""
        airborne_label = taxonomy["domains"]["airborne_pods"]["label"]
        assert 'מטע"ד' in airborne_label


class TestW21ConsistencyFixes:
    """Labels normalized to this taxonomy's own "עברית (English)" convention."""

    def test_edge_ai_label_leads_with_hebrew(self, taxonomy: dict) -> None:
        label = taxonomy["domains"]["computer_vision"]["sub"]["edge_ai"]
        assert label.startswith("בינה מלאכותית")
        assert "(Edge AI)" in label

    def test_gps_denied_nav_label_is_hebraized(self, taxonomy: dict) -> None:
        label = taxonomy["domains"]["computer_vision"]["sub"]["gps_denied_nav"]
        assert "GPS-Denied" not in label.split("(")[0]  # no raw English adjective in the Hebrew part
        assert "GPS-Denied Navigation" in label

    def test_computational_optics_label_moves_english_into_parens(self, taxonomy: dict) -> None:
        label = taxonomy["domains"]["secondary"]["sub"]["computational_optics"]
        hebrew_part = label.split("(")[0]
        assert "Gimbal Stabilization" not in hebrew_part
        assert "Gimbal Stabilization" in label


class TestW24DirectedEnergyDomain:
    """New `directed_energy` domain -- existing keys (esp. `air_defense.hel`) kept unchanged."""

    def test_directed_energy_domain_exists(self, taxonomy: dict) -> None:
        assert "directed_energy" in taxonomy["domains"]

    def test_air_defense_hel_subdomain_unchanged_key(self, taxonomy: dict) -> None:
        """The existing HEL-as-interceptor subdomain must survive untouched (task: "keep existing
        keys")."""
        sub = taxonomy["domains"]["air_defense"]["sub"]
        assert "hel" in sub
        assert "High-Energy Laser" in sub["hel"]

    def test_directed_energy_covers_required_subtopics(self, taxonomy: dict) -> None:
        sub = taxonomy["domains"]["directed_energy"]["sub"]
        all_labels = " ".join(sub.values())
        for required_term in (
            "HEL Weapon Systems",
            "Laser Sources",
            "Beam Combiners",
            "Beam Directors",
            "Adaptive Optics",
            "Thermal Management",
        ):
            assert required_term in all_labels, required_term

    def test_directed_energy_domain_appears_between_air_defense_and_c_uas(self, taxonomy: dict) -> None:
        """Domain order drives report section order (report_daily.md/report_weekly.md: "לפי סדר
        הופעת התחומים ברשימה") -- confirms the new domain was inserted in a sensible place rather
        than appended at the end, and that dict insertion order survived the YAML round-trip."""
        keys = list(taxonomy["domains"].keys())
        assert keys.index("air_defense") < keys.index("directed_energy") < keys.index("c_uas")

    def test_directed_energy_subdomain_keys_are_stable_slugs(self, taxonomy: dict) -> None:
        for key in taxonomy["domains"]["directed_energy"]["sub"]:
            assert key.isidentifier() and key == key.lower()


class TestW24WatchlistAdditions:
    def _focus_of(self, watchlist: dict, name: str, section: str = "companies") -> list[str]:
        for row in watchlist[section]:
            if row["name"] == name:
                return row.get("focus") or []
        raise AssertionError(f"{name!r} not found in watchlist.{section}")

    @pytest.mark.parametrize(
        "name",
        [
            "RTX",
            "Lockheed Martin",
            "BlueHalo",
            "MBDA",
            "Rheinmetall",
            "Elbit",
            "IAI",
            "Rafael",
            "Hanwha",
            "QinetiQ",
            "Boeing",
            "General Atomics",
            "nLIGHT",
            "Coherent",
            "IPG Photonics",
            "TRUMPF",
        ],
    )
    def test_hel_company_has_directed_energy_focus(self, watchlist: dict, name: str) -> None:
        assert "directed_energy" in self._focus_of(watchlist, name)

    def test_iron_beam_program_has_directed_energy_focus(self, watchlist: dict) -> None:
        assert "directed_energy" in self._focus_of(watchlist, "Iron Beam", section="programs")

    def test_mbda_carries_dragonfire_alias(self, watchlist: dict) -> None:
        mbda = next(row for row in watchlist["companies"] if row["name"] == "MBDA")
        assert "DragonFire" in (mbda.get("aliases") or [])

    def test_ipg_alias_is_strict_to_avoid_acronym_collisions(self, watchlist: dict) -> None:
        """'IPG' alone is a generic-enough acronym that it should require the canonical name to
        co-occur before attribution, per this file's own strict_aliases convention (see its header
        comment on the BlueHalo/AeroVironment LOCUST collision)."""
        row = next(r for r in watchlist["companies"] if r["name"] == "IPG Photonics")
        assert "IPG" in (row.get("strict_aliases") or [])


class TestW24Sources:
    def test_sources_yaml_loads_cleanly(self) -> None:
        sources = load_sources(str(CONFIG_DIR / "sources.yaml"))
        assert len(sources) > 0

    def test_at_least_two_verified_directed_energy_sources(self) -> None:
        sources = load_sources(str(CONFIG_DIR / "sources.yaml"))
        de_verified = [s for s in sources if "directed_energy" in s.tags and s.verified]
        assert len(de_verified) >= 2, [s.id for s in sources if "directed_energy" in s.tags]

    def test_unverified_directed_energy_source_is_disabled(self) -> None:
        """A source checked live and found dead (403/404/etc.) must be `verified: false` (task
        instruction) and, per this registry's existing convention (Q4-2/Q4-3), `enabled: false` so
        it never wastes a fetch cycle."""
        sources = load_sources(str(CONFIG_DIR / "sources.yaml"))
        by_id = {s.id: s for s in sources}
        dead = by_id.get("breaking_defense_dew")
        assert dead is not None
        assert dead.verified is False
        assert dead.enabled is False


class TestW24TendersKeywords:
    def test_hel_keywords_present(self, tenders_config: dict) -> None:
        keywords = tenders_config["keywords"]
        for term in ("high-energy laser", "directed energy", "laser weapon system", "beam director"):
            assert term in keywords, term

    def test_no_duplicate_keywords_introduced(self, tenders_config: dict) -> None:
        keywords = tenders_config["keywords"]
        assert len(keywords) == len(set(keywords))
