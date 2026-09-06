"""Round 3 QA-loop repair (D8, patent survey) -- unit tests for the four findings fixed in
``eoa.patents.survey``/``eoa.qa.d8_patent_survey`` (docs/qa/loop/round_1_judge.md D8 section):

1. exclusivity/market-share overclaim guard, gated on assignee-data coverage.
2. relationship-map citation integrity (both parties must be named in the cited source).
3. timeline/CPC "genuinely absent data" disclosure + the D8 scorer's stronger presence check
   (covered in tests/unit/test_qa_score.py::TestD8 -- not duplicated here).
4. LLM-synthesis retry-then-"narrative pending" marker + the nightly regeneration hook.

Mirrors tests/unit/test_patents_survey.py's own "stub every DB/network call" convention -- no live
DB/Ollama/HTTP calls anywhere in this file.
"""

from __future__ import annotations

from unittest.mock import patch

from eoa.llm.schemas.patents import AssigneeProfile, PatentBizAction, PatentCiteSentence, PatentSurveyDraft
from eoa.patents.survey import (
    NARRATIVE_PENDING_MARKER_HE,
    _assignee_coverage,
    _coverage_caveat_he,
    _enforce_coverage_caveat,
    _fallback_draft,
    _name_in_text,
    _RenderSection,
    _run_synthesis_with_retries,
    _scrub_exclusivity_claims,
    _verify_relationship_edges,
    find_surveys_pending_narrative,
    regenerate_pending_narrative,
)


def _sentence(text: str, cites: list[int] | None = None) -> PatentCiteSentence:
    return PatentCiteSentence(text_he=text, cites=cites if cites is not None else [1])


def _min_draft(**overrides) -> PatentSurveyDraft:
    from eoa.llm.schemas.patents import ClusterNarrative

    base = dict(
        exec_summary=[_sentence("משפט לדוגמה.")],
        landscape=[_sentence("משפט לדוגמה.")],
        tech_clusters=[ClusterNarrative(cluster_label_he="אשכול א", paragraph=[_sentence("משפט לדוגמה.")])],
        assignee_profiles=[
            AssigneeProfile(
                assignee_name="Anduril",
                tech_product_chain=[_sentence("משפט לדוגמה.")],
                implications_he=[_sentence("משפט לדוגמה.")],
            )
        ],
        business_implications=[
            PatentBizAction(action_he=f"פעולה {i}.", rationale_he="נימוק.", rationale_cites=[1])
            for i in range(3)
        ],
        timeline_narrative=[_sentence("משפט לדוגמה.")],
    )
    base.update(overrides)
    return PatentSurveyDraft(**base)


# --------------------------------------------------------------------------
# finding 1: exclusivity/market-share overclaim guard
# --------------------------------------------------------------------------


class TestAssigneeCoverage:
    def test_one_of_six_coverage(self):
        rows = [{"assignees": ["Anduril"]}] + [{"assignees": []} for _ in range(5)]
        missing, total, coverage = _assignee_coverage(rows)
        assert (missing, total) == (5, 6)
        assert coverage == 1 / 6

    def test_six_of_six_coverage(self):
        rows = [{"assignees": ["Anduril"]} for _ in range(6)]
        missing, total, coverage = _assignee_coverage(rows)
        assert (missing, total) == (0, 6)
        assert coverage == 1.0

    def test_empty_sample_is_vacuously_full_coverage(self):
        assert _assignee_coverage([]) == (0, 0, 1.0)

    def test_non_company_assignee_does_not_count_as_coverage(self):
        # "Europe" resolves to a non-company curated record -- must not count as a real assignee.
        rows = [{"assignees": ["Europe"]}, {"assignees": ["Anduril"]}]
        missing, total, coverage = _assignee_coverage(rows)
        assert (missing, total) == (1, 2)
        assert coverage == 0.5


class TestCoverageCaveat:
    def test_caveat_text_names_missing_and_total(self):
        text = _coverage_caveat_he(5, 6)
        assert "5" in text and "6" in text
        assert "בלעדיות" in text or "נתח שוק" in text


