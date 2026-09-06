"""Tests for the pure-logic (no DB/network) helpers in eoa.patents.survey -- the DB-facing
functions (_fetchall, _assignee_market_items/_events, build_patent_survey's own orchestration) are
exercised only via integration/manual runs, mirroring tests/unit/test_patents_scan.py's own
"stub every DB/network call" convention for anything that would otherwise need a live connection.
"""

from __future__ import annotations

import datetime as dt

from eoa.llm.schemas.patents import AssigneeProfile, ClusterNarrative, PatentBizAction, PatentCiteSentence
from eoa.patents.survey import PatentSurveyDraft as _PatentSurveyDraftAlias  # re-exported name check
from eoa.patents.survey import (
    _build_draft_from_synthesis,
    _cluster_assignees,
    _domain_label,
    _extend_registry_with_db_records,
    _is_real_company_assignee,
    _pub_country,
    _scrub_consistency_violations,
    _select_profile_assignees,
    _territory_filter,
)


class TestPubCountryAndTerritoryFilter:
    def test_extracts_leading_country_code(self):
        assert _pub_country("US9197834B2") == "US"
        assert _pub_country("WO2013127450A1") == "WO"

    def test_returns_none_for_unrecognized_shape(self):
        assert _pub_country("") is None
        assert _pub_country(None) is None

    def test_territory_filter_none_returns_unchanged(self):
        rows = [{"pub_number": "US1"}, {"pub_number": "JP1"}]
        assert _territory_filter(rows, None) == rows

    def test_territory_filter_restricts_to_code(self):
        rows = [{"pub_number": "US1"}, {"pub_number": "JP1"}, {"pub_number": "US2"}]
        out = _territory_filter(rows, "us")
        assert [r["pub_number"] for r in out] == ["US1", "US2"]


class TestRealCompanyAssigneeFilter:
    def test_placeholder_dash_excluded(self):
        assert _is_real_company_assignee("—") is False
        assert _is_real_company_assignee("") is False

    def test_watchlist_company_included(self):
        assert _is_real_company_assignee("Anduril") is True

    def test_curated_org_or_country_excluded(self):
        # "Europe" resolves via the curated-org/country table with kind != "company" -- must never
        # be counted as a patent assignee (this was the observed 2026-09-06 bug in a stale row).
        assert _is_real_company_assignee("Europe") is False

    def test_unknown_name_defaults_to_real(self):
        """A real-world assignee not on any curated list at all (the common case for a
        non-watchlist company) must still count -- only a *resolved non-company* record excludes."""
        assert _is_real_company_assignee("Some Random Semiconductor Co") is True

    def test_cluster_assignees_excludes_non_company_and_counts_real(self):
        rows = [
            {"assignees": ["Anduril", "Europe"]},
            {"assignees": ["Anduril"]},
            {"assignees": []},
        ]
        counter = _cluster_assignees(rows)
        assert counter["Anduril"] == 2
        assert "Europe" not in counter

    def test_select_profile_assignees_respects_limit_and_order(self):
        rows = [{"assignees": ["A"]}, {"assignees": ["A"]}, {"assignees": ["B"]}]
        counter = _cluster_assignees(rows)
        names = _select_profile_assignees(counter, limit=1)
        assert names == ["A"]


class TestDomainLabel:
    def test_known_domain_returns_hebrew_english_label(self):
        label = _domain_label("c_uas")
        assert "כטב" in label or "C-UAS" in label

    def test_unknown_domain_falls_back_to_raw_id(self):
        assert _domain_label("not_a_real_domain") == "not_a_real_domain"

    def test_none_domain_returns_empty_string(self):
        assert _domain_label(None) == ""


