"""Unit tests for W19b (docs/REVIEW_2026-09-06_evening.md; user requirement 2026-09-06 21:20,
verbatim: "group the manufacturers' products by families and allow drill-down, not flooding the
operator"):

- ``eoa.payloads.models.parse_family_variant`` -- the deterministic family/variant parser,
  exercised against every one of the 62 ``config/payloads_seed.yaml`` canonical names so a future
  edit to the parser can never silently regress a real seed row.
- ``eoa.payloads.models.canonical_vendor`` -- watchlist-alias vendor canonicalisation
  (``eoa.pipeline.entity_normalize.resolve_canonical``).
- ``eoa.payloads.models.build_payload_tree`` -- the vendor -> family -> variant grouping used by
  ``GET /api/payloads/tree`` and the frontend tree view.

No DB/LLM/network calls anywhere in this file -- ``eoa.payloads.models`` is pure text/config
lookups (mirrors ``tests/unit/test_payloads_round3.py``'s existing convention for this module).
"""

from __future__ import annotations

import pytest

from eoa.payloads.models import (
    build_payload_tree,
    canonical_vendor,
    parse_family_variant,
)

# ``(canonical_name, expected_family, expected_variant)`` for every one of the 62 identity rows in
# config/payloads_seed.yaml (round 4b/W19). Kept in the same order as the seed file so a diff
# against that file is easy to eyeball.
SEED_CASES: list[tuple[str, str, str]] = [
    ("WESCAM MX-6", "MX", "MX-6"),
    ("WESCAM MX-8", "MX", "MX-8"),
    ("WESCAM MX-10", "MX", "MX-10"),
    ("WESCAM MX-15", "MX", "MX-15"),
    ("WESCAM MX-15D", "MX", "MX-15D"),
    ("WESCAM MX-20", "MX", "MX-20"),
    ("WESCAM MX-20HD", "MX", "MX-20HD"),
    ("WESCAM MX-25", "MX", "MX-25"),
    ("FLIR Star SAFIRE 380-HD", "Star SAFIRE", "Star SAFIRE 380-HD"),
    ("FLIR Star SAFIRE 260", "Star SAFIRE", "Star SAFIRE 260"),
    ("FLIR Star SAFIRE 230-HD", "Star SAFIRE", "Star SAFIRE 230-HD"),
    ("Controp T-Stamp", "T-Stamp", "T-Stamp"),
    ("Controp DSP-HD", "DSP", "DSP-HD"),
    ("Elbit DCoMPASS", "DCoMPASS", "DCoMPASS"),
    ("Elbit CoMPASS", "CoMPASS", "CoMPASS"),
    ("Elbit AMPS", "AMPS", "AMPS"),
    ("Elbit Micro-CoMPASS", "Micro-CoMPASS", "Micro-CoMPASS"),
    ("Elbit Skylens", "Skylens", "Skylens"),
    ("Rafael Toplite", "Toplite", "Toplite"),
    ("Rafael Litening 5", "Litening", "Litening 5"),
    ("IAI MOSP 3000", "MOSP", "MOSP 3000"),
    ("IAI Mini-POP", "POP", "Mini-POP"),
    ("IAI POP300", "POP", "POP300"),
    ("Safran Euroflir 410", "Euroflir", "Euroflir 410"),
    ("Safran Euroflir 350", "Euroflir", "Euroflir 350"),
    ("Safran Euroflir 610", "Euroflir", "Euroflir 610"),
    ("Hensoldt Argos-II", "Argos", "Argos-II"),
    ("Hensoldt Nightowl", "Nightowl", "Nightowl"),
    ("Leonardo LEOSS", "LEOSS", "LEOSS"),
    ("Leonardo Gabbiano", "Gabbiano", "Gabbiano"),
    ("Leonardo SLX", "SLX", "SLX"),
    ("Aselsan ASELFLIR-500", "ASELFLIR", "ASELFLIR-500"),
    ("Aselsan ASELFLIR-400", "ASELFLIR", "ASELFLIR-400"),
    ("Aselsan CATS", "CATS", "CATS"),
    ("Teledyne FLIR Boson", "Boson", "Boson"),
    ("Teledyne FLIR Neutrino", "Neutrino", "Neutrino"),
    ("Teledyne FLIR Tau 2", "Tau", "Tau 2"),
    ("Teledyne FLIR Hadron", "Hadron", "Hadron"),
    ("Opgal Sii", "Sii", "Sii"),
    ("Opgal Zvit", "Zvit", "Zvit"),
    ("PVP Thermal Core", "Thermal Core", "Thermal Core"),
    ("Thales Talios", "Talios", "Talios"),
    ("Thales Gecko", "Gecko", "Gecko"),
    ("Lockheed Martin Sniper ATP", "Sniper", "Sniper ATP"),
    ("Northrop Grumman LITENING", "LITENING", "LITENING"),
    ("Raytheon MTS-A", "MTS", "MTS-A"),
    ("Raytheon MTS-B", "MTS", "MTS-B"),
    ("Raytheon MTS-C", "MTS", "MTS-C"),
    ("Collins Aerospace DB-110", "DB", "DB-110"),
    ("Trakka TC-300", "TC", "TC-300"),
    ("TrakkaCam TC-215", "TC", "TC-215"),
    ("NextVision Raptor", "Raptor", "Raptor"),
    ("NextVision Colibri", "Colibri", "Colibri"),
    ("NextVision DragonEye", "DragonEye", "DragonEye"),
    ("UAV Vision Gimbal", "Gimbal", "Gimbal"),
    ("Silent Sentinel Sentry", "Sentry", "Sentry"),
    ("HGH Spynel", "Spynel", "Spynel"),
    ("SCD Pelican-D", "Pelican", "Pelican-D"),
    ("SCD Blackbird", "Blackbird", "Blackbird"),
    ("SCD Crane", "Crane", "Crane"),
    ("Lynred Detector", "Detector", "Detector"),
    ("Exosens Photonics", "Photonics", "Photonics"),
]