class TestScrubExclusivityClaims:
    def test_low_coverage_replaces_exclusivity_sentence_in_exec_summary(self):
        draft = _min_draft(exec_summary=[_sentence("אנדוריל היא השחקן הבלעדי בתחום זה.")])
        notes = _scrub_exclusivity_claims(draft, coverage=1 / 6, missing=5, total=6)
        assert len(notes) == 1
        assert draft.exec_summary[0].text_he == _coverage_caveat_he(5, 6)

    def test_low_coverage_replaces_exclusivity_in_business_action(self):
        draft = _min_draft(
            business_implications=[
                PatentBizAction(
                    action_he="לפעול מול השחקן היחיד בשוק.",
                    rationale_he="החברה שולטת בשוק לחלוטין.",
                    rationale_cites=[1],
                ),
                PatentBizAction(action_he="פעולה 2.", rationale_he="נימוק.", rationale_cites=[1]),
                PatentBizAction(action_he="פעולה 3.", rationale_he="נימוק.", rationale_cites=[1]),
            ]
        )
        notes = _scrub_exclusivity_claims(draft, coverage=1 / 6, missing=5, total=6)
        assert len(notes) == 2  # both action_he and rationale_he matched
        assert draft.business_implications[0].action_he == _coverage_caveat_he(5, 6)
        assert draft.business_implications[0].rationale_he == _coverage_caveat_he(5, 6)

    def test_english_exclusive_keyword_also_caught(self):
        draft = _min_draft(landscape=[_sentence("Anduril is the exclusive player in this market.")])
        notes = _scrub_exclusivity_claims(draft, coverage=1 / 6, missing=5, total=6)
        assert len(notes) == 1
        assert draft.landscape[0].text_he == _coverage_caveat_he(5, 6)

    def test_full_coverage_never_scrubs_even_a_real_exclusivity_claim(self):
        """At 6/6 coverage the guard does not fire at all -- an exclusivity claim is only
        *unsupported* when assignee data is actually sparse; this function never second-guesses a
        claim backed by full coverage (that judgment belongs to the LLM/human, not this gate)."""
        draft = _min_draft(exec_summary=[_sentence("אנדוריל היא השחקן הבלעדי בתחום זה.")])
        notes = _scrub_exclusivity_claims(draft, coverage=1.0, missing=0, total=6)
        assert notes == []
        assert "בלעדי" in draft.exec_summary[0].text_he

    def test_no_exclusivity_language_no_change(self):
        draft = _min_draft(exec_summary=[_sentence("אנדוריל פעילה בתחום זה.")])
        notes = _scrub_exclusivity_claims(draft, coverage=1 / 6, missing=5, total=6)
        assert notes == []


class TestEnforceCoverageCaveat:
    def test_appends_caveat_to_exec_summary_and_every_profile_section(self):
        draft = _fallback_draft([])
        draft.sections.append(_RenderSection(title_he="פרופיל מקצה: Anduril", sentences=[]))
        draft.sections.append(_RenderSection(title_he="פרופיל מקצה: Elbit", sentences=[]))
        draft.sections.append(_RenderSection(title_he="נוף הפטנטים", sentences=[]))  # not a profile
        _enforce_coverage_caveat(draft, 5, 6)
        caveat = _coverage_caveat_he(5, 6)
        assert any(s.text_he == caveat for s in draft.exec_summary)
        profile_sections = [s for s in draft.sections if s.title_he.startswith("פרופיל מקצה:")]
        assert len(profile_sections) == 2
        for section in profile_sections:
            assert any(s.text_he == caveat for s in section.sentences)
        non_profile = next(s for s in draft.sections if s.title_he == "נוף הפטנטים")
        assert non_profile.sentences == []

    def test_idempotent_does_not_duplicate_caveat(self):
        draft = _fallback_draft([])
        _enforce_coverage_caveat(draft, 5, 6)
        _enforce_coverage_caveat(draft, 5, 6)
        caveat = _coverage_caveat_he(5, 6)
        assert sum(1 for s in draft.exec_summary if s.text_he == caveat) == 1


# --------------------------------------------------------------------------
# finding 2: relationship-map citation integrity
# --------------------------------------------------------------------------


class TestNameInText:
    def test_direct_name_match(self):
        assert _name_in_text("Anduril", "Anduril wins a new contract") is True

    def test_no_match(self):
        assert _name_in_text("Elbit Systems", "Anduril to appoint someone to head Israel activity") is False

    def test_empty_text_never_matches(self):
        assert _name_in_text("Anduril", "") is False

    def test_watchlist_alias_match(self):
        # "Anduril" itself resolves via the watchlist; a canonical alias should also match if it's
        # in the text even when the exact profile name string isn't.
        canonical = {"name": "Anduril", "aliases": ["Anduril Industries"]}
        with patch("eoa.patents.survey.resolve_canonical", return_value=canonical):
            assert _name_in_text("Anduril", "Anduril Industries announced a new product.") is True


class TestVerifyRelationshipEdges:
    def test_edge_kept_when_source_names_both_parties(self):
        edges = [{"from": "Anduril", "to": "US Army", "kind": "contract_award", "n": 8}]
        source_text = {8: "Anduril wins $65m US Army contract to build TITAN hardware"}
        kept, dropped = _verify_relationship_edges(edges, source_text)
        assert kept == edges
        assert dropped == []

    def test_edge_dropped_when_source_omits_one_party(self):
        """The exact round-1 regression: 'Anduril <-> Elbit Systems | מיזוג/רכישה | Sigma 155
        howitzer system' whose only cited source (a personnel-appointment article) never mentions
        Elbit or the howitzer program at all."""
        edges = [
            {
                "from": "Anduril",
                "to": "Elbit Systems",
                "kind": "m_and_a",
                "n": 14,
                "program": "Sigma 155 howitzer system",
            }
        ]
        source_text = {14: "Anduril to appoint Amiram Norkin to head Israel activity"}
        kept, dropped = _verify_relationship_edges(edges, source_text)
        assert kept == []
        assert len(dropped) == 1
        assert "Elbit Systems" in dropped[0]
        assert "[14]" in dropped[0]

    def test_edge_with_no_citation_number_is_dropped(self):
        edges = [{"from": "Anduril", "to": "Palantir", "kind": "contract_award", "n": None}]
        kept, dropped = _verify_relationship_edges(edges, {})
        assert kept == []
        assert len(dropped) == 1
        assert "ללא ציטוט" in dropped[0]

    def test_multiple_edges_partial_drop(self):
        edges = [
            {"from": "Anduril", "to": "US Army", "kind": "contract_award", "n": 8},
            {"from": "Anduril", "to": "Elbit Systems", "kind": "m_and_a", "n": 14},
        ]
        source_text = {
            8: "Anduril wins $65m US Army contract",
            14: "Anduril to appoint Amiram Norkin to head Israel activity",
        }
        kept, dropped = _verify_relationship_edges(edges, source_text)
        assert len(kept) == 1
        assert kept[0]["to"] == "US Army"
        assert len(dropped) == 1