class TestExtendRegistryWithDbRecords:
    def test_new_items_get_sequential_numbers_continuing_registry(self):
        registry = [{"n": 1, "id": 10, "kind": "patent"}, {"n": 2, "id": 11, "kind": "patent"}]
        entries = [{"id": 501, "title": "כתבה א", "published_at": dt.date(2026, 1, 1)}]
        _extend_registry_with_db_records(registry, entries, item_id_key="id")
        assert entries[0]["n"] == 3
        assert registry[-1]["kind"] == "db_item"
        assert registry[-1]["item_id"] == 501

    def test_event_reuses_market_items_registry_row_for_same_underlying_item(self):
        registry = [{"n": 1, "id": 10, "kind": "patent"}]
        market_items = [{"id": 501, "title": "כתבה א"}]
        events = [{"item_id": 501, "title": "חוזה"}]
        _extend_registry_with_db_records(registry, market_items, item_id_key="id")
        _extend_registry_with_db_records(registry, events, item_id_key="item_id")
        assert market_items[0]["n"] == events[0]["n"]
        assert len(registry) == 2  # no duplicate row for the shared underlying item

    def test_entries_without_item_id_get_no_number(self):
        registry: list[dict] = []
        entries = [{"title": "no id here"}]
        _extend_registry_with_db_records(registry, entries, item_id_key="id")
        assert entries[0]["n"] is None
        assert registry == []


def _sentence(cites: list[int] | None = None) -> PatentCiteSentence:
    return PatentCiteSentence(text_he="משפט לדוגמה.", cites=cites if cites is not None else [1])


def _min_survey_kwargs(**overrides) -> dict:
    """``tech_clusters``/``timeline_narrative`` are required (min_length=1) on
    ``PatentSurveyDraft`` -- this fills in a minimal valid value for both so tests that don't care
    about them can omit them, mirroring ``tests/unit/test_patents_schemas.py``'s own ``_draft()``
    helper."""
    base = dict(
        tech_clusters=[ClusterNarrative(cluster_label_he="אשכול א", paragraph=[_sentence()])],
        timeline_narrative=[_sentence()],
    )
    base.update(overrides)
    return base


