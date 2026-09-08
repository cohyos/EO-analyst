"""Schema tests for the product dossier (PD-backend, user request 2026-09-08):
``eoa.llm.schemas.product_dossier.ProductDossierOut`` and its row models -- nulls for unknowns,
every fact carries ``cites``, empty ``cites`` is allowed (the post-checks in ``eoa.dossier.extract``
are what enforce non-empty ``cites`` on a *kept* fact, not the schema itself -- the schema must
first accept whatever the model wrote so the post-check pass can inspect and trim it).

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_product_dossier_schema.py -q``
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from eoa.llm.schemas.analysis import Sentence
from eoa.llm.schemas.product_dossier import (
    CompetitorRow,
    DealRow,
    IdentityBlock,
    PriceRow,
    ProductDossierOut,
    SpecRow,
)


def test_minimal_identity_only_dossier_validates() -> None:
    dossier = ProductDossierOut(identity=IdentityBlock(product_name="SPECTRO XR"))
    assert dossier.identity.product_name == "SPECTRO XR"
    assert dossier.identity.status_he == "לא ידוע"
    assert dossier.specifications == []
    assert dossier.pricing == []
    assert dossier.what_changed_he is None


def test_identity_defaults_are_nulls_not_guesses() -> None:
    identity = IdentityBlock(product_name="X")
    assert identity.vendor == ""
    assert identity.first_announced is None
    assert identity.cites == []


def test_spec_row_allows_empty_cites() -> None:
    row = SpecRow(parameter_he="טווח זיהוי", value='10 ק"מ')
    assert row.cites == []


def test_summary_he_max_six_sentences() -> None:
    sentences = [Sentence(text_he=f"משפט {i}.", cites=[1]) for i in range(6)]
    dossier = ProductDossierOut(identity=IdentityBlock(product_name="X"), summary_he=sentences)
    assert len(dossier.summary_he) == 6
    with pytest.raises(ValidationError):
        ProductDossierOut(
            identity=IdentityBlock(product_name="X"),
            summary_he=[Sentence(text_he=f"משפט {i}.", cites=[1]) for i in range(7)],
        )


def test_sentence_rejects_inline_citation_marker() -> None:
    with pytest.raises(ValidationError):
        Sentence(text_he="עובדה כלשהי [1].", cites=[1])


def test_sentence_requires_non_empty_cites() -> None:
    with pytest.raises(ValidationError):
        Sentence(text_he="עובדה כלשהי.", cites=[])


def test_deal_row_defaults() -> None:
    deal = DealRow(customer="US Air Force")
    assert deal.kind == "contract_award"
    assert deal.confidence == 0.5
    assert deal.amount == ""


def test_deal_row_pd_fix_fields_default() -> None:
    """PD-fix (2026-09-08, item 3): `amount_value`/`date_kind`/`region_he` are new, deterministically
    derived-post-extraction fields (never model-authored) -- default to null/"deal"/empty."""
    deal = DealRow(customer="US Air Force")
    assert deal.amount_value is None
    assert deal.date_kind == "deal"
    assert deal.region_he == ""


def test_deal_row_accepts_pd_fix_fields() -> None:
    deal = DealRow(
        customer="Some AF",
        amount="כ-80 מיליון דולר",
        amount_value=80_000_000.0,
        currency="USD",
        country="",
        region_he="מדינה באסיה-פסיפיק",
        date="2026-03-01",
        date_kind="published",
    )
    assert deal.amount_value == 80_000_000.0
    assert deal.region_he == "מדינה באסיה-פסיפיק"
    assert deal.date_kind == "published"
    with pytest.raises(ValidationError):
        DealRow(customer="x", date_kind="rumor")  # type: ignore[arg-type]


def test_price_row_requires_valid_source_kind_literal() -> None:
    row = PriceRow(figure="$1M", source_kind="contract", basis_he="לתוכנית כולה")
    assert row.source_kind == "contract"
    with pytest.raises(ValidationError):
        PriceRow(figure="$1M", source_kind="rumor")  # type: ignore[arg-type]


def test_competitor_row_minimal() -> None:
    row = CompetitorRow(product="Litening")
    assert row.vendor == ""
    assert row.cites == []


def test_full_dossier_round_trips_through_model_dump() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR", vendor="Elbit Systems", cites=[1]),
        summary_he=[Sentence(text_he='זהו מטע"ד EO/IR.', cites=[1, 2])],
        specifications=[SpecRow(parameter_he="משקל", value='25 ק"ג', cites=[1])],
        pricing=[PriceRow(figure="$2M", source_kind="tender", basis_he="ליחידה", cites=[3])],
    )
    dumped = dossier.model_dump()
    restored = ProductDossierOut.model_validate(dumped)
    assert restored.identity.vendor == "Elbit Systems"
    assert restored.specifications[0].value == '25 ק"ג'
    assert restored.pricing[0].source_kind == "tender"