# --------------------------------------------------------------------------
# finding 4: LLM-synthesis retry, narrative-pending marker, regeneration hook
# --------------------------------------------------------------------------


class TestRunSynthesisWithRetries:
    def test_succeeds_on_first_attempt_no_sleep(self):
        sleeps: list[float] = []
        with patch("eoa.patents.survey._run_synthesis", return_value="draft-object"):
            result = _run_synthesis_with_retries(
                "topic", [], "data", role="resident", interactive=False, sleep=sleeps.append
            )
        assert result == "draft-object"
        assert sleeps == []

    def test_succeeds_on_third_attempt_after_two_failures(self):
        sleeps: list[float] = []
        with patch("eoa.patents.survey._run_synthesis", side_effect=[None, None, "draft-object"]):
            result = _run_synthesis_with_retries(
                "topic",
                [],
                "data",
                role="resident",
                interactive=False,
                max_attempts=3,
                delay_s=1.0,
                sleep=sleeps.append,
            )
        assert result == "draft-object"
        assert sleeps == [1.0, 1.0]  # slept between attempts 1->2 and 2->3, never after the last

    def test_returns_none_after_exhausting_every_attempt(self):
        sleeps: list[float] = []
        with patch("eoa.patents.survey._run_synthesis", return_value=None) as mocked:
            result = _run_synthesis_with_retries(
                "topic",
                [],
                "data",
                role="resident",
                interactive=False,
                max_attempts=3,
                delay_s=0.5,
                sleep=sleeps.append,
            )
        assert result is None
        assert mocked.call_count == 3
        assert sleeps == [0.5, 0.5]


class TestFallbackDraftNarrativePendingMarker:
    def test_default_uses_no_llm_text(self):
        draft = _fallback_draft(["open point"])
        assert NARRATIVE_PENDING_MARKER_HE not in draft.exec_summary[0].text_he

    def test_explicit_narrative_pending_marker(self):
        draft = _fallback_draft(["open point"], exec_summary_he=NARRATIVE_PENDING_MARKER_HE)
        assert draft.exec_summary[0].text_he == NARRATIVE_PENDING_MARKER_HE


class _FakeCursor:
    def __init__(self, fetchone_result=None, fetchall_result=None):
        self.fetchone_result = fetchone_result
        self.fetchall_result = fetchall_result or []
        self.executed: list[tuple] = []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        return self

    def fetchone(self):
        return self.fetchone_result

    def fetchall(self):
        return self.fetchall_result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestFindSurveysPendingNarrative:
    def test_returns_rows_from_query(self, monkeypatch):
        expected = [{"report_id": 35, "survey_id": 12, "topic": "FPA עם פיקסל דיגיטלי (DROIC)"}]
        cur = _FakeCursor(fetchall_result=expected)
        monkeypatch.setattr("eoa.patents.survey.connection", lambda: _FakeConnection(cur))
        result = find_surveys_pending_narrative(limit=5)
        assert result == expected
        query, params = cur.executed[0]
        assert "narrative_pending" in query
        assert params == {"limit": 5}


class TestRegeneratePendingNarrative:
    def test_looks_up_topic_and_rebuilds(self, monkeypatch):
        cur = _FakeCursor(fetchone_result={"topic": "FPA עם פיקסל דיגיטלי (DROIC)"})
        monkeypatch.setattr("eoa.patents.survey.connection", lambda: _FakeConnection(cur))
        with patch("eoa.patents.survey.build_patent_survey", return_value="paths-object") as mocked:
            result = regenerate_pending_narrative(12, role="resident", interactive=False)
        assert result == "paths-object"
        mocked.assert_called_once_with("FPA עם פיקסל דיגיטלי (DROIC)", role="resident", interactive=False)

    def test_raises_when_survey_id_unknown(self, monkeypatch):
        cur = _FakeCursor(fetchone_result=None)
        monkeypatch.setattr("eoa.patents.survey.connection", lambda: _FakeConnection(cur))
        try:
            regenerate_pending_narrative(999)
            raised = False
        except ValueError:
            raised = True
        assert raised