class TestBuildDraftFromSynthesis:
    def test_assembles_sections_in_expected_order(self):
        synthesis = _PatentSurveyDraftAlias(
            exec_summary=[_sentence()],
            landscape=[_sentence()],
            tech_clusters=[ClusterNarrative(cluster_label_he="אשכול X", paragraph=[_sentence()])],
            assignee_profiles=[
                AssigneeProfile(
                    assignee_name="Anduril",
                    tech_product_chain=[_sentence()],
                    recent_activity=[_sentence()],
                    implications_he=[_sentence()],
                )
            ],
            relationships=[_sentence()],
            white_spaces=[_sentence()],
            israel_position=[_sentence()],
            business_implications=[
                PatentBizAction(action_he=f"פעולה {i}.", rationale_he="נימוק.", rationale_cites=[1])
                for i in range(3)
            ],
            timeline_narrative=[_sentence()],
            outlook=[_sentence()],
        )
        draft = _build_draft_from_synthesis(synthesis, open_points_extra=["נקודה פתוחה"])
        titles = [s.title_he for s in draft.sections]
        assert titles == [
            "נוף הפטנטים",
            "אשכול טכנולוגי: אשכול X",
            "פרופיל מקצה: Anduril",
            "יחסים עסקיים (מפת יחסים)",
            "חורים והזדמנויות (White Space)",
            "עמדת התעשייה הישראלית",
            "השלכות עסקיות והמלצות",
            "ציר זמן -- ניתוח",
        ]
        assert draft.open_points_he == ["נקודה פתוחה"]
        assert len(draft.exec_summary) == 1
        assert len(draft.outlook) == 1

    def test_optional_sections_omitted_when_empty(self):
        """``tech_clusters``/``timeline_narrative`` are required (min_length=1, see
        eoa.llm.schemas.patents.PatentSurveyDraft) so they always render; ``relationships``/
        ``white_spaces``/``israel_position`` stay genuinely optional (there may be no deterministic
        relationship/white-space/Israel data at all) and are omitted when empty."""
        synthesis = _PatentSurveyDraftAlias(
            exec_summary=[_sentence()],
            landscape=[_sentence()],
            tech_clusters=[ClusterNarrative(cluster_label_he="אשכול א", paragraph=[_sentence()])],
            assignee_profiles=[
                AssigneeProfile(
                    assignee_name="Anduril", tech_product_chain=[_sentence()], implications_he=[_sentence()]
                )
            ],
            business_implications=[
                PatentBizAction(action_he=f"פעולה {i}.", rationale_he="נימוק.", rationale_cites=[1])
                for i in range(3)
            ],
            timeline_narrative=[_sentence()],
        )
        draft = _build_draft_from_synthesis(synthesis, open_points_extra=[])
        titles = [s.title_he for s in draft.sections]
        assert "אשכול טכנולוגי: אשכול א" in titles
        assert "ציר זמן -- ניתוח" in titles
        assert "יחסים עסקיים (מפת יחסים)" not in titles
        assert "חורים והזדמנויות (White Space)" not in titles
        assert "עמדת התעשייה הישראלית" not in titles
        assert "השלכות עסקיות והמלצות" in titles

    def test_assignee_profile_section_has_no_activity_marker_when_empty(self):
        synthesis = _PatentSurveyDraftAlias(
            **_min_survey_kwargs(
                exec_summary=[_sentence()],
                landscape=[_sentence()],
                assignee_profiles=[
                    AssigneeProfile(
                        assignee_name="Anduril",
                        tech_product_chain=[_sentence()],
                        recent_activity=[],
                        implications_he=[_sentence()],
                    )
                ],
                business_implications=[
                    PatentBizAction(action_he=f"פעולה {i}.", rationale_he="נימוק.", rationale_cites=[1])
                    for i in range(3)
                ],
            )
        )
        draft = _build_draft_from_synthesis(synthesis, open_points_extra=[])
        profile_section = next(s for s in draft.sections if s.title_he == "פרופיל מקצה: Anduril")
        texts = [s.text_he for s in profile_section.sentences]
        assert "פעילות עדכנית:" not in texts
        assert "השלכות:" in texts

    def test_fabricated_cpc_code_stripped_when_assignee_has_no_real_cpc_data(self):
        """Goal (2026-09-06, observed live on report_id=31): the model fabricated a CPC code
        inside an otherwise validly-cited tech_product_chain sentence for an assignee whose real
        CPC-cluster data was empty. assignee_has_cpc maps that assignee to False (the default when
        omitted), so any CPC-code-shaped token or "(... CPC ...)" aside is stripped."""
        synthesis = _PatentSurveyDraftAlias(
            **_min_survey_kwargs(
                exec_summary=[_sentence()],
                landscape=[_sentence()],
                assignee_profiles=[
                    AssigneeProfile(
                        assignee_name="Anduril",
                        tech_product_chain=[
                            PatentCiteSentence(
                                text_he=(
                                    "פטנטי עיבוד-על-החיישן של Anduril (אשכול CPC: Y10S 7/00, Y10S 7/160) "
                                    "מזוהים עם מוצרי Lattice."
                                ),
                                cites=[1],
                            )
                        ],
                        implications_he=[_sentence()],
                    )
                ],
                business_implications=[
                    PatentBizAction(action_he=f"פעולה {i}.", rationale_he="נימוק.", rationale_cites=[1])
                    for i in range(3)
                ],
            )
        )
        draft = _build_draft_from_synthesis(
            synthesis, open_points_extra=[], assignee_has_cpc={"Anduril": False}
        )
        profile_section = next(s for s in draft.sections if s.title_he == "פרופיל מקצה: Anduril")
        chain_text = profile_section.sentences[0].text_he
        assert "CPC" not in chain_text
        assert "Y10S" not in chain_text
        assert "Lattice" in chain_text  # the rest of the sentence survives

    def test_real_cpc_code_preserved_when_assignee_has_cpc_data(self):
        synthesis = _PatentSurveyDraftAlias(
            **_min_survey_kwargs(
                exec_summary=[_sentence()],
                landscape=[_sentence()],
                assignee_profiles=[
                    AssigneeProfile(
                        assignee_name="Anduril",
                        tech_product_chain=[
                            PatentCiteSentence(
                                text_he="פטנטי Anduril משתייכים לאשכול G01J5 המתועד במאגר.", cites=[1]
                            )
                        ],
                        implications_he=[_sentence()],
                    )
                ],
                business_implications=[
                    PatentBizAction(action_he=f"פעולה {i}.", rationale_he="נימוק.", rationale_cites=[1])
                    for i in range(3)
                ],
            )
        )
        draft = _build_draft_from_synthesis(
            synthesis, open_points_extra=[], assignee_has_cpc={"Anduril": True}
        )
        profile_section = next(s for s in draft.sections if s.title_he == "פרופיל מקצה: Anduril")
        assert "G01J5" in profile_section.sentences[0].text_he