def test_seed_case_count_matches_payloads_seed_yaml() -> None:
    """62 identity rows per config/payloads_seed.yaml's own header comment (round 4b, W19)."""
    assert len(SEED_CASES) == 62


@pytest.mark.parametrize("canonical_name,expected_family,expected_variant", SEED_CASES)
def test_parse_family_variant_seed_names(
    canonical_name: str, expected_family: str, expected_variant: str
) -> None:
    family, variant = parse_family_variant(canonical_name)
    assert (family, variant) == (expected_family, expected_variant)


def test_parse_family_variant_groups_multiple_variants_under_one_family() -> None:
    """The whole point of the parser: several WESCAM MX-* names share one family so the tree can
    collapse them under a single expandable row."""
    families = {
        parse_family_variant(n)[0]
        for n in (
            "WESCAM MX-6",
            "WESCAM MX-15",
            "WESCAM MX-15D",
            "WESCAM MX-20HD",
            "WESCAM MX-25",
        )
    }
    assert families == {"MX"}


def test_parse_family_variant_keeps_a_real_word_suffix_whole() -> None:
    """ "T-Stamp" must not be mistaken for a hyphenated model-number split ("T" + "Stamp") --
    "Stamp" is a real word, not a short code, so the whole name stays one single-variant family."""
    assert parse_family_variant("Controp T-Stamp") == ("T-Stamp", "T-Stamp")


def test_parse_family_variant_empty_input() -> None:
    assert parse_family_variant("") == ("", "")
    assert parse_family_variant(None) == ("", "")  # type: ignore[arg-type]


def test_parse_family_variant_unknown_brand_keeps_whole_name_as_variant() -> None:
    """A vendor not in BRAND_PREFIXES (not yet on the curated list) still gets a deterministic,
    non-crashing result -- the whole name is its own variant/family rather than erroring."""
    family, variant = parse_family_variant("Acme Widget 9000")
    assert variant == "Acme Widget 9000"
    assert family == "Acme Widget"


# --------------------------------------------------------------------------
# canonical_vendor
# --------------------------------------------------------------------------


