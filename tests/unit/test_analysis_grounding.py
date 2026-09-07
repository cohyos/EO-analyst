"""Unit tests for round-14 (2026-09-07): the analysis-stage grounding guard
(``eoa.pipeline.analysis_grounding``), added after item 39's fabricated "Ophir Optronics, subsidiary
of IAI" affiliation claim and invented "Planar Optics"/"Telescopeics" competitors reached
``weekly_2026-09-07.md``/``bd_il_2026-09-07.md``.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_analysis_grounding.py -q``
"""

from __future__ import annotations

from typing import ClassVar

from eoa.pipeline import analysis_grounding as ag

# --------------------------------------------------------------------------
# item-39 fixture (docs/qa/content_review/CR-analyze.md's own root-cause example) -- the item's
# real, edrmagazine.eu source text is entirely about the Ophir(R) SupIR-X lens and never mentions
# Israel Aerospace Industries at all.
# --------------------------------------------------------------------------

ITEM_39 = {
    "id": 39,
    "title": "The all-new 15-300 mm f/4 MWIR zoom engineered for 10 µm SXGA detectors",
    "url": (
        "https://www.edrmagazine.eu/the-all-new-15-300-mm-f-4-mwir-zoom-engineered-for-10-"
        "%c2%b5m-sxga-detectors"
    ),
    "entities_mentioned": ["Ophir Optronics"],
    "clean_text": (
        "The all-new 15-300 mm f/4 MWIR zoom engineered for 10 µm SXGA detectors\n"
        "The Ophir® SupIR-X 15-300 mm f/4 motorized continuous zoom lens is purpose-built to "
        "fully leverage the performance of 10 µm SXGA MWIR detectors for demanding long-range "
        "ISR and surveillance missions.\n"
        "Combining a 45° to 2.4° horizontal field of view, high-resolution imaging, and a "
        "compact ~1 kg design, SupIR-X delivers the clarity, stability, and reach required for "
        "advanced airborne, land, and maritime EO/IR systems.\n"
        "When greater range is needed, SupIR-X integrates seamlessly with the Ophir extender "
        "ecosystem, scaling up to 1200 mm focal length while maintaining a constant f/4 aperture, "
        "enabling vehicle target detection beyond 26 km under standard operational conditions.\n"
        "photo courtesy MKS"
    ),
}

ITEM_39_SUMMARY_HE = (
    "חברת Ophir Optronics, חברה בת של "
    "תעשייה אווירית (תע״א), "
    "השיקה עדשה חדשה למטע״דים "
    "אוויריים ויבשתיים: עדשת "
    "זום MWIR (Mid-Wave Infrared) דו-צירית 15-300 מ״מ f/4, "
    "המיועדת לגלאי 10 מיקרון SXGA. "
    "העדשה, Ophir® SupIR-X, מציעה שדה ראייה "
    "של 45°-2.4° אופקי, משקל של כ-1 ק״ג, "
    "וניתנת להרחבה עד 1200 מ״מ באמצעות "
    "מתאמי Ophir. היא מיועדת למשימות ISR, "
    "הגנה על גבולות, אבטחת חופים "
    "ומתקנים קריטיים."
)

ITEM_39_SO_WHAT_HE = (
    "להערכתנו, השקת העדשה "
    "החדשה Ophir® SupIR-X מעניקה יתרון "
    "תחרותי לתעשייה אווירית "
    "(תע״א) על פני מתחרותיה כמו "
    "פלנטריוניקס (Planar Optics) וטלסקופיקס "
    "(Telescopeics), במיוחד בתחום המטע״דים "
    "האוויריים והיבשתיים. "
    "העדשה, המיועדת לגלאי 10 "
    "מיקרון SXGA, מציעה פתרון קל משקל "
    "(כ-1 ק״ג) עם שדה ראייה רחב "
    "(45°-2.4° אופקי) ויכולת הרחבה עד "
    "1200 מ״מ, מה שמשפר באופן דרמטי "
    "את יכולות התצפית והזיהוי "
    "לטווח ארוך."
)

_SO_WHAT_PREFIX = "להערכתנו"  # "להערכתנו"


