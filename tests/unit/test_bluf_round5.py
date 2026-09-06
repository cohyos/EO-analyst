"""Round 5 P3 (docs/PLAN_ROUND5_REPORTS.md P3, docs/REPORT_TEMPLATE_BENCHMARK.md sec 1 items 1/2/6,
sec 4 items 4/5/11; docs/qa/loop/round_3_judge.md D2): BLUF, likelihood/confidence separation,
assumptions/falsifiers, and the so_what template-phrase ban.

Pure string-processing/pydantic-model checks -- no DB, no Ollama, no network (CONVENTIONS.md rule
10), same convention as ``test_report_style_round4b.py``/``test_report_round3_d6.py``. BLUF/
likelihood-confidence/assumptions rendering itself is exercised end-to-end via the real
``eoa.report.docx_builder`` (P4, landed natively the same evening -- reads ``draft.bluf``/
``OutlookIndicator.likelihood``/``confidence_level``/``confidence_basis_he``/``draft.assumptions``
directly, see ``test_renderer_round5.py``) rather than re-implemented here; this file focuses on
what this package (P3) owns: the schema/validators, the deterministic fallback drafts' shape, and
the so_what template-phrase post-pass.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_bluf_round5.py -q``
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from eoa.llm.schemas.analysis import (
    MAX_BLUF_SENTENCES,
    MAX_BLUF_WORDS,
    AssumptionFalsifier,
    DailyReportDraft,
    OutlookIndicator,
    Sentence,
    StructuredSection,
)
from eoa.llm.schemas.reports import MonthlyReportDraft, WeeklyReportDraft
from eoa.report import daily, monthly, weekly
from eoa.report import docx_builder as db
from eoa.report.qa_citations import (
    SO_WHAT_TEMPLATE_PHRASES_HE,
    check,
    strip_so_what_phrases,
    strip_so_what_phrases_from_draft,
)

# =================================================================================================
# BLUF: schema-level constraints
# =================================================================================================


class TestBlufSchemaConstraints:
    def test_daily_bluf_defaults_to_empty(self) -> None:
        draft = DailyReportDraft()
        assert draft.bluf == []

    def test_bluf_accepts_up_to_max_sentences(self) -> None:
        sentences = [Sentence(text_he=f"משפט {i}", cites=[i]) for i in range(1, MAX_BLUF_SENTENCES + 1)]
        draft = DailyReportDraft(bluf=sentences)
        assert len(draft.bluf) == MAX_BLUF_SENTENCES

    def test_bluf_rejects_more_than_max_sentences(self) -> None:
        sentences = [Sentence(text_he=f"משפט {i}", cites=[i]) for i in range(1, MAX_BLUF_SENTENCES + 2)]
        with pytest.raises(ValidationError):
            DailyReportDraft(bluf=sentences)

    def test_bluf_rejects_more_than_max_words_total(self) -> None:
        long_sentence = Sentence(text_he=" ".join(["מילה"] * (MAX_BLUF_WORDS + 1)), cites=[1])
        with pytest.raises(ValidationError):
            DailyReportDraft(bluf=[long_sentence])

    def test_bluf_requires_non_empty_cites_like_every_sentence(self) -> None:
        with pytest.raises(ValidationError):
            Sentence(text_he="ללא ציטוט", cites=[])

    @pytest.mark.parametrize("draft_cls", [WeeklyReportDraft, MonthlyReportDraft])
    def test_weekly_and_monthly_share_the_same_bluf_constraints(self, draft_cls) -> None:
        with pytest.raises(ValidationError):
            draft_cls(bluf=[Sentence(text_he=" ".join(["מילה"] * (MAX_BLUF_WORDS + 1)), cites=[1])])
        # a valid, in-budget bluf loads fine on both structured drafts
        draft = draft_cls(bluf=[Sentence(text_he="דבר חשוב קרה השבוע", cites=[1])])
        assert len(draft.bluf) == 1


# =================================================================================================
# BLUF: rendering is eoa.report.docx_builder's job (P4, native) -- integration check that a
# model-authored bluf and this package's own zero-narrative fallback both flow through correctly.
# =================================================================================================


class TestBlufRendersThroughDocxBuilder:
    def test_model_authored_bluf_renders_before_the_executive_summary(self) -> None:
        draft = DailyReportDraft(
            bluf=[Sentence(text_he="יש להיערך מיידית", cites=[1])],
            exec_summary=[Sentence(text_he="תקציר.", cites=[1])],
            sections=[
                StructuredSection(
                    title_he="תחום",
                    domain="airborne_pods",
                    sentences=[Sentence(text_he="ניתוח.", cites=[1])],
                )
            ],
        )
        items = [{"id": 1, "n": 1, "title": "Item", "source_name": "src", "url": "https://x"}]
        md = db.render_markdown(draft, items, [])
        assert "## שורה תחתונה" in md
        assert md.index("שורה תחתונה") < md.index("תקציר מנהלים")
        assert "יש להיערך מיידית [1]" in md

    def test_daily_deterministic_fallback_synthesizes_a_labelled_bluf(self) -> None:
        """The whole point of leaving `bluf=[]` on the fallback draft (see
        `eoa.report.daily._deterministic_fallback_draft`'s own docstring): docx_builder's own
        zero-narrative synthesis kicks in and labels the result as system-built, which an
        explicitly-populated `bluf` would have suppressed."""
        items = [{"id": 1, "n": 1, "title": "Top item", "level": "red", "so_what_he": "חשוב."}]
        draft = daily._deterministic_fallback_draft(items, [], [])
        assert draft.bluf == []  # P3 leaves this to docx_builder's own fallback synthesis
        registry = [{"n": 1, "id": 1, "title": "Top item"}]
        md = db.render_markdown(draft, registry, [])
        assert "## שורה תחתונה" in md
        assert "ללא ניסוח מודל" in md.split("## שורה תחתונה")[1].split("## תקציר מנהלים")[0]

    def test_weekly_and_monthly_fallback_also_synthesize_a_bluf(self) -> None:
        items = [{"id": 1, "n": 1, "title": "Top item", "level": "red", "so_what_he": "חשוב."}]
        registry = [{"n": 1, "id": 1, "title": "Top item"}]
        for module in (weekly, monthly):
            draft = module._deterministic_fallback_draft(items, [], [])
            assert draft.bluf == []
            md = db.render_markdown(draft, registry, [])
            assert "## שורה תחתונה" in md

    def test_deterministic_fallback_draft_passes_its_own_citation_check(self) -> None:
        items = [{"id": 1, "n": 1, "title": "Top item", "level": "red", "so_what_he": "חשוב."}]
        draft = daily._deterministic_fallback_draft(items, [], [])
        qa = check(draft, [{"n": 1}])
        assert qa.passed, qa.errors


# =================================================================================================
# Likelihood vs confidence (ICD 203): schema-level validation
# =================================================================================================


class TestLikelihoodConfidenceSchema:
    def test_old_indicator_without_the_new_fields_still_loads(self) -> None:
        indicator = OutlookIndicator(text_he="X יקרה", cites=[1])
        assert indicator.likelihood is None
        assert indicator.confidence_level is None

    def test_confidence_level_requires_a_basis(self) -> None:
        with pytest.raises(ValidationError):
            OutlookIndicator(text_he="X יקרה", cites=[1], confidence_level="גבוה")

    def test_likelihood_alone_is_allowed_without_confidence(self) -> None:
        indicator = OutlookIndicator(text_he="X יקרה", cites=[1], likelihood="גבוהה")
        assert indicator.confidence_level is None

    def test_only_the_documented_literal_values_are_accepted(self) -> None:
        with pytest.raises(ValidationError):
            OutlookIndicator(text_he="X יקרה", cites=[1], likelihood="high")
        with pytest.raises(ValidationError):
            OutlookIndicator(
                text_he="X יקרה", cites=[1], confidence_level="high", confidence_basis_he="2 מקורות"
            )

    def test_full_construction_with_all_three_fields(self) -> None:
        indicator = OutlookIndicator(
            text_he="X יקרה",
            cites=[1],
            likelihood="גבוהה",
            confidence_level="בינוני",
            confidence_basis_he="2 מקורות עצמאיים",
        )
        assert indicator.likelihood == "גבוהה"
        assert indicator.confidence_level == "בינוני"
        assert indicator.confidence_basis_he == "2 מקורות עצמאיים"


# =================================================================================================
# Likelihood vs confidence: rendering is docx_builder's job (P4, native) -- confirm this package's
# Hebrew-literal field values actually reach the page as intended even though
# ``_format_likelihood``/``_format_confidence_level`` were written expecting a numeric ratio /
# English key (see docs/MODULES.md "Round 5 P3" for the full coordination note) -- both fall back
# to rendering the value verbatim, which is already exactly the desired Hebrew text.
# =================================================================================================


class TestLikelihoodConfidenceRendersThroughDocxBuilder:
    def test_hebrew_literal_values_render_correctly_via_the_existing_formatters(self) -> None:
        indicator = OutlookIndicator(
            text_he="X יקרה",
            cites=[1],
            likelihood="גבוהה",
            confidence_level="בינוני",
            confidence_basis_he="2 מקורות עצמאיים",
        )
        rendered = db._render_outlook_indicator(indicator)
        assert "סבירות: גבוהה" in rendered
        assert "ביטחון: בינוני (2 מקורות עצמאיים)" in rendered

    def test_two_indicators_stay_clause_separated_in_the_full_outlook_text(self) -> None:
        import re

        clause_split_re = re.compile(r"[.,;]")
        ind1 = OutlookIndicator(
            text_he="אירוע א",
            cites=[1],
            likelihood="נמוכה",
            confidence_level="נמוך",
            confidence_basis_he="מקור בודד",
        )
        ind2 = OutlookIndicator(
            text_he="אירוע ב",
            cites=[2],
            likelihood="גבוהה",
            confidence_level="גבוה",
            confidence_basis_he="שלושה מקורות",
        )
        draft = DailyReportDraft(outlook=[ind1, ind2])
        outlook_text = db._draft_outlook_text(draft)
        clauses = clause_split_re.split(outlook_text)
        assert not any("סבירות" in c and "ביטחון" in c for c in clauses)


# =================================================================================================
# Assumptions <-> falsifiers: schema
# =================================================================================================


class TestAssumptionsFalsifiersSchema:
    def test_schema_rejects_empty_assumption_or_falsifier_text(self) -> None:
        with pytest.raises(ValidationError):
            AssumptionFalsifier(assumption_he="", falsifier_he="X", cites=[])
        with pytest.raises(ValidationError):
            AssumptionFalsifier(assumption_he="X", falsifier_he="", cites=[])

    def test_cites_may_be_empty_a_structural_assumption_need_not_cite_an_item(self) -> None:
        a = AssumptionFalsifier(assumption_he="קצב הרכש נמשך", falsifier_he="ירידה בתקציב", cites=[])
        assert a.cites == []

    def test_daily_draft_assumptions_default_to_empty_and_are_optional(self) -> None:
        draft = DailyReportDraft()
        assert draft.assumptions == []

    @pytest.mark.parametrize("draft_cls", [WeeklyReportDraft, MonthlyReportDraft])
    def test_weekly_and_monthly_cap_assumptions_at_four(self, draft_cls) -> None:
        too_many = [
            AssumptionFalsifier(assumption_he=f"הנחה {i}", falsifier_he=f"הפרכה {i}", cites=[])
            for i in range(5)
        ]
        with pytest.raises(ValidationError):
            draft_cls(assumptions=too_many)


class TestAssumptionsRenderThroughDocxBuilder:
    def test_renders_after_outlook_as_a_falsifier_wording_bullet_list(self) -> None:
        draft = DailyReportDraft(
            outlook=[OutlookIndicator(text_he="להערכתנו המגמה תימשך", is_assessment=True)],
            assumptions=[
                AssumptionFalsifier(assumption_he="הנחה א", falsifier_he="הפרכה א", cites=[1]),
            ],
        )
        items = [{"id": 1, "n": 1, "title": "Item", "source_name": "src", "url": "https://x"}]
        md = db.render_markdown(draft, items, [])
        assert md.index("מבט קדימה") < md.index("הנחות והפרכות")
        assert "הנחה א" in md and "הפרכה א" in md and "[1]" in md.split("הנחות והפרכות")[1]

    def test_no_assumptions_section_when_absent(self) -> None:
        draft = DailyReportDraft(exec_summary=[Sentence(text_he="תקציר.", cites=[1])])
        items = [{"id": 1, "n": 1, "title": "Item"}]
        md = db.render_markdown(draft, items, [])
        assert "הנחות והפרכות" not in md


# =================================================================================================
# so_what template-phrase ban (docs/qa/loop/round_3_judge.md D2)
# =================================================================================================


class TestSoWhatTemplateBan:
    def test_the_tasks_named_phrases_are_in_the_banned_list(self) -> None:
        for phrase in ("מחזק את מעמדה", "מהווה צעד משמעותי", "מעיד על מגמה"):
            assert phrase in SO_WHAT_TEMPLATE_PHRASES_HE

    def test_strips_the_judges_own_example(self) -> None:
        """docs/qa/loop/round_3_judge.md D2's own evidence: "לחזק את מעמדה" recurring in a
        cloud-drafted weekly report's prose (Hensoldt/Elbit items)."""
        cleaned, removed = strip_so_what_phrases("העסקה עשויה לחזק את מעמדה של הנדסולד בשוק האירופי.")
        assert "לחזק את מעמדה" not in cleaned
        assert removed

    def test_no_op_when_nothing_matches(self) -> None:
        text = "החברה זכתה בחוזה בסך 100 מיליון דולר."
        cleaned, removed = strip_so_what_phrases(text)
        assert cleaned == text
        assert removed == []

    def test_strips_from_exec_summary_and_the_original_draft_is_not_mutated(self) -> None:
        draft = DailyReportDraft(
            exec_summary=[Sentence(text_he="הצעד מהווה צעד משמעותי לחברה", cites=[1])],
        )
        updated, count = strip_so_what_phrases_from_draft(draft, report_kind="daily")
        assert count == 1
        assert "מהווה צעד משמעותי" not in updated.exec_summary[0].text_he
        # pydantic model_copy semantics: the caller's own draft object is left untouched
        assert "מהווה צעד משמעותי" in draft.exec_summary[0].text_he

    def test_strips_from_section_sentences_too(self) -> None:
        section = StructuredSection(
            title_he="תחום",
            domain="airborne_pods",
            sentences=[Sentence(text_he="זה מעיד על מגמה ברורה בשוק", cites=[1])],
        )
        draft = DailyReportDraft(sections=[section])
        updated, count = strip_so_what_phrases_from_draft(draft, report_kind="daily")
        assert count == 1
        assert "מעיד על מגמה" not in updated.sections[0].sentences[0].text_he

    def test_zero_count_and_no_op_when_nothing_removed(self) -> None:
        draft = DailyReportDraft(exec_summary=[Sentence(text_he="עובדה נקייה לגמרי", cites=[1])])
        updated, count = strip_so_what_phrases_from_draft(draft, report_kind="daily")
        assert count == 0
        assert updated.exec_summary[0].text_he == "עובדה נקייה לגמרי"
