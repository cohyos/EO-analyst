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
    DealRow,
    DossierPatentRow,
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
    assert result.dossier.pricing[0].basis_he == dossier_extract.BASIS_UNIT_HE


def test_price_row_number_not_grounded_is_dropped() -> None:
    items = [{"id": 1, "title": "contract award", "summary_he": "חוזה נחתם, ליחידה.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        pricing=[PriceRow(figure="$99 million", source_kind="contract", basis_he="ליחידה", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.pricing == []


# --------------------------------------------------------------------------
# PD-fix-3 (2026-09-08, item 1): basis_he consistency -- a price row is kept only when its basis_he
# classifies deterministically into one of the three canonical forms; the SPECTRO XR case is the
# real live-dossier row that motivated this ("לחוזה כולל (לא צוין מחיר ליחידה)", not the prompt's
# own "לתוכנית כולה").
# --------------------------------------------------------------------------


def test_classify_price_basis_contract_total_spectro_xr_live_case() -> None:
    assert (
        dossier_extract._classify_price_basis("לחוזה כולל (לא צוין מחיר ליחידה)")
        == dossier_extract.BASIS_TOTAL_HE
    )


def test_classify_price_basis_per_unit() -> None:
    assert dossier_extract._classify_price_basis("ליחידה") == dossier_extract.BASIS_UNIT_HE
    assert dossier_extract._classify_price_basis("מחיר ליחידה") == dossier_extract.BASIS_UNIT_HE


def test_classify_price_basis_lot() -> None:
    assert dossier_extract._classify_price_basis("למנה של 5 יחידות") == "למנה של 5 יחידות"
    assert dossier_extract._classify_price_basis("lot of 12 units") == "למנה של 12 יחידות"


def test_classify_price_basis_unparseable_returns_none() -> None:
    assert dossier_extract._classify_price_basis("מחיר משוער") is None


def test_price_row_spectro_xr_contract_total_is_normalized_and_kept() -> None:
    items = [
        {
            "id": 1,
            "title": "contract award",
            "summary_he": "כ-80 מיליון דולר לחוזה כולל (לא צוין מחיר ליחידה).",
            "so_what_he": "",
        }
    ]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        pricing=[
            PriceRow(
                figure="כ-80 מיליון דולר",
                source_kind="contract",
                basis_he="לחוזה כולל (לא צוין מחיר ליחידה)",
                cites=[1],
            )
        ],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert len(result.dossier.pricing) == 1
    assert result.dossier.pricing[0].basis_he == dossier_extract.BASIS_TOTAL_HE
    assert result.dossier.pricing[0].figure == "כ-80 מיליון דולר"


def test_price_row_unparseable_basis_is_dropped() -> None:
    items = [{"id": 1, "title": "contract award", "summary_he": "$2 million contract.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        pricing=[PriceRow(figure="$2 million", source_kind="contract", basis_he="מחיר משוער", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.pricing == []
    assert any(d.field == "pricing" and "unparseable" in d.reason for d in result.dropped)


# --------------------------------------------------------------------------
# PD-fix (2026-09-08, item 3): deal amount/date/country deterministic post-processing
# --------------------------------------------------------------------------


def test_parse_amount_he_hebrew_scale_and_currency() -> None:
    assert dossier_extract.parse_amount_he("כ-80 מיליון דולר") == (80_000_000.0, "USD")


def test_parse_amount_he_english_scale_and_currency() -> None:
    assert dossier_extract.parse_amount_he("$2 million") == (2_000_000.0, "USD")


def test_parse_amount_he_no_digits_returns_none() -> None:
    assert dossier_extract.parse_amount_he("") == (None, None)
    assert dossier_extract.parse_amount_he("no numbers here") == (None, None)


def test_split_country_region_region_only() -> None:
    assert dossier_extract.split_country_region("Asia-Pacific country") == ("", "Asia-Pacific country")
    assert dossier_extract.split_country_region("מדינה באסיה-פסיפיק") == ("", "מדינה באסיה-פסיפיק")


def test_split_country_region_specific_country_is_kept() -> None:
    assert dossier_extract.split_country_region("India") == ("India", "")
    assert dossier_extract.split_country_region("") == ("", "")


def test_deal_row_amount_parsed_and_country_split_to_region() -> None:
    items = [
        {
            "id": 1,
            "title": "Elbit wins deal",
            "summary_he": "עסקה בהיקף כ-80 מיליון דולר, מדינה באסיה-פסיפיק.",
            "so_what_he": "",
        }
    ]
    corpus = _corpus_with_registry([_reg(1, published_at="2026-03-01")], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[
            DealRow(
                customer="Some AF",
                amount="כ-80 מיליון דולר",
                country="מדינה באסיה-פסיפיק",
                date=None,
                cites=[1],
            )
        ],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    deal = result.dossier.deals[0]
    assert deal.amount_value == 80_000_000.0
    assert deal.currency == "USD"
    assert deal.country == ""
    assert deal.region_he == "מדינה באסיה-פסיפיק"
    # No date on the deal itself -> backfilled from the cited item's own published_at.
    assert deal.date == "2026-03-01"
    assert deal.date_kind == "published"


def test_deal_row_with_own_date_keeps_date_kind_deal() -> None:
    items = [{"id": 1, "title": "contract", "summary_he": "נחתם חוזה ב-1.3.2026.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1, published_at="2026-05-01")], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="Some AF", date="2026-03-01", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    deal = result.dossier.deals[0]
    assert deal.date == "2026-03-01"
    assert deal.date_kind == "deal"


# --------------------------------------------------------------------------
# PD-fix-3 (2026-09-08, item 4): a placeholder "customer" string the model wrote literally (e.g.
# the live SPECTRO XR "—") is normalized to None -- never persisted/rendered as-is.
# --------------------------------------------------------------------------


def test_deal_customer_placeholder_dash_normalized_to_none() -> None:
    items = [{"id": 1, "title": "contract", "summary_he": "עסקה כלשהי.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="—", kind="contract_award", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.deals[0].customer is None


def test_deal_customer_placeholder_hebrew_text_normalized_to_none() -> None:
    items = [{"id": 1, "title": "contract", "summary_he": "עסקה כלשהי.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="לא ידוע", kind="contract_award", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.deals[0].customer is None


def test_deal_real_customer_name_is_kept_verbatim() -> None:
    items = [{"id": 1, "title": "contract", "summary_he": "עסקה עם US Air Force.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="US Air Force", kind="contract_award", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.deals[0].customer == "US Air Force"


# --------------------------------------------------------------------------
# PD-fix-3 (2026-09-08, item 1): patents -- relevance_he is entirely code-derived (never trusted as
# the model wrote it) and a row with no grounded applicant/product-name link is dropped outright.
# --------------------------------------------------------------------------


def test_patent_row_with_matching_assignee_gets_deterministic_relevance_he() -> None:
    corpus = _corpus_with_registry([_reg(1, kind="patent")])
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        patents=[
            DossierPatentRow(
                pub_number="US1234567B2",
                title="EO/IR payload apparatus",
                assignee="Elbit Systems",
                relevance_he="Some model-written text that may or may not be true.",
                cites=[1],
            )
        ],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert len(result.dossier.patents) == 1
    relevance = result.dossier.patents[0].relevance_he
    assert "Elbit Systems" in relevance
    assert "Some model-written text" not in relevance


def test_patent_row_admitting_no_confirmed_link_is_dropped() -> None:
    """Reproduces the live defect verbatim: the model's own relevance_he already says there is no
    confirmed link in the sources, and the row must not survive grounding regardless."""
    corpus = _corpus_with_registry([_reg(1, kind="patent")])
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        patents=[
            DossierPatentRow(
                pub_number="CN113804187A",
                title="target positioning pod apparatus",
                assignee="",
                relevance_he="אין אישור במקורות לקשר",
                cites=[1],
            )
        ],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.patents == []
    assert any(d.field == "patents" for d in result.dropped)


def test_patent_row_product_name_in_title_is_kept_without_assignee_match() -> None:
    corpus = _corpus_with_registry([_reg(1, kind="patent")])
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        patents=[
            DossierPatentRow(
                pub_number="US9999999A1",
                title="SPECTRO XR compact payload housing",
                assignee="Unrelated Assignee Inc",
                relevance_he="",
                cites=[1],
            )
        ],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert len(result.dossier.patents) == 1
    assert "SPECTRO XR" in result.dossier.patents[0].relevance_he
