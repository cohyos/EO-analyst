"""Renderer + plan-topic tests for the product dossier (PD-backend, user request 2026-09-08):
``eoa.dossier.report``'s table builders (<= 6 columns, "לא נמצא במקורות" placeholders) and
``eoa.dossier.plan.build_topics`` (deterministic, no-network query construction).

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_product_dossier_report.py -q``
"""

from __future__ import annotations

from eoa.dossier import plan as dossier_plan
from eoa.dossier import report as dossier_report
from eoa.llm.schemas.product_dossier import (
    DealRow,
    IdentityBlock,
    PartnerRow,
    PerformanceRow,
    PriceRow,
    ProductDossierOut,
    SpecRow,
    VersionRow,
)

_ALL_TABLE_BUILDERS = [
    dossier_report._specifications_table,
    dossier_report._variants_table,
    dossier_report._performance_table,
    dossier_report._deals_table,
    dossier_report._pricing_table,
    dossier_report._partnerships_table,
    dossier_report._competitors_table,
    dossier_report._patents_table,
    dossier_report._tenders_table,
]


def test_empty_dossier_every_table_is_none() -> None:
    dossier = ProductDossierOut(identity=IdentityBlock(product_name="X"))
    for builder in _ALL_TABLE_BUILDERS:
        assert builder(dossier) is None


def _sample_dossier() -> ProductDossierOut:
    return ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR", vendor="Elbit Systems"),
        specifications=[SpecRow(parameter_he="משקל", value="", unit="", variant="", cites=[1])],
        variants_and_versions=[VersionRow(name="Block II", cites=[1])],
        performance=[PerformanceRow(metric_he="טווח", claimed_value='10 ק"מ', cites=[1])],
        deals=[DealRow(customer="US Air Force", cites=[1])],
        pricing=[PriceRow(figure="$2M", basis_he="ליחידה", source_kind="contract", cites=[1])],
        partnerships=[PartnerRow(partner="Partner Co", cites=[1])],
    )


def test_tables_stay_within_six_columns() -> None:
    dossier = _sample_dossier()
    for builder in _ALL_TABLE_BUILDERS:
        tbl = builder(dossier)
        if tbl is None:
            continue
        assert len(tbl["headers"]) <= 6, tbl["title_he"]
        for row in tbl["rows"]:
            assert len(row) == len(tbl["headers"])


def test_missing_value_renders_placeholder() -> None:
    dossier = _sample_dossier()
    tbl = dossier_report._specifications_table(dossier)
    assert tbl is not None
    assert dossier_report.PLACEHOLDER_HE in tbl["rows"][0]


def test_cite_cell_formats_numbers() -> None:
    assert dossier_report._cite_cell([1, 2]) == "[1], [2]"
    assert dossier_report._cite_cell([]) == "—"


def test_signal_count_and_outcome_not_found_for_empty_dossier() -> None:
    from eoa.dossier.plan import PlanResult

    dossier = ProductDossierOut(identity=IdentityBlock(product_name="X"))
    outcome, confidence = dossier_report._compute_outcome_confidence(dossier, PlanResult(findings=[]))
    assert outcome == "not_found"
    assert confidence <= 0.3


def test_outcome_found_when_many_signals_present() -> None:
    from eoa.dossier.plan import PlanResult
    from eoa.llm.schemas.analysis import Sentence

    dossier = _sample_dossier()
    dossier = dossier.model_copy(update={"summary_he": [Sentence(text_he="עובדה.", cites=[1])]})
    outcome, _confidence = dossier_report._compute_outcome_confidence(dossier, PlanResult(findings=[]))
    assert outcome in ("found", "partial")


# --------------------------------------------------------------------------
# plan.build_topics -- deterministic, no network
# --------------------------------------------------------------------------


def test_build_topics_names_product_in_every_question() -> None:
    topics = dossier_plan.build_topics("SPECTRO XR", "Elbit Systems", ["Spectro"])
    assert len(topics) == len(dossier_plan.TOPICS)
    for t in topics:
        assert "SPECTRO XR" in t["question_he"]
        assert "Elbit Systems" in t["question_he"]


def test_build_topics_respects_max_topics_cap() -> None:
    topics = dossier_plan.build_topics("SPECTRO XR", "Elbit Systems", [], max_topics=3)
    assert len(topics) == 3


def test_build_topics_no_vendor_still_valid() -> None:
    topics = dossier_plan.build_topics("MOSP 5000", None, [])
    assert all(t["question_he"] for t in topics)