class TestItem39Fixture:
    """The exact fabrication that motivated this round: summary_he claimed Ophir Optronics is a
    subsidiary of "תעשייה אווירית" (IAI) -- false, and never mentioned
    in this item's own source text -- and so_what_he invented two non-existent competitors."""

    def setup_method(self) -> None:
        self.result = ag.ground_analysis_fields(
            ITEM_39,
            summary_he=ITEM_39_SUMMARY_HE,
            so_what_he=ITEM_39_SO_WHAT_HE,
            entities_mentioned=ITEM_39["entities_mentioned"],
        )

    def test_iai_affiliation_claim_removed_from_summary(self) -> None:
        assert "תע״א" not in self.result.summary_he
        assert "תעשייה אווירית" not in self.result.summary_he

    def test_summary_stays_coherent_and_keeps_real_facts(self) -> None:
        """Rule (a)/(b) strip at clause granularity for a well-formed affiliation clause -- the
        rest of the FACT-mode sentence (the real product spec) must survive, including a
        legitimate technical-term gloss ("MWIR (Mid-Wave Infrared)") that is not itself an
        organisation-name claim."""
        assert "Ophir Optronics" in self.result.summary_he
        assert "Mid-Wave Infrared" in self.result.summary_he
        assert "15-300" in self.result.summary_he
        assert "10 מיקרון SXGA" in self.result.summary_he

    def test_fabricated_competitors_removed_from_so_what(self) -> None:
        assert "Planar Optics" not in self.result.so_what_he
        assert "Telescopeics" not in self.result.so_what_he
        assert "פלנטריוניקס" not in self.result.so_what_he
        assert "טלסקופיקס" not in self.result.so_what_he

    def test_so_what_no_longer_asserts_iai(self) -> None:
        assert "תע״א" not in self.result.so_what_he

    def test_removals_are_logged_with_category_and_value(self) -> None:
        categories = {r["category"] for r in self.result.removed}
        assert "affiliation" in categories
        assert any("תע״א" in r["value"] or "IAI" in r["value"] for r in self.result.removed)

    def test_so_what_flagged_too_thin_for_repair_script_to_reanalyze(self) -> None:
        """Stripping the fabricated first sentence also removes the mandatory "להערכתנו"
        opening -- the repair script must treat this as "too thin" and re-run the analysis rather
        than persist a so_what_he missing its required prefix."""
        assert ag.is_too_thin(self.result.so_what_he, require_prefix=_SO_WHAT_PREFIX)

    def test_summary_not_too_thin(self) -> None:
        assert not ag.is_too_thin(self.result.summary_he)


# --------------------------------------------------------------------------
# regression: a grounded affiliation claim must survive
# --------------------------------------------------------------------------


class TestGroundedAffiliationSurvives:
    """A real affiliation, correctly attributed and (for this test) also actually present in the
    source text, must never be stripped -- both via the company_facts.yaml registry match and via
    plain source co-occurrence."""

    def test_elop_part_of_elbit_survives_via_registry(self) -> None:
        item = {
            "id": 999,
            "title": "Elbit Systems press release",
            "clean_text": "Elbit Systems today announced that Elop, part of Elbit Systems, delivered the first unit.",
        }
        summary_he = "חברת Elop, חלק מ-Elbit Systems, מסרה את היחידה הראשונה."
        result = ag.ground_analysis_fields(item, summary_he=summary_he)
        assert result.removed == []
        assert "Elop" in result.summary_he
        assert "Elbit Systems" in result.summary_he

    def test_elta_subsidiary_of_iai_survives(self) -> None:
        item = {
            "id": 1001,
            "title": "IAI ELTA radar test",
            "clean_text": "IAI's ELTA Systems successfully tested a new radar array this week.",
        }
        summary_he = "ELTA, חברה בת של IAI, בדקה מערך מכ״ם חדש."
        result = ag.ground_analysis_fields(item, summary_he=summary_he)
        assert result.removed == []
        assert "ELTA" in result.summary_he


# --------------------------------------------------------------------------
# rule (b): registry hard-rejects a contradicting claim even when the source text mentions both
# names -- co-occurrence alone must not be enough once a curated fact contradicts it.
# --------------------------------------------------------------------------


class TestRegistryOverridesCoOccurrence:
    def test_ophir_iai_claim_rejected_even_when_both_names_co_occur_in_source(self) -> None:
        item = {
            "id": 1002,
            "title": "Ophir Optronics and IAI at the same trade show",
            "clean_text": (
                "Ophir Optronics and Israel Aerospace Industries (IAI) both exhibited at the same "
                "trade show this week, showcasing unrelated products."
            ),
        }
        summary_he = (
            "חברת Ophir Optronics, חברה בת של "
            "תעשייה אווירית (IAI), "
            "הציגה מוצר בתערוכה."
        )
        result = ag.ground_analysis_fields(item, summary_he=summary_he)
        assert "IAI" not in result.summary_he
        assert any(r["category"] == "affiliation" for r in result.removed)


# --------------------------------------------------------------------------
# rule (a): a fabricated organisation name in key_facts / entities_mentioned is dropped; a real,
# source-mentioned one is kept.
# --------------------------------------------------------------------------