class TestScrubConsistencyViolations:
    """A14b point 6 (goal 2026-09-06): fix the P2-observed internal-consistency slip ("the summary
    said 'no Anduril patents' while patent 15 was Anduril") -- the deterministic per-assignee
    counts (survey.py's own registry, never the LLM's own claim) are the ground truth checked
    against here."""

    def test_replaces_false_negation_and_returns_violation_note(self):
        synthesis = _PatentSurveyDraftAlias(
            **_min_survey_kwargs(
                exec_summary=[PatentCiteSentence(text_he="אין פטנטים של Anduril בתחום זה.", cites=[1])],
                landscape=[_sentence()],
                assignee_profiles=[_profile("Anduril")],
                business_implications=[
                    PatentBizAction(action_he=f"פעולה {i}.", rationale_he="נימוק.", rationale_cites=[1])
                    for i in range(3)
                ],
            )
        )
        notes = _scrub_consistency_violations(synthesis, {"Anduril": 2})
        assert len(notes) == 1
        assert "Anduril" in notes[0]
        # the replacement quotes the false claim back for transparency, but the sentence itself
        # no longer *asserts* it as fact -- it now opens with the deterministic correction marker.
        assert synthesis.exec_summary[0].text_he.startswith("תוקן אוטומטית לפי נתוני הנספח")

    def test_no_change_when_no_violation(self):
        synthesis = _PatentSurveyDraftAlias(
            **_min_survey_kwargs(
                exec_summary=[_sentence()],
                landscape=[_sentence()],
                assignee_profiles=[_profile("Anduril")],
                business_implications=[
                    PatentBizAction(action_he=f"פעולה {i}.", rationale_he="נימוק.", rationale_cites=[1])
                    for i in range(3)
                ],
            )
        )
        original_text = synthesis.exec_summary[0].text_he
        notes = _scrub_consistency_violations(synthesis, {"Anduril": 2})
        assert notes == []
        assert synthesis.exec_summary[0].text_he == original_text

    def test_checks_business_action_rationale(self):
        synthesis = _PatentSurveyDraftAlias(
            **_min_survey_kwargs(
                exec_summary=[_sentence()],
                landscape=[_sentence()],
                assignee_profiles=[_profile("Anduril")],
                business_implications=[
                    PatentBizAction(
                        action_he="לפנות ללקוח.",
                        rationale_he="אין פטנטים של Anduril בתחום זה כרגע.",
                        rationale_cites=[1],
                    ),
                    PatentBizAction(action_he="פעולה 2.", rationale_he="נימוק.", rationale_cites=[1]),
                    PatentBizAction(action_he="פעולה 3.", rationale_he="נימוק.", rationale_cites=[1]),
                ],
            )
        )
        notes = _scrub_consistency_violations(synthesis, {"Anduril": 5})
        assert len(notes) == 1
        assert "תוקן אוטומטית" in synthesis.business_implications[0].rationale_he


def _profile(name: str = "Anduril") -> AssigneeProfile:
    return AssigneeProfile(
        assignee_name=name,
        tech_product_chain=[_sentence()],
        implications_he=[_sentence()],
    )