def test_canonical_vendor_resolves_watchlist_alias() -> None:
    # config/watchlist.yaml: name L3Harris, aliases include "WESCAM"/"L3Harris WESCAM".
    assert canonical_vendor("L3Harris WESCAM") == "L3Harris"


def test_canonical_vendor_merges_sibling_brands_under_one_corporate_parent() -> None:
    # config/watchlist.yaml: name RTX, aliases include Raytheon and "Collins Aerospace" -- real
    # corporate siblings that should land in the same vendor row of the tree.
    assert canonical_vendor("Raytheon") == "RTX"
    assert canonical_vendor("Collins Aerospace") == "RTX"


def test_canonical_vendor_passes_through_unknown_vendor() -> None:
    # Trakka Systems/UAV Vision/Silent Sentinel/HGH are not (yet) on the watchlist -- never guess.
    assert canonical_vendor("Trakka Systems") == "Trakka Systems"


def test_canonical_vendor_none_and_empty() -> None:
    assert canonical_vendor(None) is None
    assert canonical_vendor("") == ""


# --------------------------------------------------------------------------
# build_payload_tree
# --------------------------------------------------------------------------


def _row(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": 1,
        "canonical_name": "WESCAM MX-15",
        "vendor_entity_name": "L3Harris WESCAM",
        "family": "MX",
        "variant": "MX-15",
        "category": "gimbal",
        "image_url": None,
        "spec_url": "https://wescam.com/products/mx-series/",
        "spec_source": "wescam.com",
        "spec_version_count": 2,
        "price_ref_count": 1,
        "latest_spec_date": "2026-09-05",
        "latest_price_date": "2026-08-20",
    }
    base.update(over)
    return base


def test_build_payload_tree_groups_vendor_family_variant() -> None:
    rows = [
        _row(id=1, canonical_name="WESCAM MX-15", family="MX", variant="MX-15"),
        _row(
            id=2,
            canonical_name="WESCAM MX-20HD",
            family="MX",
            variant="MX-20HD",
            spec_version_count=1,
            price_ref_count=0,
        ),
        _row(
            id=3,
            canonical_name="Rafael Toplite",
            vendor_entity_name="Rafael Advanced Defense Systems",
            family="Toplite",
            variant="Toplite",
            category="pod",
            spec_version_count=0,
            price_ref_count=0,
            latest_spec_date=None,
            latest_price_date=None,
        ),
    ]
    tree = build_payload_tree(rows)
    assert tree["vendor_count"] == 2
    assert tree["family_count"] == 2
    assert tree["payload_count"] == 3

    by_vendor = {v["vendor"]: v for v in tree["vendors"]}
    assert set(by_vendor) == {"L3Harris", "Rafael"}

    wescam = by_vendor["L3Harris"]
    assert wescam["family_count"] == 1
    assert wescam["payload_count"] == 2
    mx_family = wescam["families"][0]
    assert mx_family["family"] == "MX"
    assert mx_family["variant_count"] == 2
    assert mx_family["spec_version_count"] == 3
    assert mx_family["price_ref_count"] == 1
    assert [v["canonical_name"] for v in mx_family["variants"]] == ["WESCAM MX-15", "WESCAM MX-20HD"]


def test_build_payload_tree_merges_corporate_siblings_via_canonical_vendor() -> None:
    rows = [
        _row(
            id=1,
            canonical_name="Raytheon MTS-A",
            vendor_entity_name="Raytheon",
            family="MTS",
            variant="MTS-A",
        ),
        _row(
            id=2,
            canonical_name="Collins Aerospace DB-110",
            vendor_entity_name="Collins Aerospace",
            family="DB",
            variant="DB-110",
        ),
    ]
    tree = build_payload_tree(rows)
    assert tree["vendor_count"] == 1
    rtx = tree["vendors"][0]
    assert rtx["vendor"] == "RTX"
    assert {f["family"] for f in rtx["families"]} == {"MTS", "DB"}


def test_build_payload_tree_empty() -> None:
    tree = build_payload_tree([])
    assert tree == {"vendors": [], "vendor_count": 0, "family_count": 0, "payload_count": 0}