class TestEntityGroundingKeyFactsAndEntities:
    ITEM: ClassVar[dict] = {
        "id": 1003,
        "title": "Thales delivers new sensor",
        "clean_text": "Thales Group delivered a new electro-optical sensor to a European customer this month.",
    }

    def test_ungrounded_key_fact_dropped(self) -> None:
        facts = [
            "Thales Group מסרה חיישן חדש ללקוח אירופי.",
            "החיישן פותח בידי Zorblatt Systems.",
        ]
        result = ag.ground_analysis_fields(self.ITEM, key_facts=facts)
        assert "Thales Group מסרה חיישן חדש ללקוח אירופי." in result.key_facts
        assert not any("Zorblatt" in f for f in result.key_facts)

    def test_ungrounded_entities_mentioned_dropped_grounded_kept(self) -> None:
        result = ag.ground_analysis_fields(self.ITEM, entities_mentioned=["Thales", "Zorblatt Systems"])
        assert "Thales" in result.entities_mentioned
        assert "Zorblatt Systems" not in result.entities_mentioned


# --------------------------------------------------------------------------
# rule (c): competitor list -- an ungrounded name is dropped, a grounded one survives, and the
# clause is only dropped whole when *no* listed name grounds.
# --------------------------------------------------------------------------


class TestCompetitorListStripping:
    def test_partial_list_keeps_grounded_name_drops_fabricated_one(self) -> None:
        item = {
            "id": 1004,
            "title": "Rafael and Anduril at the same conference",
            "clean_text": "Rafael Advanced Defense Systems and Anduril Industries both presented at the conference.",
        }
        so_what_he = (
            "להערכתנו, המערכת מעניקה "
            "יתרון לרפאל על פני מתחרים "
            "כמו Anduril ו-Zorblatt Systems."
        )
        result = ag.ground_analysis_fields(item, so_what_he=so_what_he)
        assert "Anduril" in result.so_what_he
        assert "Zorblatt" not in result.so_what_he
        removed_competitors = [r["value"] for r in result.removed if r["category"] == "competitor"]
        assert any("Zorblatt" in v for v in removed_competitors)

    def test_whole_list_dropped_when_no_name_grounds(self) -> None:
        item = {
            "id": 1005,
            "title": "Rafael unveils new laser system",
            "clean_text": "Rafael Advanced Defense Systems unveiled a new laser interception system today.",
        }
        so_what_he = (
            "להערכתנו, המערכת מעניקה "
            "יתרון לרפאל על פני מתחרים "
            "כמו Zorblatt Systems ו-Blorpalo Inc."
        )
        result = ag.ground_analysis_fields(item, so_what_he=so_what_he)
        assert "Zorblatt" not in result.so_what_he
        assert "Blorpalo" not in result.so_what_he


# --------------------------------------------------------------------------
# rule (d): numbers/units in summary_he -- an ungrounded money figure/year is stripped, a grounded
# spec number survives.
# --------------------------------------------------------------------------


class TestNumberGrounding:
    def test_ungrounded_money_figure_sentence_stripped(self) -> None:
        item = {
            "id": 1006,
            "title": "Contract award",
            "clean_text": "The company won a new contract this quarter to supply sensors.",
        }
        summary_he = (
            "החברה זכתה בחוזה חדשה לאספקת חיישנים. "
            "ערך העסקה מוערך ב-450 מיליון דולר."
        )
        result = ag.ground_analysis_fields(item, summary_he=summary_he)
        assert "450" not in result.summary_he
        assert any(r["category"] == "number" for r in result.removed)
        assert "זכתה בחוזה חדשה" in result.summary_he

    def test_grounded_money_figure_survives(self) -> None:
        item = {
            "id": 1007,
            "title": "Contract award",
            "clean_text": "The company won a new $450 million contract this quarter to supply sensors.",
        }
        summary_he = (
            "החברה זכתה בחוזה חדשה לאספקת חיישנים. "
            "ערך העסקה מוערך ב-450 מיליון דולר."
        )
        result = ag.ground_analysis_fields(item, summary_he=summary_he)
        assert "450" in result.summary_he
        assert result.removed == []


# --------------------------------------------------------------------------
# is_too_thin
# --------------------------------------------------------------------------


class TestIsTooThin:
    def test_empty_is_too_thin(self) -> None:
        assert ag.is_too_thin("")

    def test_short_text_is_too_thin(self) -> None:
        assert ag.is_too_thin("עדשה.")

    def test_substantial_text_not_too_thin(self) -> None:
        assert not ag.is_too_thin("זהו משפט ארוך מספיק מספיק מספיק.")

    def test_missing_required_prefix_is_too_thin(self) -> None:
        assert ag.is_too_thin("משפט רגיל ללא קידומת חובה.", require_prefix=_SO_WHAT_PREFIX)

    def test_present_required_prefix_not_too_thin(self) -> None:
        text = f"{_SO_WHAT_PREFIX}, זה משפט עם הקידומת הנדרשת."
        assert not ag.is_too_thin(text, require_prefix=_SO_WHAT_PREFIX)
