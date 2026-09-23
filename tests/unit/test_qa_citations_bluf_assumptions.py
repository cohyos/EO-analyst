"""F18 (SOL-AUDIT-2026-09-24, docs/qa/content_review/SOL-AUDIT-2026-09-24.md): QA could pass a
structured report draft with a dangling citation in BLUF or in the "הנחות והפרכות" (assumptions/
falsifier) section -- `_check_structured` walked `sections`/`trends`/`exec_summary`/`outlook`, but
never `draft.bluf`/`draft.assumptions`, both of which `eoa.report.docx_builder` renders natively
with their own `[n]` markers.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_qa_citations_bluf_assumptions.py -q``
"""

from __future__ import annotations

from eoa.llm.schemas.analysis import AssumptionFalsifier, DailyReportDraft, Sentence
from eoa.report.qa_citations import check

_ITEMS = [{"id": 1, "n": 1}, {"id": 2, "n": 2}]


def test_bluf_with_valid_citation_passes():
    draft = DailyReportDraft(
        bluf=[Sentence(text_he="רפאל זכתה בחוזה חדש.", cites=[1])],
        exec_summary=[],
        sections=[],
        open_points_he=[],
    )
    result = check(draft, _ITEMS)
    assert result.passed, result.errors


def test_bluf_with_unknown_reference_fails_qa():
    draft = DailyReportDraft(
        bluf=[Sentence(text_he="רפאל זכתה בחוזה חדש.", cites=[999])],
        exec_summary=[],
        sections=[],
        open_points_he=[],
    )
    result = check(draft, _ITEMS)
    assert not result.passed
    assert 999 in result.bad_refs
    assert any("BLUF" in e for e in result.errors)


def test_assumptions_with_valid_citation_passes():
    draft = DailyReportDraft(
        exec_summary=[],
        sections=[],
        assumptions=[
            AssumptionFalsifier(
                assumption_he="קצב הרכש הנוכחי נמשך.", falsifier_he="ירידה חדה ברכש.", cites=[2]
            )
        ],
        open_points_he=[],
    )
    result = check(draft, _ITEMS)
    assert result.passed, result.errors


def test_assumptions_with_unknown_reference_fails_qa():
    draft = DailyReportDraft(
        exec_summary=[],
        sections=[],
        assumptions=[
            AssumptionFalsifier(
                assumption_he="קצב הרכש הנוכחי נמשך.", falsifier_he="ירידה חדה ברכש.", cites=[999]
            )
        ],
        open_points_he=[],
    )
    result = check(draft, _ITEMS)
    assert not result.passed
    assert 999 in result.bad_refs
    assert any("הנחות והפרכות" in e for e in result.errors)


def test_assumptions_with_empty_cites_is_allowed():
    """An assumption is often a structural premise, not itself a citable claim -- empty `cites` is
    valid by schema and must not fail QA."""
    draft = DailyReportDraft(
        exec_summary=[],
        sections=[],
        assumptions=[
            AssumptionFalsifier(assumption_he="קצב הרכש הנוכחי נמשך.", falsifier_he="ירידה חדה ברכש.")
        ],
        open_points_he=[],
    )
    result = check(draft, _ITEMS)
    assert result.passed, result.errors


def test_empty_bluf_and_assumptions_pass_unaffected():
    draft = DailyReportDraft(exec_summary=[], sections=[], open_points_he=[])
    result = check(draft, _ITEMS)
    assert result.passed, result.errors
