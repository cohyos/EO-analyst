"""Extraction post-check tests for the product dossier (PD-backend, user request 2026-09-08):
``eoa.dossier.extract.ground_dossier`` -- the deterministic, code-only checks that run AFTER the
LLM's structured-extraction call (never mocked here; ``ground_dossier`` takes an already-built
:class:`~eoa.llm.schemas.product_dossier.ProductDossierOut` draft, no network/DB/LLM involved).

Covers the three worked cases from the task brief: a number not in source -> field null; an
invented competitor -> dropped; a price without basis -> dropped.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_product_dossier_extract.py -q``
"""

from __future__ import annotations

from typing import Any

from eoa.dossier import extract as dossier_extract
from eoa.dossier.corpus import CorpusResult
from eoa.dossier.plan import PlanResult
from eoa.llm.schemas.analysis import Sentence
from eoa.llm.schemas.product_dossier import (
    CompetitorRow,
    IdentityBlock,
    PriceRow,
    ProductDossierOut,
    SpecRow,
)


def _corpus_with_registry(registry: list[dict[str, Any]], **items_kwargs: Any) -> CorpusResult:
    return CorpusResult(
        product_key="elbit-spectro-xr",
        product_name="SPECTRO XR",
        vendor="Elbit Systems",
        aliases=["Spectro"],
        terms=["SPECTRO XR", "Spectro"],
        registry=registry,
        **items_kwargs,
    )


_EMPTY_PLAN = PlanResult(findings=[])


def _reg(n: int, kind: str = "item", **extra: Any) -> dict[str, Any]:
    return {"n": n, "kind": kind, "id": n, "title": f"title-{n}", "url": None, "source_name": "src", **extra}


# --------------------------------------------------------------------------
# cites validity
# --------------------------------------------------------------------------


def test_out_of_range_cites_are_stripped_from_summary_sentence() -> None:
    corpus = _corpus_with_registry([_reg(1)])
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        summary_he=[Sentence(text_he="עובדה תקינה.", cites=[1, 99])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert len(result.dossier.summary_he) == 1
    assert result.dossier.summary_he[0].cites == [1]
    assert any(d.field == "summary_he" for d in result.dropped)


def test_sentence_with_only_bad_cites_is_dropped() -> None:
    corpus = _corpus_with_registry([_reg(1)])
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        risks_and_gaps_he=[Sentence(text_he="פער כלשהו.", cites=[42])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.risks_and_gaps_he == []


# --------------------------------------------------------------------------
# number not in source -> field null
# --------------------------------------------------------------------------


def test_spec_value_number_not_in_source_is_blanked() -> None:
    items = [{"id": 1, "title": "datasheet", "summary_he": 'המשקל הוא 20 ק"ג.', "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="משקל", value='99 ק"ג', cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.specifications[0].value == ""
    assert any(d.field == "specifications.value" for d in result.dropped)


def test_spec_value_number_grounded_is_kept() -> None:
    items = [{"id": 1, "title": "datasheet", "summary_he": 'המשקל הוא 20 ק"ג.', "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="משקל", value='20 ק"ג', cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.specifications[0].value == '20 ק"ג'
    assert result.dropped == []


# --------------------------------------------------------------------------
# invented competitor -> dropped
# --------------------------------------------------------------------------


def test_invented_competitor_is_dropped() -> None:
    items = [{"id": 1, "title": "article about Litening pod", "summary_he": "", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        competitors=[CompetitorRow(product="TotallyMadeUpPod9000", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.competitors == []
    assert any(d.field == "competitors" for d in result.dropped)


def test_grounded_competitor_is_kept() -> None:
    items = [{"id": 1, "title": "article about Litening pod", "summary_he": "", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        competitors=[CompetitorRow(product="Litening", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert len(result.dossier.competitors) == 1
    assert result.dossier.competitors[0].product == "Litening"


# --------------------------------------------------------------------------
# price without basis -> dropped
# --------------------------------------------------------------------------


def test_price_row_missing_basis_is_dropped() -> None:
    items = [{"id": 1, "title": "contract award", "summary_he": "$2 million contract.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        pricing=[PriceRow(figure="$2 million", source_kind="contract", basis_he="", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.pricing == []
    assert any(d.field == "pricing" and "basis" in d.reason for d in result.dropped)


def test_price_row_wrong_source_kind_is_dropped() -> None:
    items = [{"id": 1, "title": "blog rumor", "summary_he": "$2 million maybe.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        pricing=[PriceRow(figure="$2 million", source_kind="article", basis_he="ליחידה", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.pricing == []


def test_price_row_fully_grounded_is_kept() -> None:
    items = [
        {
            "id": 1,
            "title": "contract award",
            "summary_he": "מחיר לפי חוזה: $2 million ליחידה.",
            "so_what_he": "",
        }
    ]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        pricing=[PriceRow(figure="$2 million", source_kind="contract", basis_he="ליחידה", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert len(result.dossier.pricing) == 1


def test_price_row_number_not_grounded_is_dropped() -> None:
    items = [{"id": 1, "title": "contract award", "summary_he": "חוזה נחתם, ליחידה.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        pricing=[PriceRow(figure="$99 million", source_kind="contract", basis_he="ליחידה", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.pricing == []
